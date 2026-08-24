from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from multi_agent_report_contract import (  # noqa: E402
    MultiAgentReportContractError,
    build_luna_investigation_report,
    build_luna_investigation_report_v2,
    build_sol_handoff_v2,
    build_terra_final_input,
    build_terra_final_report,
    sha256_value,
    validate_dual_report_plan,
    validate_sol_handoff_v2,
    validate_terra_final_report,
    validate_luna_investigation_report_v2,
)
from orchestration_plan import seal_read_plan  # noqa: E402
from read_bundle import build_read_bundle  # noqa: E402


def branch(index: int, *, required: bool = True, depends_on: list[str] | None = None) -> dict:
    return {
        "branch_id": f"branch-{index}",
        "purpose": f"investigate-{index}",
        "rationale": f"independent-{index}",
        "required": required,
        "depends_on": list(depends_on or []),
        "allowed_task_artifact_ids": ["artifact-dialogue"],
        "allowed_mcp_tools": ["get_task_context", "read_task_artifact"],
        "collection_scope": [f"scope-{index}"],
        "query_constraints": {"domain": index},
        "maximum_calls": 4,
        "maximum_records": 20,
        "maximum_bytes": 65536,
        "completion_requirements": ["report"],
        "failure_policy": "fail_closed",
        "expected_evidence_kinds": ["record"],
    }


def plan(count: int = 3, *, branches: list[dict] | None = None) -> dict:
    return seal_read_plan(
        {
            "schema_version": "orchestration_read_plan_v1",
            "plan_id": "PLAN-DUAL-1",
            "subject": "math",
            "capture_id": "CAP-DUAL-1",
            "frozen_task_sha256": "1" * 64,
            "release_id": "2" * 64,
            "activation_id": "3" * 64,
            "authority_snapshot_sha256": "4" * 64,
            "generation": "dual-report-fixture-v1",
            "orchestrate_skill": {
                "id": "multi-agent-read-orchestrate",
                "version": "1.0.0",
                "sha256": "5" * 64,
            },
            "terra_agent_contract_sha256": "6" * 64,
            "created_at": "2026-08-24T00:00:00+00:00",
            "branches": branches or [branch(index) for index in range(1, count + 1)],
            "formal_write_count": 0,
        }
    )


def branch_result(value: dict, index: int) -> dict:
    core = {
        "schema_version": "read_branch_result_v1",
        "plan_id": value["plan_id"],
        "plan_sha256": value["plan_sha256"],
        "request_sha256": str(index) * 64,
        "branch_id": f"branch-{index}",
        "subject": value["subject"],
        "capture_id": value["capture_id"],
        "frozen_task_sha256": value["frozen_task_sha256"],
        "release_id": value["release_id"],
        "activation_id": value["activation_id"],
        "authority_snapshot_sha256": value["authority_snapshot_sha256"],
        "generation": value["generation"],
        "status": "succeeded",
        "required": True,
        "purpose": f"investigate-{index}",
        "child_agent_id": f"luna-{index}",
        "parent_agent_id": "terra-parent",
        "read_session_id": f"session-{index}",
        "mcp_launcher_pid": 100 + index,
        "mcp_launcher_pgid": 100 + index,
        "wave_index": 1,
        "calls": [{"sequence": 1, "tool": "get_task_context", "status": "succeeded"}],
        "evidence": [
            {
                "evidence_ref": f"mcp-item:math:item-{index}",
                "source_sha256": "a" * 64,
            }
        ],
        "findings": [{"code": f"finding-{index}"}],
        "conflicts": [],
        "missing_evidence": [],
        "pagination_closed": True,
        "duration_ms": 1,
        "gate_decision": {},
        "formal_write_count": 0,
    }
    return {**core, "result_sha256": sha256_value(core)}


def bundle(value: dict) -> dict:
    results = [
        branch_result(value, index)
        for index in range(1, len(value["branches"]) + 1)
    ]
    return build_read_bundle(
        value,
        {
            "results": results,
            "logical_branch_count": len(results),
            "physical_slot_count": len(results),
            "maximum_active_branch_count": len(results),
            "wave_count": 1,
            "duration_ms": 1,
        },
    )


