from __future__ import annotations

import datetime as dt
import fcntl
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from analysis_package_v1 import (  # noqa: E402
    AnalysisPackageDriver,
    AnalysisPackageStore,
    REPORT_SCHEMA,
    build_durable_capture,
    sha256_value,
)
from nightly_sol_alignment import (  # noqa: E402
    NightlySolCoordinator,
    NightlySolError,
    NightlySolStore,
    freeze_from_store,
    parse_nightly_command,
)


def executor(stage: str, model: str, stage_input: dict) -> dict:
    subject = stage_input["subject"]
    return {
        "report": {
            "schema_version": REPORT_SCHEMA,
            "stage": stage,
            "subject": subject,
            "capture_id": stage_input["capture_id"],
            "summary": "synthetic report",
            "proposals": [],
            "duplicate_candidates": [],
            "warnings": [],
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
        "receipt": {"stage": stage, "formal_write_count": 0},
    }


class NightlySolAlignmentTests(unittest.TestCase):
    def test_exact_command_routes_absolute_or_resolved_today_only(self) -> None:
        today = dt.date(2026, 8, 21)
        self.assertEqual(
            parse_nightly_command("开始 2026-08-20 数学正式入库", today=today),
            {
                "subject": "math",
                "capture_intake_date": "2026-08-20",
                "normalized_command": "开始 2026-08-20 数学正式入库",
            },
        )
        self.assertEqual(
            parse_nightly_command("开始今天的408正式入库", today=today)[
                "normalized_command"
            ],
            "开始 2026-08-21 408正式入库",
        )
        for invalid in (
            "开始今天数学正式入库",
            " 开始今天的英语正式入库",
            "开始 2026-08-21 英语正式入库，请执行",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                NightlySolError, "nightly_command_not_exact"
            ):
                parse_nightly_command(invalid, today=today)

    def test_batch_uses_capture_intake_date_not_study_date_and_binds_skill(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store = AnalysisPackageStore(root)
            driver = AnalysisPackageDriver(package_store, executor)
            for index, study_date in enumerate(("2026-08-14", "2026-08-19"), start=1):
                driver.run(
                    build_durable_capture(
                        capture_id=f"CAP-MATH-{index:03d}",
                        subject="math",
                        study_date=study_date,
                        captured_at=f"2026-08-21T0{index}:00:00+08:00",
                        payload={"synthetic": index},
                        source_kind="synthetic",
                    )
                )
            skill = root / "SKILL.md"
            skill.write_text("synthetic skill\n", encoding="utf-8")
            batch = freeze_from_store(
                package_store=package_store,
                subject="math",
                capture_intake_date="2026-08-21",
                skill_name="kaoyan-math-nightly-qa",
                skill_source_path=skill,
            )
            self.assertEqual(
                batch["capture_ids"], ["CAP-MATH-001", "CAP-MATH-002"]
            )
            self.assertEqual(batch["capture_intake_date"], "2026-08-21")
            self.assertEqual(batch["capture_set_sha256"], sha256_value(batch["capture_ids"]))
            self.assertEqual(batch["skill"]["source_sha256"], __import__("hashlib").sha256(skill.read_bytes()).hexdigest())

    def test_hard_conflict_is_persisted_after_lease_release_and_resume_is_single_item(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = NightlySolStore(root)
            coordinator = NightlySolCoordinator(store)
            batch = {
                "schema_version": "study-intake-nightly-sol-batch-v1",
                "batch_id": "NIGHTLY-" + "A" * 28,
                "subject": "english",
                "capture_intake_date": "2026-08-21",
                "capture_ids": ["CAP-EN-001", "CAP-EN-002"],
                "capture_set_sha256": "a" * 64,
                "analysis_packages": [],
                "skill": {},
                "status": "frozen",
                "formal_write_count": 0,
            }
            native = {
                "schema_version": "english_apply_receipt_v1",
                "subject": "english",
                "batch_id": batch["batch_id"],
                "status": "awaiting_user",
                "capture_results": [
                    {"capture_id": "CAP-EN-001", "status": "committed"}
                ],
                "changed_files": ["bank/master_bank.csv"],
                "formal_write_count": 1,
                "conflict": {
                    "capture_id": "CAP-EN-002",
                    "kind": "ambiguous_formal_target",
                },
            }
            conflict_id = "CONFLICT-" + "B" * 24
            result = {
                "schema_version": "study-intake-nightly-sol-adapter-result-v1",
                "adapter_name": "EnglishNightlySolAdapter",
                "subject": "english",
                "batch_id": batch["batch_id"],
                "status": "awaiting_user",
                "native_receipt": native,
                "native_receipt_sha256": sha256_value(native),
                "conflict_id": conflict_id,
                "formal_write_count": 1,
            }
            recorded = coordinator.record_adapter_result(batch=batch, result=result)
            self.assertTrue(recorded["writer_lease_released"])
            drifted = dict(result)
            drifted["native_receipt"] = {
                **native,
                "changed_files": ["bank/sentence_patterns.md"],
            }
            drifted["native_receipt_sha256"] = sha256_value(
                drifted["native_receipt"]
            )
            with self.assertRaisesRegex(
                NightlySolError, "nightly_pointer_no_clobber_conflict"
            ):
                coordinator.record_adapter_result(batch=batch, result=drifted)
            with store.global_lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            resumed = coordinator.record_resolution(
                conflict_id=conflict_id,
                resolution={"selected_target": "synthetic-target-a"},
            )
            self.assertEqual(resumed["resume_scope"], "conflicted_capture_only")
            self.assertFalse(resumed["rerun_analysis_package"])
            self.assertTrue(resumed["writer_lease_released"])

    def test_nonconflict_result_cannot_smuggle_conflict_id(self) -> None:
        batch = {"subject": "cs408", "batch_id": "BATCH-1"}
        native = {"formal_write_count": 0}
        result = {
            "schema_version": "study-intake-nightly-sol-adapter-result-v1",
            "adapter_name": "CS408NightlySolAdapter",
            "subject": "cs408",
            "batch_id": "BATCH-1",
            "status": "complete",
            "native_receipt": native,
            "native_receipt_sha256": sha256_value(native),
            "conflict_id": "CONFLICT-BAD",
            "formal_write_count": 0,
        }
        from nightly_sol_alignment import validate_adapter_result

        with self.assertRaisesRegex(NightlySolError, "adapter_conflict_binding_invalid"):
            validate_adapter_result(result, batch=batch)


if __name__ == "__main__":
    unittest.main()
