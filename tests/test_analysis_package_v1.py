from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from analysis_package_v1 import (  # noqa: E402
    AnalysisPackageDriver,
    AnalysisPackageError,
    AnalysisPackageStore,
    REPORT_SCHEMA,
    build_durable_capture,
    capture_intake_date,
    sha256_value,
)


def executor(stage: str, model: str, stage_input: dict) -> dict:
    subject = stage_input["subject"]
    capture_id = stage_input["capture_id"]
    raw_sha = sha256_value(
        {"stage": stage, "stage_input": stage_input, "raw": "synthetic"}
    )
    return {
        "report": {
            "schema_version": REPORT_SCHEMA,
            "stage": stage,
            "subject": subject,
            "capture_id": capture_id,
            "summary": f"synthetic {stage} report",
            "proposals": [{
                "kind": "synthetic_suggestion",
                "summary": stage,
                "target_hint": None,
                "field_hints": [],
                "evidence_refs": [f"mcp-item:{subject}:synthetic:{stage}"],
            }],
            "duplicate_candidates": [],
            "warnings": (["synthetic_warning"] if stage == "luna_analysis" else []),
            "evidence_refs": [f"mcp-item:{subject}:synthetic:{stage}"],
            "formal_write_count": 0,
        },
        "runtime": {
            "requested_model": model,
            "requested_reasoning_effort": "max",
            "runtime_model": model,
            "runtime_reasoning_effort": "max",
            "runtime_metadata_provenance": "synthetic_attestation",
            "duration_ms": 1,
        },
        "receipt": {
            "schema_version": "synthetic-analysis-stage-receipt-v1",
            "stage": stage,
            "input_sha256": sha256_value(stage_input),
            "raw_output_object_sha256": raw_sha,
            "raw_output_object_ref": (
                "study-intake-direct-model-stage-raw://sha256/" + raw_sha
            ),
            "formal_write_count": 0,
        },
    }