def legacy_handoff(value: dict) -> dict:
    core = {
        "schema_version": "sol_handoff_envelope_v1",
        "subject": value["subject"],
        "capture_id": value["capture_id"],
        "read_bundle_sha256": bundle(value)["read_bundle_sha256"],
        "candidate_sha256": "c" * 64,
        "review_sha256": "d" * 64,
        "risk_report_sha256": "e" * 64,
        "sol_review_ready": True,
        "diagnostic_review_ready": True,
        "quality_clean": True,
        "allowed_sol_actions": ["adopt", "defer", "modify", "reject", "request_reread"],
        "formal_apply_authorized": False,
        "formal_write_count": 0,
    }
    return {**core, "handoff_sha256": sha256_value(core)}


class MultiAgentReportContractTests(unittest.TestCase):
    def fixture(self, count: int = 3) -> tuple[dict, dict, list[dict], list[dict]]:
        value = plan(count)
        read_bundle = bundle(value)
        results = [branch_result(value, index) for index in range(1, count + 1)]
        reports = [
            build_luna_investigation_report(
                plan=value,
                read_bundle=read_bundle,
                branch_result=result,
                summary=f"Luna investigation {index}",
                confidence="high",
            )
            for index, result in enumerate(results, start=1)
        ]
        return value, read_bundle, results, reports

    def test_accepts_exactly_three_or_four_required_independent_branches(self) -> None:
        self.assertEqual(len(validate_dual_report_plan(plan(3))["branches"]), 3)
        self.assertEqual(len(validate_dual_report_plan(plan(4))["branches"]), 4)
        for count in (2, 5):
            with self.subTest(count=count), self.assertRaises(
                MultiAgentReportContractError
            ):
                validate_dual_report_plan(plan(count))
        optional = [branch(index) for index in range(1, 4)]
        optional[-1]["required"] = False
        optional[-1]["failure_policy"] = "optional_missing"
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "dual_report_optional_branch_forbidden"
        ):
            validate_dual_report_plan(plan(branches=optional))
        dependent = [branch(index) for index in range(1, 4)]
        dependent[-1]["depends_on"] = ["branch-1"]
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "dual_report_branch_dependency_forbidden"
        ):
            validate_dual_report_plan(plan(branches=dependent))

    def test_terra_receives_every_full_luna_body_in_plan_order(self) -> None:
        value, read_bundle, results, reports = self.fixture(4)
        shuffled = [reports[2], reports[0], reports[3], reports[1]]
        terra_input = build_terra_final_input(
            plan=value, read_bundle=read_bundle, branch_results=results,
            luna_reports=shuffled
        )
        self.assertEqual(
            [row["branch_id"] for row in terra_input["luna_reports"]],
            [row["branch_id"] for row in value["branches"]],
        )
        self.assertEqual(
            terra_input["luna_reports"][2]["findings"],
            [{"code": "finding-3"}],
        )
        self.assertTrue(all("summary" in row for row in terra_input["luna_reports"]))

    def test_missing_extra_duplicate_and_tampered_luna_reports_fail_closed(self) -> None:
        value, read_bundle, results, reports = self.fixture()
        cases = [
            reports[:-1],
            [*reports, copy.deepcopy(reports[0])],
            [reports[0], reports[0], reports[2]],
        ]
        for rows in cases:
            with self.subTest(size=len(rows)), self.assertRaises(
                MultiAgentReportContractError
            ):
                build_terra_final_input(
                    plan=value, read_bundle=read_bundle,
                    branch_results=results, luna_reports=rows
                )
        tampered = copy.deepcopy(reports)
        tampered[0]["summary"] = "changed after sealing"
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "luna_report_digest_invalid"
        ):
            build_terra_final_input(
                plan=value, read_bundle=read_bundle,
                branch_results=results, luna_reports=tampered
            )

        rebound = copy.deepcopy(reports)
        rebound[0]["findings"] = [{"code": "invented-after-branch"}]
        rebound_core = {
            key: item for key, item in rebound[0].items()
            if key != "report_sha256"
        }
        rebound[0]["report_sha256"] = sha256_value(rebound_core)
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "luna_report_content_invalid"
        ):
            build_terra_final_input(
                plan=value, read_bundle=read_bundle,
                branch_results=results, luna_reports=rebound
            )

        tampered_bundle = copy.deepcopy(read_bundle)
        tampered_bundle["coverage_complete"] = not tampered_bundle[
            "coverage_complete"
        ]
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "read_bundle_digest_invalid"
        ):
            build_terra_final_input(
                plan=value, read_bundle=tampered_bundle,
                branch_results=results, luna_reports=reports
            )

    def test_failed_branch_cannot_be_promoted_to_semantic_luna_report(self) -> None:
        value = plan()
        result = branch_result(value, 1)
        result["status"] = "failed"
        core = {key: item for key, item in result.items() if key != "result_sha256"}
        result["result_sha256"] = sha256_value(core)
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "luna_report_failed_branch_forbidden"
        ):
            build_luna_investigation_report(
                plan=value, read_bundle=bundle(value), branch_result=result,
                summary="must not build", confidence="unknown"
            )

    def test_successor_luna_report_seals_every_execution_artifact(self) -> None:
        value = plan()
        read_bundle = bundle(value)
        result = branch_result(value, 1)
        artifacts = {
            "read_session_id": "SESSION-1",
            "read_session_manifest_sha256": "1" * 64,
            "opened_session_receipt_ref": "study://opened",
            "final_session_receipt_ref": "study://final",
        }
        for index, stem in enumerate((
            "mcp_transcript", "mcp_call_receipt", "raw_output",
            "stage_execution_receipt", "normalization_receipt",
        ), start=2):
            artifacts[f"{stem}_sha256"] = str(index) * 64
            artifacts[f"{stem}_ref"] = (
                f"study-intake-{stem.replace('_', '-')}://sha256/"
                + str(index) * 64
            )
        report = build_luna_investigation_report_v2(
            plan=value, read_bundle=read_bundle, branch_result=result,
            summary="successor", confidence="high",
            execution_artifacts=artifacts,
        )
        validate_luna_investigation_report_v2(
            report, plan=value, read_bundle=read_bundle,
            branch_result=result,
        )
        for key in artifacts:
            changed = copy.deepcopy(report)
            changed["execution_artifacts"][key] = (
                None if key.endswith("_ref") else "0" * 64
            )
            with self.subTest(key=key), self.assertRaises(
                MultiAgentReportContractError
            ):
                validate_luna_investigation_report_v2(
                    changed, plan=value, read_bundle=read_bundle,
                    branch_result=result,
                )

    def test_terra_final_rejects_missing_extra_or_reordered_bindings(self) -> None:
        value, read_bundle, results, reports = self.fixture()
        terra_input = build_terra_final_input(
            plan=value, read_bundle=read_bundle, branch_results=results,
            luna_reports=reports
        )
        final = build_terra_final_report(
            terra_input=terra_input, summary="Terra final", warnings=[]
        )
        validate_terra_final_report(final, terra_input=terra_input)
        mutations = []
        missing = copy.deepcopy(final)
        missing["ordered_luna_reports"].pop()
        mutations.append(missing)
        extra = copy.deepcopy(final)
        extra["ordered_luna_reports"].append(copy.deepcopy(extra["ordered_luna_reports"][0]))
        mutations.append(extra)
        reordered = copy.deepcopy(final)
        reordered["ordered_luna_reports"].reverse()
        mutations.append(reordered)
        wrong_ref = copy.deepcopy(final)
        wrong_ref["ordered_luna_reports"][0]["report_ref"] = (
            "study-intake-luna-investigation-report://sha256/" + "0" * 64
        )
        mutations.append(wrong_ref)
        for mutation in mutations:
            with self.assertRaisesRegex(
                MultiAgentReportContractError, "terra_final_binding_invalid"
            ):
                validate_terra_final_report(mutation, terra_input=terra_input)

    def test_sol_handoff_closes_exact_luna_set_and_terra_final_ref(self) -> None:
        value, read_bundle, results, reports = self.fixture()
        terra_input = build_terra_final_input(
            plan=value, read_bundle=read_bundle, branch_results=results,
            luna_reports=reports
        )
        final = build_terra_final_report(terra_input=terra_input, summary="Terra final")
        handoff = build_sol_handoff_v2(
            legacy_handoff=legacy_handoff(value), terra_input=terra_input,
            terra_final_report=final
        )
        self.assertEqual(handoff["ordered_luna_reports"], final["ordered_luna_reports"])
        self.assertEqual(handoff["plan_sha256"], value["plan_sha256"])
        self.assertEqual(
            handoff["allowed_sol_actions"],
            ["adopt", "modify", "reject", "request_more_evidence"],
        )
        self.assertTrue(handoff["terra_final_report"]["report_ref"].endswith(final["report_sha256"]))
        self.assertFalse(handoff["formal_apply_authorized"])
        self.assertEqual(handoff["formal_write_count"], 0)
        validate_sol_handoff_v2(
            handoff, legacy_handoff=legacy_handoff(value),
            terra_input=terra_input, terra_final_report=final
        )
        invalid_legacy = legacy_handoff(value)
        invalid_legacy["review_sha256"] = "not-a-sha"
        invalid_core = {
            key: item for key, item in invalid_legacy.items()
            if key != "handoff_sha256"
        }
        invalid_legacy["handoff_sha256"] = sha256_value(invalid_core)
        with self.assertRaisesRegex(
            MultiAgentReportContractError, "sol_handoff_legacy_binding_invalid"
        ):
            build_sol_handoff_v2(
                legacy_handoff=invalid_legacy, terra_input=terra_input,
                terra_final_report=final
            )
        for key in ("ordered_luna_reports", "terra_final_report"):
            mutation = copy.deepcopy(handoff)
            if key == "ordered_luna_reports":
                mutation[key].reverse()
            else:
                mutation[key]["report_sha256"] = "0" * 64
            with self.subTest(key=key), self.assertRaisesRegex(
                MultiAgentReportContractError, "sol_handoff_v2_binding_invalid"
            ):
                validate_sol_handoff_v2(
                    mutation, legacy_handoff=legacy_handoff(value),
                    terra_input=terra_input, terra_final_report=final
                )

    def test_new_schemas_parse_and_generator_declares_them(self) -> None:
        names = {
            "luna-investigation-report-v1.json",
            "luna-investigation-report-v2.json",
            "terra-final-report-v1.json",
            "terra-final-report-v2.json",
            "sol-handoff-envelope-v2.json",
            "sol-handoff-envelope-v3.json",
        }
        for name in names:
            schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(
                (ROOT / "schemas" / name).read_bytes(),
                (ROOT / "plugin/kaoyan-study-intake/schemas" / name).read_bytes(),
            )
        source = (
            ROOT / "plugin/kaoyan-study-intake/scripts/generate_manifests.py"
        ).read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f'"{name}"', source)

        value, read_bundle, results, reports = self.fixture(4)
        terra_input = build_terra_final_input(
            plan=value, read_bundle=read_bundle, branch_results=results,
            luna_reports=reports
        )
        final = build_terra_final_report(
            terra_input=terra_input, summary="Terra final"
        )
        handoff = build_sol_handoff_v2(
            legacy_handoff=legacy_handoff(value), terra_input=terra_input,
            terra_final_report=final
        )
        instances = {
            "luna-investigation-report-v1.json": reports[0],
            "terra-final-report-v1.json": final,
            "sol-handoff-envelope-v2.json": handoff,
        }
        jsonschema_python = Path("/opt/miniconda3/envs/dl/bin/python")
        for name, instance in instances.items():
            request = {
                "schema": json.loads((ROOT / "schemas" / name).read_text()),
                "instance": instance,
            }
            completed = subprocess.run(
                [
                    str(jsonschema_python), "-c",
                    "import json,sys; from jsonschema import Draft202012Validator; "
                    "r=json.load(sys.stdin); Draft202012Validator.check_schema(r['schema']); "
                    "e=list(Draft202012Validator(r['schema']).iter_errors(r['instance'])); "
                    "json.dump([x.message for x in e],sys.stdout)",
                ],
                input=json.dumps(request), text=True, capture_output=True,
                check=False, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), [], name)


if __name__ == "__main__":
    unittest.main()
