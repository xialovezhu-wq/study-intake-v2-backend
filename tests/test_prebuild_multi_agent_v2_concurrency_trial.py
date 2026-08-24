from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prebuild_multi_agent_v2_trial",
    ROOT / "scripts/run_prebuild_multi_agent_v2_concurrency_trial.py",
)
assert SPEC and SPEC.loader
trial = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trial
SPEC.loader.exec_module(trial)

from preprocessor_core import Candidate  # noqa: E402


SUBJECTS = ("math", "cs408", "english")


def candidate(subject: str, ordinal: int) -> Candidate:
    capture_id = f"CAP-{subject.upper()}-{ordinal:02d}"
    return Candidate(
        subject=subject,
        capture_id=capture_id,
        study_date="2026-08-24",
        recorded_at=f"2026-08-24T09:00:0{ordinal}+08:00",
        input_fingerprint=str(ordinal) * 64,
        input_binding={"producer_fixture": True},
        model_input={"capture": {"synthetic": True}},
        allowed_evidence_refs=(),
        image_paths=(),
        target_label=capture_id,
        canonical_state="awaiting_background_analysis",
        sol_state="pending_review",
    )


class FakeRealProducer:
    """Fake ledger boundary only; model/MCP are replaced separately."""

    def __call__(self, roots: Mapping[str, Path], _trial_root: Path):
        if set(roots) != set(SUBJECTS):
            raise AssertionError("subject roots were not isolated")
        rows = []
        for subject in SUBJECTS:
            for ordinal in range(1, 4):
                value = candidate(subject, ordinal)
                rows.append(
                    trial.CaptureEnvelope(
                        subject=subject,
                        capture_id=value.capture_id,
                        candidate=value,
                        producer_entrypoint=f"/isolated/{subject}/producer.py",
                        producer_receipt={
                            "status": "recorded",
                            "capture_id": value.capture_id,
                            "prebuild_marker": f"MARKER-{value.capture_id}",
                            "formal_write_count": 0,
                        },
                    )
                )
        return rows


