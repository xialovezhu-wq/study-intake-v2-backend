from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from analysis_package_v1 import AnalysisPackageError, AnalysisPackageStore, build_durable_capture  # noqa: E402
from analysis_package_v2 import (  # noqa: E402
    build_terra_initial_analysis,
    publish_analysis_package_v2,
    reopen_analysis_package_v2,
)
from multi_agent_report_contract import (  # noqa: E402
    build_luna_investigation_report,
    build_luna_investigation_report_v2,
    build_sol_handoff_v2,
    build_sol_handoff_v3,
    build_terra_final_input,
    build_terra_final_report,
    build_terra_final_input_v2,
    build_terra_final_report_v2,
)
from tests.test_multi_agent_report_contract import (  # noqa: E402
    branch_result,
    bundle,
    legacy_handoff,
    plan,
)
from read_bundle import build_read_bundle  # noqa: E402


class AnalysisPackageV2Tests(unittest.TestCase):
    @staticmethod
    def _terra_execution(phase: str, seed: str) -> dict:
        stage = {
            "initial": "math_analysis",
            "final": "math_critical_review",
        }[phase]
        raw_sha = seed * 64
        execution_sha = chr(ord(seed) + 1) * 64
        normalization_sha = chr(ord(seed) + 2) * 64
        return {
            "requested_model": "gpt-5.6-terra",
            "requested_reasoning_effort": "max",
            "provider_stage_name": stage,
            "raw_output_sha256": raw_sha,
            "raw_output_ref": (
                "study-intake-direct-model-stage-raw://sha256/" + raw_sha
            ),
            "stage_execution_receipt_sha256": execution_sha,
            "stage_execution_receipt_ref": (
                "study-intake-direct-model-stage-execution://sha256/"
                + execution_sha
            ),
            "normalization_receipt_sha256": normalization_sha,
            "normalization_receipt_ref": (
                "study-intake-direct-model-stage-normalization://sha256/"
                + normalization_sha
            ),
            "formal_write_count": 0,
        }

    def _inputs(self, count: int = 3) -> dict:
        read_plan = plan(count)
        read_bundle = bundle(read_plan)
        results = [
            branch_result(read_plan, index) for index in range(1, count + 1)
        ]
        reports = [
            build_luna_investigation_report(
                plan=read_plan, read_bundle=read_bundle,
                branch_result=result, summary=f"Luna investigation {index}",
                confidence="high",
            )
            for index, result in enumerate(results, start=1)
        ]
        terra_input = build_terra_final_input(
            plan=read_plan, read_bundle=read_bundle, branch_results=results,
            luna_reports=reports,
        )
        final = build_terra_final_report(
            terra_input=terra_input, summary="Terra final"
        )
        legacy = legacy_handoff(read_plan)
        legacy["read_bundle_sha256"] = read_bundle["read_bundle_sha256"]
        legacy_core = {key: item for key, item in legacy.items() if key != "handoff_sha256"}
        legacy["handoff_sha256"] = __import__("multi_agent_report_contract").sha256_value(legacy_core)
        handoff = build_sol_handoff_v2(
            legacy_handoff=legacy, terra_input=terra_input,
            terra_final_report=final,
        )
        capture = build_durable_capture(
            capture_id=read_plan["capture_id"], subject=read_plan["subject"],
            study_date="2026-08-24", captured_at="2026-08-24T09:00:00+08:00",
            payload={"synthetic": True}, source_kind="synthetic",
        )
        initial = build_terra_initial_analysis(
            capture=capture, summary="Terra initial",
            analysis={"sections": [{
                "kind": "investigation_plan", "summary": "identity and relations",
                "evidence_refs": ["mcp-item:math:initial"],
            }]},
            proposed_branches=[{
                "branch_id": row["branch_id"], "purpose": row["purpose"],
                "rationale": row["rationale"],
            } for row in read_plan["branches"]],
            evidence_refs=["mcp-item:math:initial"],
        )
        return {
            "capture": capture, "plan": read_plan, "read_bundle": read_bundle,
            "branch_results": results, "terra_initial": initial,
            "terra_initial_execution": self._terra_execution("initial", "1"),
            "luna_outputs": reports, "terra_final": final,
            "terra_final_execution": self._terra_execution("final", "4"),
            "sol_handoff": handoff,
        }

    def test_three_and_four_outputs_persist_one_reference_only_package(self) -> None:
        for count in (3, 4):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as folder:
                store = AnalysisPackageStore(Path(folder))
                package = publish_analysis_package_v2(store, **self._inputs(count))
                self.assertEqual(len(package["luna_outputs"]), count)
                self.assertNotIn("summary", package["luna_outputs"][0])
                self.assertEqual(
                    package["terra_initial_execution"]["provider_stage_name"],
                    "math_analysis",
                )
                self.assertEqual(
                    package["terra_final_execution"]["provider_stage_name"],
                    "math_critical_review",
                )
                reopened = reopen_analysis_package_v2(
                    store, package["package_sha256"]
                )
                self.assertEqual(reopened, {
                    key: value for key, value in package.items()
                    if key not in {"package_sha256", "package_ref"}
                })
                pointer = (
                    store.index_root / "math" / "2026-08-24"
                    / "CAP-DUAL-1.json"
                )
                self.assertEqual(
                    json.loads(pointer.read_text())["schema_version"],
                    "study-intake-analysis-package-pointer-v2",
                )

    def test_tampered_object_and_exact_set_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            values = self._inputs(3)
            package = publish_analysis_package_v2(store, **values)
            first = package["luna_outputs"][0]
            path = store.report_root / first["sha256"][:2] / f"{first['sha256']}.json"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(AnalysisPackageError, "analysis_object_reopen_invalid"):
                reopen_analysis_package_v2(store, package["package_sha256"])

        values = self._inputs(3)
        values["luna_outputs"] = values["luna_outputs"][:-1]
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(
            AnalysisPackageError, "analysis_package_v2_luna_set_invalid"
        ):
            publish_analysis_package_v2(AnalysisPackageStore(Path(folder)), **values)

    def test_failed_branch_is_a_separate_diagnostic_not_a_fake_report(self) -> None:
        values = self._inputs(4)
        plan = values["plan"]
        results = [branch_result(plan, index) for index in range(1, 5)]
        diagnostic = copy.deepcopy(results[-1])
        diagnostic["status"] = "failed"
        diagnostic_core = {
            key: item for key, item in diagnostic.items()
            if key != "result_sha256"
        }
        diagnostic["result_sha256"] = __import__(
            "multi_agent_report_contract"
        ).sha256_value(diagnostic_core)
        results[-1] = diagnostic
        bundle = build_read_bundle(plan, {
            "results": results, "logical_branch_count": 4,
            "physical_slot_count": 4, "maximum_active_branch_count": 4,
            "wave_count": 1, "duration_ms": 1,
        })
        execution = {
            "read_session_id": "SESSION-PARTIAL",
            "read_session_manifest_sha256": "1" * 64,
            "opened_session_receipt_ref": "study://opened",
            "final_session_receipt_ref": "study://final",
            "mcp_transcript_sha256": "2" * 64,
            "mcp_transcript_ref": "study://mcp/" + "2" * 64,
            "mcp_call_receipt_sha256": "3" * 64,
            "mcp_call_receipt_ref": "study://call/" + "3" * 64,
            "raw_output_sha256": "4" * 64,
            "raw_output_ref": "study://raw/" + "4" * 64,
            "stage_execution_receipt_sha256": "5" * 64,
            "stage_execution_receipt_ref": "study://stage/" + "5" * 64,
            "normalization_receipt_sha256": "6" * 64,
            "normalization_receipt_ref": "study://normalization/" + "6" * 64,
        }
        reports = [
            build_luna_investigation_report_v2(
                plan=plan, read_bundle=bundle, branch_result=result,
                summary=f"success {index}", confidence="high",
                execution_artifacts=execution,
            )
            for index, result in enumerate(results[:3], start=1)
        ]
        terra_input = build_terra_final_input_v2(
            plan=plan, read_bundle=bundle, branch_results=results,
            luna_reports=reports, diagnostic_records=[diagnostic],
        )
        # Build the final/handoff from the exact successful report set; the
        # diagnostic remains a separate outer-package member.
        final = build_terra_final_report_v2(
            terra_input=terra_input, summary="partial final",
            branch_assessments=[{
                "branch_id": row["branch_id"], "outcome": row["outcome"],
                "disposition": (
                    "adopt" if row["outcome"] == "report" else "diagnostic_only"
                ), "rationale": "synthetic partial closure",
            } for row in terra_input["branch_coverage"]],
            subject_analysis={"sections": ["synthetic"]},
        )
        legacy = legacy_handoff(plan)
        legacy["read_bundle_sha256"] = bundle["read_bundle_sha256"]
        legacy_core = {
            key: item for key, item in legacy.items()
            if key != "handoff_sha256"
        }
        legacy["handoff_sha256"] = __import__(
            "multi_agent_report_contract"
        ).sha256_value(legacy_core)
        handoff = build_sol_handoff_v3(
            legacy_handoff=legacy, terra_input=terra_input,
            terra_final_report=final,
        )
        values.update({
            "read_bundle": bundle, "branch_results": results,
            "luna_outputs": [*reports, diagnostic],
            "terra_final": final, "sol_handoff": handoff,
        })
        values["terra_initial"] = build_terra_initial_analysis(
            capture=values["capture"], summary="initial",
            analysis={"sections": [{"kind": "plan", "summary": "four branches", "evidence_refs": []}]},
            proposed_branches=[{
                "branch_id": row["branch_id"], "purpose": row["purpose"],
                "rationale": row["rationale"],
            } for row in plan["branches"]],
        )
        with tempfile.TemporaryDirectory() as folder:
            package = publish_analysis_package_v2(
                AnalysisPackageStore(Path(folder)), **values
            )
            self.assertEqual(len(package["ordered_luna_reports"]), 3)
            self.assertEqual(len(package["ordered_luna_diagnostics"]), 1)
            self.assertEqual(
                package["ordered_luna_diagnostics"][0]["kind"],
                "diagnostic_record",
            )

    def test_v1_pointer_reopens_and_v2_cannot_clobber_it(self) -> None:
        from analysis_package_v1 import AnalysisPackageDriver
        from tests.test_analysis_package_v1 import executor

        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            values = self._inputs(3)
            v1 = AnalysisPackageDriver(store, executor).run(values["capture"])
            self.assertEqual(
                store.reopen_package(v1["package_sha256"])["schema_version"],
                "study-intake-analysis-package-v1",
            )
            with self.assertRaisesRegex(
                AnalysisPackageError, "analysis_object_no_clobber_conflict"
            ):
                publish_analysis_package_v2(store, **values)

    def test_v2_pointer_is_idempotent_but_changed_package_cannot_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            values = self._inputs(3)
            first = publish_analysis_package_v2(store, **values)
            second = publish_analysis_package_v2(store, **values)
            self.assertEqual(first["package_sha256"], second["package_sha256"])
            changed = copy.deepcopy(values)
            changed["terra_initial"] = build_terra_initial_analysis(
                capture=changed["capture"], summary="changed initial",
                analysis={"sections": [{
                    "kind": "plan", "summary": "changed", "evidence_refs": [],
                }]},
                proposed_branches=[{
                    "branch_id": row["branch_id"], "purpose": row["purpose"],
                    "rationale": row["rationale"],
                } for row in changed["plan"]["branches"]],
            )
            with self.assertRaisesRegex(
                AnalysisPackageError, "analysis_object_no_clobber_conflict"
            ):
                publish_analysis_package_v2(store, **changed)

    def test_terra_execution_bindings_are_required_and_fail_closed(self) -> None:
        mutations = []
        for field, changed in (
            ("requested_model", "gpt-5.6-luna"),
            ("requested_reasoning_effort", "high"),
            ("provider_stage_name", "math_critical_review"),
            ("formal_write_count", 1),
        ):
            value = self._terra_execution("initial", "1")
            value[field] = changed
            mutations.append(value)
        missing = self._terra_execution("initial", "1")
        missing.pop("raw_output_ref")
        mutations.append(missing)
        extra = self._terra_execution("initial", "1")
        extra["unexpected"] = True
        mutations.append(extra)
        bad_ref = self._terra_execution("initial", "1")
        bad_ref["normalization_receipt_ref"] = (
            "study-intake-direct-model-stage-normalization://sha256/" + "f" * 64
        )
        mutations.append(bad_ref)
        for index, execution in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as folder:
                values = self._inputs(3)
                values["terra_initial_execution"] = execution
                with self.assertRaisesRegex(
                    AnalysisPackageError,
                    "terra_execution_(binding|artifact)_invalid",
                ):
                    publish_analysis_package_v2(
                        AnalysisPackageStore(Path(folder)), **values
                    )

    def test_deep_reopen_rejects_terra_execution_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            package = publish_analysis_package_v2(store, **self._inputs(3))
            tampered = {
                key: copy.deepcopy(value)
                for key, value in package.items()
                if key not in {"package_sha256", "package_ref"}
            }
            tampered["terra_final_execution"]["provider_stage_name"] = (
                "english_critical_review"
            )
            digest, _ = store._publish(
                store.package_root,
                tampered,
                "study-intake-analysis-package",
            )
            with self.assertRaisesRegex(
                AnalysisPackageError, "terra_execution_binding_invalid"
            ):
                reopen_analysis_package_v2(store, digest)

    def test_changed_terra_execution_cannot_clobber_v2_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            values = self._inputs(3)
            first = publish_analysis_package_v2(store, **values)
            changed = copy.deepcopy(values)
            changed["terra_final_execution"] = self._terra_execution("final", "7")
            with self.assertRaisesRegex(
                AnalysisPackageError, "analysis_object_no_clobber_conflict"
            ):
                publish_analysis_package_v2(store, **changed)
            self.assertEqual(
                reopen_analysis_package_v2(store, first["package_sha256"]),
                {
                    key: value for key, value in first.items()
                    if key not in {"package_sha256", "package_ref"}
                },
            )

    def test_new_schema_mirrors_and_package_instance(self) -> None:
        for name in ("terra-initial-analysis-v1.json", "analysis-package-v2.json"):
            self.assertEqual(
                (ROOT / "schemas" / name).read_bytes(),
                (ROOT / "plugin/kaoyan-study-intake/schemas" / name).read_bytes(),
            )
        with tempfile.TemporaryDirectory() as folder:
            package = publish_analysis_package_v2(
                AnalysisPackageStore(Path(folder)), **self._inputs(4)
            )
            schema = json.loads((ROOT / "schemas/analysis-package-v2.json").read_text())
            instance = {
                key: value for key, value in package.items()
                if key not in {"package_sha256", "package_ref"}
            }
            completed = subprocess.run(
                [
                    "/opt/miniconda3/envs/dl/bin/python", "-c",
                    "import json,sys; from jsonschema import Draft202012Validator; "
                    "r=json.load(sys.stdin); Draft202012Validator.check_schema(r['schema']); "
                    "e=list(Draft202012Validator(r['schema']).iter_errors(r['instance'])); "
                    "json.dump([x.message for x in e],sys.stdout)",
                ],
                input=json.dumps({"schema": schema, "instance": instance}),
                text=True, capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), [])


if __name__ == "__main__":
    unittest.main()