class AnalysisPackageV1Tests(unittest.TestCase):
    def test_capture_dates_are_distinct_and_intake_date_uses_shanghai(self) -> None:
        self.assertEqual(
            capture_intake_date("2026-08-20T16:30:00Z"),
            "2026-08-21",
        )
        capture = build_durable_capture(
            capture_id="CAP-EN-001",
            subject="english",
            study_date="2026-08-14",
            captured_at="2026-08-20T16:30:00Z",
            payload={"synthetic": True},
            source_kind="synthetic",
        )
        self.assertEqual(capture["study_date"], "2026-08-14")
        self.assertEqual(capture["capture_intake_date"], "2026-08-21")

    def test_three_durable_reports_produce_one_nightly_ready_package(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            capture = build_durable_capture(
                capture_id="CAP-MATH-001",
                subject="math",
                study_date="2026-08-19",
                captured_at="2026-08-21T09:00:00+08:00",
                payload={"question": "synthetic-only"},
                source_kind="synthetic",
            )
            package = AnalysisPackageDriver(store, executor).run(capture)
            self.assertEqual(
                package["stage_order"],
                ["terra_analysis", "luna_analysis", "terra_final"],
            )
            self.assertEqual(package["status"], "ready_for_nightly")
            self.assertEqual(package["formal_write_count"], 0)
            self.assertEqual(package["warnings"], ["synthetic_warning"])
            rows = store.packages_for(
                subject="math", capture_intake_date_value="2026-08-21"
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["capture_id"], "CAP-MATH-001")
            for stage in rows[0]["stages"]:
                digest = stage["report_sha256"]
                path = store.report_root / digest[:2] / f"{digest}.json"
                self.assertEqual(
                    __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                    digest,
                )

    def test_warning_and_disagreement_do_not_reject_structurally_valid_capture(self) -> None:
        def disagreeing(stage: str, model: str, stage_input: dict) -> dict:
            result = executor(stage, model, stage_input)
            result["report"]["warnings"] = [f"{stage}_disagrees"]
            result["report"]["proposals"] = [{
                "kind": "different",
                "summary": stage,
                "target_hint": None,
                "field_hints": [],
                "evidence_refs": [
                    f"mcp-item:{stage_input['subject']}:synthetic:{stage}"
                ],
            }]
            return result

        with tempfile.TemporaryDirectory() as folder:
            capture = build_durable_capture(
                capture_id="CAP-408-001",
                subject="cs408",
                study_date="2026-08-21",
                captured_at="2026-08-21T10:00:00+08:00",
                payload={"synthetic": True},
                source_kind="synthetic",
            )
            package = AnalysisPackageDriver(
                AnalysisPackageStore(Path(folder)), disagreeing
            ).run(capture)
            self.assertEqual(len(package["warnings"]), 3)
            self.assertEqual(package["status"], "ready_for_nightly")

    def test_missing_optional_model_fields_normalize_to_warning_and_remain_ready(self) -> None:
        def incomplete(stage: str, model: str, stage_input: dict) -> dict:
            result = executor(stage, model, stage_input)
            if stage == "terra_analysis":
                result["report"] = {
                    "summary": "durable but structurally incomplete",
                    "evidence_refs": result["report"]["evidence_refs"],
                    "unexpected_advisory": {"ignored": True},
                }
            elif stage == "luna_analysis":
                result["report"]["proposals"] = "malformed"
                result["report"]["warnings"] = ["luna_quality_warning", 1]
            return result

        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            package = AnalysisPackageDriver(store, incomplete).run(
                build_durable_capture(
                    capture_id="CAP-EN-NORMALIZE-001",
                    subject="english",
                    study_date="2026-08-20",
                    captured_at="2026-08-21T10:00:00+08:00",
                    payload={"synthetic": True},
                    source_kind="synthetic",
                )
            )
            self.assertEqual(package["status"], "ready_for_nightly")
            self.assertTrue(
                any("normalization" in warning for warning in package["warnings"])
            )
            self.assertEqual(package["stages"][0]["normalization_status"], "incomplete")
            self.assertTrue(package["stages"][0]["raw_output_ref"])
            self.assertTrue(package["stages"][0]["execution_receipt_ref"])
            self.assertIn(
                "analysis_normalization_unknown_fields_dropped",
                package["warnings"],
            )

    def test_missing_raw_output_binding_is_retryable_technical_failure(self) -> None:
        def missing_raw(stage: str, model: str, stage_input: dict) -> dict:
            result = executor(stage, model, stage_input)
            result["receipt"].pop("raw_output_object_sha256")
            result["receipt"].pop("raw_output_object_ref")
            return result

        with tempfile.TemporaryDirectory() as folder:
            capture = build_durable_capture(
                capture_id="CAP-RAW-MISSING-001",
                subject="math",
                study_date="2026-08-21",
                captured_at="2026-08-21T10:00:00+08:00",
                payload={"synthetic": True},
                source_kind="synthetic",
            )
            with self.assertRaisesRegex(
                AnalysisPackageError, "analysis_stage_raw_output_sha256_invalid"
            ):
                AnalysisPackageDriver(
                    AnalysisPackageStore(Path(folder)), missing_raw
                ).run(capture)

    def test_naive_timestamp_and_runtime_or_evidence_drift_fail_closed(self) -> None:
        with self.assertRaisesRegex(AnalysisPackageError, "captured_at_timezone_missing"):
            build_durable_capture(
                capture_id="CAP-1",
                subject="math",
                study_date="2026-08-21",
                captured_at="2026-08-21T10:00:00",
                payload={},
                source_kind="synthetic",
            )

        def drifted(stage: str, model: str, stage_input: dict) -> dict:
            result = executor(stage, model, stage_input)
            result["runtime"]["runtime_model"] = "gpt-5.6-luna"
            return result

        with tempfile.TemporaryDirectory() as folder:
            capture = build_durable_capture(
                capture_id="CAP-2",
                subject="math",
                study_date="2026-08-21",
                captured_at="2026-08-21T10:00:00+08:00",
                payload={},
                source_kind="synthetic",
            )
            with self.assertRaisesRegex(
                AnalysisPackageError, "analysis_runtime_identity_invalid"
            ):
                AnalysisPackageDriver(AnalysisPackageStore(Path(folder)), drifted).run(capture)

    def test_same_content_is_idempotent_but_changed_pointer_cannot_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisPackageStore(Path(folder))
            capture = build_durable_capture(
                capture_id="CAP-EN-002",
                subject="english",
                study_date="2026-08-21",
                captured_at="2026-08-21T10:00:00+08:00",
                payload={"v": 1},
                source_kind="synthetic",
            )
            first = AnalysisPackageDriver(store, executor).run(capture)
            second = AnalysisPackageDriver(store, executor).run(capture)
            self.assertEqual(first["package_sha256"], second["package_sha256"])
            changed = dict(capture)
            changed["payload"] = {"v": 2}
            changed["payload_sha256"] = sha256_value(changed["payload"])
            with self.assertRaisesRegex(
                AnalysisPackageError, "analysis_object_no_clobber_conflict"
            ):
                AnalysisPackageDriver(store, executor).run(changed)


if __name__ == "__main__":
    unittest.main()