class FakeModelMcpExecutor:
    def __init__(self, mutation: str | None = None) -> None:
        self.mutation = mutation

    @staticmethod
    def source_binding() -> dict[str, Any]:
        core = {
            "schema_version": "fixture-source-closure-v1",
            "backend": {"closure_sha256": "a" * 64},
            "producers": {subject: "b" * 64 for subject in SUBJECTS},
            "mcp": {"release_id": "c" * 64},
            "codex": {"sha256": "d" * 64, "version": "fixture"},
            "formal_write_count": 0,
        }
        basis = {
            "backend_closure_sha256": "a" * 64,
            "producer_descriptor_sha256s": {
                subject: "b" * 64 for subject in SUBJECTS
            },
            "mcp_release_id": "c" * 64,
            "codex_sha256": "d" * 64,
        }
        return {
            **core,
            "closure_basis": basis,
            "closure_sha256": trial._sha(basis),
        }

    def __call__(self, capture: Any, _runtime: Path) -> dict[str, Any]:
        subject = capture.subject
        capture_id = capture.capture_id
        providers: list[dict[str, Any]] = [
            {
                "phase": "terra_initial",
                "provider": "terra",
                "started_at": "2026-08-24T01:00:00.000000+00:00",
                "ended_at": "2026-08-24T01:00:00.100000+00:00",
                "mcp_tool_call_count": 0,
                "formal_write_count": 0,
            }
        ]
        reports: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        diagnostic_bindings: list[dict[str, Any]] = []
        for branch in range(1, 4):
            branch_id = f"branch-{branch:02d}"
            session_id = f"SESSION-{subject}-{capture_id}-{branch_id}"
            report_sha = (str(SUBJECTS.index(subject) + 1) + str(capture_id[-2:])[-1] + str(branch)) * 22
            report_sha = report_sha[:64].ljust(64, "a")
            started = "2026-08-24T01:00:00.200000+00:00"
            ended = "2026-08-24T01:00:00.800000+00:00"
            if self.mutation == "nonconcurrent":
                task_index = SUBJECTS.index(subject) * 3 + int(capture_id[-2:]) - 1
                start_second = task_index * 2 + 2
                started = f"2026-08-24T01:00:{start_second:02d}.000000+00:00"
                ended = f"2026-08-24T01:00:{start_second + 1:02d}.000000+00:00"
            elif self.mutation == "within_capture_nonconcurrent" and subject == "math" and capture_id.endswith("01"):
                started = f"2026-08-24T01:00:0{branch}.000000+00:00"
                ended = f"2026-08-24T01:00:0{branch}.500000+00:00"
            elif self.mutation == "no_three_subject_overlap":
                windows = {
                    "math": (2, 8), "cs408": (6, 12), "english": (10, 16)
                }
                start_second, end_second = windows[subject]
                started = f"2026-08-24T01:00:{start_second:02d}.000000+00:00"
                ended = f"2026-08-24T01:00:{end_second:02d}.000000+00:00"
            providers.append(
                {
                    "phase": "luna",
                    "provider": "luna",
                    "branch_id": branch_id,
                    "started_at": started,
                    "ended_at": ended,
                    "read_session_id": session_id,
                    "mcp_tool_call_count": 2,
                    "formal_write_count": 0,
                }
            )
            reports.append(
                {
                    "subject": subject,
                    "capture_id": capture_id,
                    "branch_id": branch_id,
                    "report_sha256": report_sha,
                    "read_session_id": session_id,
                    "mcp_server": trial.EXPECTED_MCP_SERVERS[subject],
                    "collections": [sorted(trial.ALLOWED_COLLECTIONS[subject])[0]],
                    "evidence_refs": [f"mcp-item:{subject}:{capture_id}:{branch_id}"],
                    "mcp_calls": [{"tool": "get_task_context"}],
                    "plan_sha256": "4" * 64,
                    "read_bundle_sha256": "5" * 64,
                    "branch_result_sha256": "6" * 64,
                    "branch_result_object_sha256": "a" * 64,
                    "capture_marker": f"MARKER-{capture_id}",
                    "marker_transport_sha256": "d" * 64,
                    "transport_proof": {
                        "task_context_verified": True,
                        "task_context_result_sha256s": ["1" * 64],
                        "task_artifacts_verified": True,
                        "task_artifact_ids": ["capture-facts"],
                        "completed_task_artifact_ids": ["capture-facts"],
                        "artifact_marker_verified": True,
                        "artifact_result_sha256s": ["2" * 64],
                        "library_result_verified": True,
                        "library_result_sha256s": ["3" * 64],
                        "library_collections": [
                            sorted(trial.ALLOWED_COLLECTIONS[subject])[0]
                        ],
                        "validated_mcp_tool_call_count": 3,
                        "formal_write_count": 0,
                    },
                    "formal_write_count": 0,
                }
            )
            bindings.append(
                {
                    "kind": "investigation_report",
                    "branch_id": branch_id,
                    "report_sha256": report_sha,
                }
            )
        providers.append(
            {
                "phase": "terra_final",
                "provider": "terra",
                "started_at": "2026-08-24T01:00:00.900000+00:00",
                "ended_at": "2026-08-24T01:00:01.000000+00:00",
                "mcp_tool_call_count": 0,
                "formal_write_count": 0,
            }
        )
        if self.mutation in {
            "nonconcurrent", "within_capture_nonconcurrent",
            "no_three_subject_overlap",
        }:
            starts = [trial._seconds(row["started_at"]) for row in providers[1:4]]
            ends = [trial._seconds(row["ended_at"]) for row in providers[1:4]]
            import datetime as dt
            def stamp(value: float) -> str:
                return dt.datetime.fromtimestamp(
                    value, dt.timezone.utc
                ).isoformat(timespec="microseconds")
            providers[0]["started_at"] = stamp(min(starts) - 1.0)
            providers[0]["ended_at"] = stamp(min(starts) - 0.5)
            providers[-1]["started_at"] = stamp(max(ends) + 0.5)
            providers[-1]["ended_at"] = stamp(max(ends) + 1.0)
        if self.mutation == "stage_order" and subject == "math" and capture_id.endswith("01"):
            providers[0]["ended_at"] = "2026-08-24T01:00:00.300000+00:00"
        if self.mutation == "cross_subject" and subject == "math" and capture_id.endswith("01"):
            reports[0]["mcp_server"] = trial.EXPECTED_MCP_SERVERS["english"]
        if self.mutation == "cross_capture" and subject == "cs408" and capture_id.endswith("01"):
            reports[1]["capture_id"] = "CAP-CS408-02"
        if self.mutation == "missing_mcp" and subject == "english" and capture_id.endswith("01"):
            providers[1]["mcp_tool_call_count"] = 0
            reports[0]["collections"] = []
            reports[0]["evidence_refs"] = []
        if self.mutation == "report_misbinding" and subject == "math" and capture_id.endswith("02"):
            bindings[2]["report_sha256"] = "f" * 64
        if self.mutation == "missing_marker" and subject == "math" and capture_id.endswith("01"):
            reports[0]["transport_proof"]["artifact_marker_verified"] = False
            reports[0]["transport_proof"]["artifact_result_sha256s"] = []
        if (
            self.mutation in {
                "diagnostic", "diagnostic_missing_binding",
                "diagnostic_missing_mcp",
            }
            and subject == "math"
            and capture_id.endswith("01")
        ):
            diagnostic = reports.pop(2)
            digest = diagnostic.pop("report_sha256")
            diagnostic.update({
                "material_kind": "diagnostic",
                "diagnostic_sha256": digest,
                "diagnostic_status": "failed",
                "diagnostic_error_code": "synthetic_luna_quality_failure",
                "evidence_refs": [],
            })
            diagnostics.append(diagnostic)
            binding = bindings.pop(2)
            binding["kind"] = "diagnostic_record"
            diagnostic_bindings.append(binding)
            if self.mutation == "diagnostic_missing_binding":
                diagnostic_bindings.clear()
            if self.mutation == "diagnostic_missing_mcp":
                providers[3]["mcp_tool_call_count"] = 0
                diagnostic["collections"] = []
                diagnostic["transport_proof"]["library_result_verified"] = False
                diagnostic["transport_proof"]["library_result_sha256s"] = []
                diagnostic["transport_proof"]["library_collections"] = []
        return {
            "status": "completed",
            "dispatcher_completion": {
                "subject": subject,
                "capture_id": capture_id,
                "unit_sha256": "9" * 64,
                "lease_fence": 1,
                "outcome": "succeeded",
                "formal_write_count": 0,
            },
            "task_interval": {
                "started_at": "2026-08-24T01:00:00.000000+00:00",
                "ended_at": "2026-08-24T01:00:20.000000+00:00",
                "duration_ms": 20000,
            },
            "provider_intervals": providers,
            "luna_reports": reports,
            "luna_diagnostics": diagnostics,
            "package": {
                "package_id": f"PKG-{capture_id}",
                "package_sha256": "e" * 64,
                "ordered_luna_reports": bindings,
                "ordered_luna_diagnostics": diagnostic_bindings,
                "plan_branch_ids": [f"branch-{branch:02d}" for branch in range(1, 4)],
                "terra_final_report_sha256": "c" * 64,
                "terra_final_ordered_luna_reports": [
                    {
                        "branch_id": row["branch_id"],
                        "report_sha256": row["report_sha256"],
                        "report_ref": (
                            "study-intake-luna-investigation-report://sha256/"
                            + row["report_sha256"]
                        ),
                    }
                    for row in bindings
                ],
                "terra_final_ordered_luna_diagnostics": [
                    {
                        "branch_id": row["branch_id"],
                        "diagnostic_sha256": row["report_sha256"],
                        "diagnostic_ref": (
                            "study-intake-luna-diagnostic-record://sha256/"
                            + row["report_sha256"]
                        ),
                        "status": next(
                            value["diagnostic_status"]
                            for value in diagnostics
                            if value["branch_id"] == row["branch_id"]
                        ),
                    }
                    for row in diagnostic_bindings
                ],
                "terra_final_branch_coverage": [
                    {
                        "branch_id": f"branch-{branch:02d}",
                        "outcome": (
                            "diagnostic"
                            if f"branch-{branch:02d}" in {
                                row["branch_id"] for row in diagnostics
                            }
                            else "report"
                        ),
                    }
                    for branch in range(1, 4)
                ],
                "terra_final_branch_assessments": [
                    {
                        "branch_id": f"branch-{branch:02d}",
                        "outcome": (
                            "diagnostic"
                            if f"branch-{branch:02d}" in {
                                row["branch_id"] for row in diagnostics
                            }
                            else "report"
                        ),
                        "disposition": (
                            "diagnostic_only"
                            if f"branch-{branch:02d}" in {
                                row["branch_id"] for row in diagnostics
                            }
                            else "adopt"
                        ),
                        "rationale": "synthetic terminal material assessment",
                    }
                    for branch in range(1, 4)
                ],
                "plan_sha256": "4" * 64,
                "plan_object_sha256": "7" * 64,
                "read_bundle_sha256": "5" * 64,
                "read_bundle_object_sha256": "8" * 64,
                "terra_initial_report_sha256": "9" * 64,
                "formal_write_count": 0,
            },
            "active_processes_after": [],
            "semantic_stage_count": 5,
            "content_quality_warnings": (
                ["synthetic_content_warning"] if capture_id.endswith("03") else []
            ),
            "formal_write_count": 0,
        }


class PrebuildMultiAgentV2ConcurrencyTrialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source_roots: dict[str, Path] = {}
        for subject in SUBJECTS:
            root = self.base / "source" / subject
            root.mkdir(parents=True)
            (root / "producer.py").write_text("# real Producer placeholder\n", encoding="utf-8")
            self.source_roots[subject] = root
        self.surface = {
            "schema_version": "fixture-production-surface-v1",
            "current": "release-a",
            "formal_surfaces": {subject: "a" * 64 for subject in SUBJECTS},
            "service_process_summary": [],
            "formal_write_count": 0,
        }

    def run_case(self, mutation: str | None = None) -> dict[str, Any]:
        return trial.run_trial_core(
            source_roots=self.source_roots,
            trial_root=self.base / f"trial-{mutation or 'pass'}",
            producer=FakeRealProducer(),
            executor=FakeModelMcpExecutor(mutation),
            surface_snapshot=lambda: copy.deepcopy(self.surface),
            timeout_seconds=10,
        )

    def test_real_trial_fixture_pins_terra_schema_to_three_branches(
        self,
    ) -> None:
        fixture = trial._load_factory(
            ROOT / "tests/fixtures/prebuild_multi_agent_v2_producers.py",
            "prebuild_branch_schema_fixture",
        )
        schema_path = self.base / "terra-initial.json"
        schema_path.write_text(
            json.dumps(
                {
                    "properties": {
                        "proposed_branches": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 4,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        fixture._pin_prebuild_terra_branch_schema(schema_path)
        proposed = json.loads(schema_path.read_text(encoding="utf-8"))[
            "properties"
        ]["proposed_branches"]
        self.assertEqual(proposed["minItems"], 3)
        self.assertEqual(proposed["maxItems"], 3)

    def test_active_copied_output_schema_change_changes_source_binding(self) -> None:
        release = self.base / "release"
        schema_root = release / "schemas"
        schema_root.mkdir(parents=True)
        names = {
            "terra_initial_output_schema": "terra-initial.json",
            "luna_investigation_output_schema": "luna.json",
            "terra_final_output_schema": "terra-final.json",
        }
        profile: dict[str, str] = {}
        for ordinal, (key, name) in enumerate(names.items(), start=1):
            path = schema_root / name
            path.write_text(
                json.dumps({"schema": ordinal}), encoding="utf-8"
            )
            profile[key] = str(path)
        config = {"analysis_package_v2": profile}
        before = trial._active_output_schema_binding(
            config=config, source_release=release
        )
        (schema_root / "terra-initial.json").write_text(
            json.dumps({"schema": 1, "maxItems": 3}), encoding="utf-8"
        )
        after = trial._active_output_schema_binding(
            config=config, source_release=release
        )
        self.assertNotEqual(before["closure_sha256"], after["closure_sha256"])
        self.assertNotEqual(
            before["files"]["terra_initial_output_schema"]["sha256"],
            after["files"]["terra_initial_output_schema"]["sha256"],
        )

    def test_nine_tasks_and_twenty_seven_sessions_pass_with_content_warnings(self) -> None:
        report = self.run_case()
        self.assertEqual(report["technical_gate"], "PASS")
        self.assertEqual(report["status"], "PASS_WITH_CONTENT_WARNINGS")
        self.assertEqual(report["completion_count"], 9)
        self.assertEqual(report["model_call_count"], 45)
        self.assertEqual(report["independent_read_session_count"], 27)
        self.assertEqual(len(report["tasks"]), 9)

    def test_cross_subject_mcp_is_rejected(self) -> None:
        with self.assertRaisesRegex(trial.TrialError, "trial_cross_subject_mcp_binding"):
            self.run_case("cross_subject")

    def test_cross_capture_report_is_rejected(self) -> None:
        with self.assertRaisesRegex(trial.TrialError, "trial_cross_capture_report_binding"):
            self.run_case("cross_capture")

    def test_missing_mcp_collection_and_evidence_are_rejected(self) -> None:
        with self.assertRaisesRegex(trial.TrialError, "trial_luna_mcp_evidence_missing"):
            self.run_case("missing_mcp")

    def test_two_reports_and_one_transport_proved_diagnostic_pass(self) -> None:
        report = self.run_case("diagnostic")
        task = next(
            row for row in report["tasks"]
            if row["subject"] == "math" and row["capture_id"].endswith("01")
        )
        self.assertEqual(len(task["luna_reports"]), 2)
        self.assertEqual(len(task["luna_diagnostics"]), 1)
        self.assertEqual(report["independent_read_session_count"], 27)
        self.assertEqual(report["status"], "PASS_WITH_CONTENT_WARNINGS")
        self.assertIn(
            "synthetic_luna_quality_failure",
            report["content_quality_warnings"],
        )

    def test_diagnostic_without_package_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            trial.TrialError, "trial_package_diagnostic_binding_invalid"
        ):
            self.run_case("diagnostic_missing_binding")

    def test_diagnostic_without_transport_proved_mcp_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            trial.TrialError, "trial_luna_mcp_evidence_missing"
        ):
            self.run_case("diagnostic_missing_mcp")

    def test_serialized_provider_calls_are_rejected(self) -> None:
        with self.assertRaisesRegex(trial.TrialError, "trial_provider_calls_not_concurrent"):
            self.run_case("nonconcurrent")

    def test_each_capture_three_luna_branches_need_common_overlap(self) -> None:
        with self.assertRaisesRegex(
            trial.TrialError, "trial_capture_luna_branches_not_concurrent"
        ):
            self.run_case("within_capture_nonconcurrent")

    def test_pairwise_cross_subject_overlap_without_three_way_overlap_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            trial.TrialError, "trial_provider_calls_not_concurrent"
        ):
            self.run_case("no_three_subject_overlap")

    def test_terra_luna_terra_stage_order_is_rejected_when_overlapped(self) -> None:
        with self.assertRaisesRegex(
            trial.TrialError, "trial_provider_stage_order_invalid"
        ):
            self.run_case("stage_order")

    def test_package_report_misbinding_is_rejected(self) -> None:
        with self.assertRaisesRegex(trial.TrialError, "trial_package_report_binding_invalid"):
            self.run_case("report_misbinding")

    def test_capture_marker_must_be_proved_by_task_artifact_result(self) -> None:
        with self.assertRaisesRegex(trial.TrialError, "trial_mcp_capture_marker_missing"):
            self.run_case("missing_marker")

    def test_transport_proof_separates_context_artifact_and_library_results(self) -> None:
        subject = "math"
        capture_id = "CAP-MATH-01"
        marker = "MARKER-CAP-MATH-01"
        session_id = "SESSION-MATH-01"
        def item(tool: str, structured: Mapping[str, Any], arguments: Mapping[str, Any] | None = None):
            structured = {
                **dict(structured),
                "ok": True,
                "schema_version": "study-read-mcp.v3",
                "read_session": {
                    "subject": subject,
                    "capture_id": capture_id,
                    "read_session_id": session_id,
                    "artifact_ids": ["capture-facts"],
                },
            }
            return {
                "item": {
                    "tool": tool,
                    "server": trial.EXPECTED_MCP_SERVERS[subject],
                    "arguments": dict(arguments or {}),
                    "result": {"structured_content": dict(structured)},
                }
            }
        transport = {
            "mcp_items": [
                item(
                    "get_task_context",
                    {"subject": subject, "items": [{"subject": subject, "capture_id": capture_id}]},
                ),
                item(
                    "read_task_artifact",
                    {
                        "subject": subject,
                        "items": [{"text": marker}],
                        "complete": True,
                    },
                    {"artifact_id": "capture-facts"},
                ),
                item(
                    "list_records",
                    {"subject": subject, "items": [{"collection": "formal_card_catalog"}]},
                    {"collection": "formal_card_catalog"},
                ),
            ]
        }
        proof = trial._transport_proof(
            transport=transport, subject=subject, capture_id=capture_id,
            marker=marker, read_session_id=session_id,
        )
        self.assertTrue(proof["task_context_verified"])
        self.assertTrue(proof["task_artifacts_verified"])
        self.assertTrue(proof["artifact_marker_verified"])
        self.assertTrue(proof["library_result_verified"])

    def test_transport_proof_accepts_library_not_found_as_no_match(self) -> None:
        subject = "math"
        capture_id = "CAP-MATH-01"
        marker = "MARKER-CAP-MATH-01"
        session_id = "SESSION-MATH-01"

        def success_item(tool: str, structured: Mapping[str, Any], arguments=None):
            return {
                "item": {
                    "tool": tool,
                    "server": trial.EXPECTED_MCP_SERVERS[subject],
                    "arguments": dict(arguments or {}),
                    "result": {
                        "structured_content": {
                            **dict(structured),
                            "ok": True,
                            "schema_version": "study-read-mcp.v3",
                            "read_session": {
                                "subject": subject,
                                "capture_id": capture_id,
                                "read_session_id": session_id,
                                "artifact_ids": ["capture-facts"],
                            },
                        }
                    },
                }
            }

        no_match = {
            "ok": False,
            "schema_version": "study-read-mcp.v3",
            "server_release": "fixture",
            "request_id": "0123456789abcdef",
            "tool": "list_records",
            "error": {
                "code": "NOT_FOUND",
                "message": "no matching records",
                "retryable": False,
            },
            "formal_write_count": 0,
            "model_call_count": 0,
            "mcp_tool_call_count": 0,
        }
        transport = {
            "subject": subject,
            "read_session_id": session_id,
            "mcp_items": [
                success_item(
                    "get_task_context",
                    {
                        "subject": subject,
                        "items": [
                            {"subject": subject, "capture_id": capture_id}
                        ],
                    },
                ),
                success_item(
                    "read_task_artifact",
                    {
                        "subject": subject,
                        "items": [{"text": marker}],
                        "complete": True,
                    },
                    {"artifact_id": "capture-facts"},
                ),
                {
                    "item": {
                        "tool": "list_records",
                        "server": trial.EXPECTED_MCP_SERVERS[subject],
                        "arguments": {"collection": "formal_card_catalog"},
                        "result": {"structured_content": no_match},
                    }
                },
            ],
        }
        proof = trial._transport_proof(
            transport=transport,
            subject=subject,
            capture_id=capture_id,
            marker=marker,
            read_session_id=session_id,
        )
        self.assertTrue(proof["library_result_verified"])
        self.assertEqual(
            proof["library_collections"], ["formal_card_catalog"]
        )
        self.assertEqual(
            len(proof["library_no_match_result_sha256s"]), 1
        )


if __name__ == "__main__":
    unittest.main()
