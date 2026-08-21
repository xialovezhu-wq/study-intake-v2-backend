from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from analysis_package_v1 import (  # noqa: E402
    AnalysisPackageDriver,
    AnalysisPackageStore,
    REPORT_SCHEMA,
    build_durable_capture,
    canonical_bytes,
    sha256_value,
)
from nightly_sol_alignment import (  # noqa: E402
    NightlySolCoordinator,
    NightlySolError,
    freeze_from_store,
    parse_nightly_command,
    validate_adapter_result,
)
from subject_sol_contract import SubjectSolRuntimeStore  # noqa: E402


LABELS = {"math": "数学", "cs408": "408", "english": "英语"}
SKILLS = {
    "math": "kaoyan-math-nightly-qa",
    "cs408": "kaoyan-408-daily-intake-curation",
    "english": "kaoyan-english-daily-intake-curation",
}


def executor(stage: str, model: str, stage_input: dict[str, Any]) -> dict[str, Any]:
    subject = stage_input["subject"]
    raw_sha = sha256_value(
        {"stage": stage, "stage_input": stage_input, "raw": "synthetic"}
    )
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
        "receipt": {
            "stage": stage,
            "raw_output_object_sha256": raw_sha,
            "raw_output_object_ref": (
                "study-intake-direct-model-stage-raw://sha256/" + raw_sha
            ),
            "formal_write_count": 0,
        },
    }


def state_sha(root: Path) -> str:
    rows = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.glob("*.json"))
    }
    return hashlib.sha256(canonical_bytes(rows)).hexdigest()


class FakeSubjectAdapter:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        batch: Mapping[str, Any],
        *,
        native_executor: Any,
        capture_ids: list[str] | None = None,
        resolution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if batch["subject"] != self.subject:
            raise AssertionError("cross-subject adapter call")
        selected = list(capture_ids or batch["capture_ids"])
        invocation = {
            "batch": dict(batch),
            "capture_ids": selected,
            "resolution": dict(resolution) if resolution is not None else None,
        }
        self.calls.append(invocation)
        return dict(native_executor(invocation))


def native_executor(
    *,
    subject: str,
    state_root: Path,
    conflict_capture_id: str | None = None,
):
    call_index = 0

    def execute(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal call_index
        call_index += 1
        batch = invocation["batch"]
        selected = list(invocation["capture_ids"])
        resolution = invocation.get("resolution")
        conflict = (
            conflict_capture_id
            if resolution is None and conflict_capture_id in selected
            else None
        )
        completed = [capture_id for capture_id in selected if capture_id != conflict]
        before = state_sha(state_root)
        operations: list[dict[str, Any]] = []
        for capture_id in completed:
            path = state_root / f"{capture_id}.json"
            payload = canonical_bytes(
                {"capture_id": capture_id, "subject": subject, "committed": True}
            )
            if not path.exists():
                path.write_bytes(payload)
                operations.append(
                    {"capture_id": capture_id, "operation": "synthetic_commit"}
                )
        after = state_sha(state_root)
        ended = f"2026-08-21T00:00:{call_index * 2:02d}Z"
        stopped = f"2026-08-21T00:00:{call_index * 2 + 1:02d}Z"
        native = {
            "schema_version": "synthetic-subject-native-terminal-v1",
            "subject": subject,
            "batch_id": batch["batch_id"],
            "status": "awaiting_user" if conflict else "committed",
            "capture_results": [
                {"capture_id": capture_id, "status": "committed"}
                for capture_id in completed
            ],
            "changed_files": [f"{capture_id}.json" for capture_id in completed],
            "formal_write_count": len(operations),
            "conflict": (
                {
                    "capture_id": conflict,
                    "kind": "ambiguous_formal_target",
                }
                if conflict
                else None
            ),
            "execution_evidence": {
                "pre_state_sha256": before,
                "post_state_sha256": after,
                "operations": operations,
                "adapter_run_id": f"synthetic-run-{call_index}",
                "pid": 101,
                "transaction_id": f"synthetic-tx-{call_index}",
                "ended_at": ended,
                "stopped_at": stopped,
                "exit_code": 0,
            },
        }
        conflict_id = (
            "CONFLICT-"
            + sha256_value(
                {
                    "subject": subject,
                    "batch_id": batch["batch_id"],
                    "capture_id": conflict,
                    "kind": "ambiguous_formal_target",
                    "skill_source_sha256": batch["skill"]["source_sha256"],
                }
            )[:24].upper()
            if conflict
            else None
        )
        return {
            "schema_version": "study-intake-nightly-sol-adapter-result-v1",
            "adapter_name": f"Synthetic{subject.title()}Adapter",
            "subject": subject,
            "batch_id": batch["batch_id"],
            "status": "awaiting_user" if conflict else "complete",
            "native_receipt": native,
            "native_receipt_sha256": sha256_value(native),
            "conflict_id": conflict_id,
            "formal_write_count": len(operations),
        }

    return execute


def prepare_batch(
    root: Path,
    *,
    subject: str,
    capture_ids: list[str],
    study_dates: list[str] | None = None,
    skill_path: Path | None = None,
) -> tuple[AnalysisPackageStore, dict[str, Any], Path]:
    package_store = AnalysisPackageStore(root)
    driver = AnalysisPackageDriver(package_store, executor)
    for index, capture_id in enumerate(capture_ids, start=1):
        driver.run(
            build_durable_capture(
                capture_id=capture_id,
                subject=subject,
                study_date=(study_dates or ["2026-08-21"] * len(capture_ids))[
                    index - 1
                ],
                captured_at=f"2026-08-21T0{index}:00:00+08:00",
                payload={"synthetic": index},
                source_kind="synthetic",
            )
        )
    skill = skill_path or (root / f"{subject}-SKILL.md")
    if skill_path is None:
        skill.write_text(f"synthetic {subject} skill\n", encoding="utf-8")
    parsed = parse_nightly_command(
        f"开始 2026-08-21 {LABELS[subject]}正式入库",
        today=dt.date(2026, 8, 21),
    )
    batch = freeze_from_store(
        package_store=package_store,
        subject=subject,
        capture_intake_date="2026-08-21",
        skill_name=SKILLS[subject],
        skill_source_path=skill,
        authorization=parsed["authorization"],
    )
    return package_store, batch, skill


class NightlySolAlignmentTests(unittest.TestCase):
    def test_exact_command_routes_absolute_or_resolved_today_only(self) -> None:
        today = dt.date(2026, 8, 21)
        absolute = parse_nightly_command(
            "开始 2026-08-20 数学正式入库", today=today
        )
        self.assertEqual(absolute["subject"], "math")
        self.assertEqual(absolute["capture_intake_date"], "2026-08-20")
        self.assertEqual(
            absolute["authorization"]["normalized_command"],
            "开始 2026-08-20 数学正式入库",
        )
        relative = parse_nightly_command("开始今天的408正式入库", today=today)
        self.assertEqual(
            relative["authorization"]["normalized_command"],
            "开始 2026-08-21 408正式入库",
        )
        for invalid in (
            "开始今天数学正式入库",
            "快速入库",
            "记录一下",
            "今天做过",
            " 开始今天的英语正式入库",
            "开始 2026-08-21 英语正式入库，请执行",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                NightlySolError, "nightly_command_not_exact"
            ):
                parse_nightly_command(invalid, today=today)

    def test_batch_uses_capture_intake_date_not_study_date_and_binds_authority(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            _store, batch, _skill = prepare_batch(
                Path(folder),
                subject="math",
                capture_ids=["CAP-MATH-001", "CAP-MATH-002"],
                study_dates=["2026-08-14", "2026-08-19"],
            )
            self.assertEqual(batch["capture_intake_date"], "2026-08-21")
            self.assertEqual(
                batch["capture_set_sha256"], sha256_value(batch["capture_ids"])
            )
            self.assertTrue(
                batch["authorization"]["authorization_id"].startswith("NAUTH-")
            )

    def test_six_exact_commands_enter_the_matching_adapter_and_subjectsol_commit(self) -> None:
        cases = [
            (subject, command)
            for subject in ("math", "cs408", "english")
            for command in (
                f"开始 2026-08-21 {LABELS[subject]}正式入库",
                f"开始今天的{LABELS[subject]}正式入库",
            )
        ]
        for subject, command in cases:
            with self.subTest(subject=subject, command=command), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                package_store, _batch, skill = prepare_batch(
                    root, subject=subject, capture_ids=[f"CAP-{subject.upper()}-001"]
                )
                adapter = FakeSubjectAdapter(subject)
                formal = root / "synthetic-formal"
                formal.mkdir()
                result = NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).dispatch_subject_thread_command(
                    text=command,
                    today=dt.date(2026, 8, 21),
                    skill_name=SKILLS[subject],
                    skill_source_path=skill,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject=subject, state_root=formal
                    ),
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id=f"synthetic-{subject}",
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(adapter.calls[0]["batch"]["subject"], subject)

    def test_real_subject_adapter_classes_feed_the_existing_subjectsol_control_plane(self) -> None:
        canonical_root = ROOT.parent
        helper_paths = {
            "math": canonical_root / "kaoyan-math/tests/test_nightly_sol_adapter.py",
            "cs408": canonical_root / "kaoyan-408/tests/test_nightly_sol_adapter.py",
            "english": canonical_root
            / "kaoyan-english/tests/english_pipeline/test_nightly_sol_adapter.py",
        }
        for subject, helper_path in helper_paths.items():
            with self.subTest(subject=subject), tempfile.TemporaryDirectory() as folder:
                spec = importlib.util.spec_from_file_location(
                    f"nightly_subject_helper_{subject}", helper_path
                )
                assert spec and spec.loader
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                adapter_module = helper.adapter
                adapter_class = getattr(
                    adapter_module,
                    {
                        "math": "MathNightlySolAdapter",
                        "cs408": "CS408NightlySolAdapter",
                        "english": "EnglishNightlySolAdapter",
                    }[subject],
                )
                root = Path(folder)
                capture_id = f"CAP-REAL-{subject.upper()}-001"
                package_store, batch, _skill = prepare_batch(
                    root,
                    subject=subject,
                    capture_ids=[capture_id],
                    skill_path=Path(adapter_module.SKILL_PATH),
                )

                def subject_native(_invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                    if subject == "math":
                        return helper.terminal(batch)
                    if subject == "cs408":
                        original = helper.batch
                        helper.batch = lambda: batch
                        try:
                            return helper.terminal([helper.curated(capture_id)])
                        finally:
                            helper.batch = original
                    return helper.terminal(batch)

                result = NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=batch,
                    adapter=adapter_class(),
                    native_executor=subject_native,
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id=f"real-adapter-{subject}",
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["subject"], subject)

    def test_duplicate_command_does_not_repeat_native_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _skill = prepare_batch(
                root, subject="math", capture_ids=["CAP-MATH-IDEMPOTENT"]
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal"
            formal.mkdir()
            coordinator = NightlySolCoordinator(
                runtime_store=SubjectSolRuntimeStore(root),
                package_store=package_store,
            )
            first = coordinator.execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native_executor(subject="math", state_root=formal),
                authorized_at="2026-08-21T00:00:00Z",
                owner_id="first",
            )
            before = state_sha(formal)
            second = coordinator.execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native_executor(subject="math", state_root=formal),
                authorized_at="2026-08-21T00:05:00Z",
                owner_id="duplicate",
            )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "ALREADY_COMMITTED")
            self.assertEqual(len(adapter.calls), 1)
            self.assertEqual(state_sha(formal), before)

    def test_adapter_exception_releases_existing_subjectsol_claim(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _skill = prepare_batch(
                root, subject="cs408", capture_ids=["CAP-408-FAIL-001"]
            )
            runtime_store = SubjectSolRuntimeStore(root)
            coordinator = NightlySolCoordinator(
                runtime_store=runtime_store,
                package_store=package_store,
            )
            adapter = FakeSubjectAdapter("cs408")

            def broken(_invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError("synthetic native failure")

            with self.assertRaisesRegex(
                NightlySolError, "subject_adapter_execution_failed"
            ):
                coordinator.execute_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=broken,
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id="failure-test",
                )
            global_state = runtime_store.read_global()
            self.assertIsNone(global_state["active_writer"])
            self.assertEqual(global_state["queue"][0]["status"], "safe_paused")

    def test_apply_publication_failure_after_review_releases_claim(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _skill = prepare_batch(
                root, subject="math", capture_ids=["CAP-MATH-PUBLISH-FAIL"]
            )
            runtime_store = SubjectSolRuntimeStore(root)
            coordinator = NightlySolCoordinator(
                runtime_store=runtime_store,
                package_store=package_store,
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal"
            formal.mkdir()
            with mock.patch.object(
                coordinator.writer_publisher,
                "publish_apply_from_execution_result_directory",
                side_effect=RuntimeError("synthetic apply publication failure"),
            ), self.assertRaisesRegex(
                NightlySolError, "subjectsol_publication_failed"
            ):
                coordinator.execute_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject="math", state_root=formal
                    ),
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id="publication-failure-test",
                )
            global_state = runtime_store.read_global()
            self.assertIsNone(global_state["active_writer"])
            self.assertEqual(global_state["queue"][0]["status"], "safe_paused")

    def test_conflict_releases_writer_and_resolution_runs_only_one_capture(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            captures = ["CAP-EN-A", "CAP-EN-B", "CAP-EN-C"]
            package_store, batch, _skill = prepare_batch(
                root, subject="english", capture_ids=captures
            )
            runtime_store = SubjectSolRuntimeStore(root)
            coordinator = NightlySolCoordinator(
                runtime_store=runtime_store,
                package_store=package_store,
            )
            adapter = FakeSubjectAdapter("english")
            formal = root / "formal"
            formal.mkdir()
            native = native_executor(
                subject="english",
                state_root=formal,
                conflict_capture_id="CAP-EN-B",
            )
            partial = coordinator.execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native,
                authorized_at="2026-08-21T00:00:00Z",
                owner_id="first-pass",
            )
            self.assertEqual(partial["status"], "PARTIAL_AWAITING_USER")
            self.assertTrue(partial["writer_lease_released"])
            self.assertIsNone(runtime_store.read_global()["active_writer"])
            a_hash = hashlib.sha256((formal / "CAP-EN-A.json").read_bytes()).hexdigest()
            c_hash = hashlib.sha256((formal / "CAP-EN-C.json").read_bytes()).hexdigest()
            self.assertFalse((formal / "CAP-EN-B.json").exists())

            def broken_resume(_invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError("synthetic resumed item infrastructure failure")

            with self.assertRaisesRegex(
                NightlySolError, "subject_adapter_execution_failed"
            ):
                coordinator.resume_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=broken_resume,
                    continuation_receipt_sha256=partial[
                        "continuation_receipt_sha256"
                    ],
                    conflict_id=partial["conflict_id"],
                    conflicted_capture_id="CAP-EN-B",
                    user_option="synthetic-target-b",
                    resolved_at="2026-08-21T00:10:00Z",
                    owner_id="resume-b-failed-once",
                )
            self.assertIsNone(runtime_store.read_global()["active_writer"])

            completed = coordinator.resume_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native,
                continuation_receipt_sha256=partial[
                    "continuation_receipt_sha256"
                ],
                conflict_id=partial["conflict_id"],
                conflicted_capture_id="CAP-EN-B",
                user_option="synthetic-target-b",
                resolved_at="2026-08-21T00:10:00Z",
                owner_id="resume-b",
            )
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(completed["resume_scope"], "conflicted_capture_only")
            self.assertFalse(completed["rerun_analysis_package"])
            self.assertEqual(
                [call["capture_ids"] for call in adapter.calls],
                [captures, ["CAP-EN-B"], ["CAP-EN-B"]],
            )
            self.assertEqual(
                hashlib.sha256((formal / "CAP-EN-A.json").read_bytes()).hexdigest(),
                a_hash,
            )
            self.assertEqual(
                hashlib.sha256((formal / "CAP-EN-C.json").read_bytes()).hexdigest(),
                c_hash,
            )
            self.assertTrue((formal / "CAP-EN-B.json").is_file())
            global_state = runtime_store.read_global()
            self.assertEqual(global_state["formal_write_count"], 3)
            self.assertEqual(global_state["queue"][0]["status"], "committed")

    def test_nonconflict_result_cannot_smuggle_conflict_id(self) -> None:
        batch = {"subject": "cs408", "batch_id": "BATCH-1"}
        native = {
            "capture_results": [],
            "execution_evidence": {
                "pre_state_sha256": "a" * 64,
                "post_state_sha256": "b" * 64,
                "operations": [],
                "adapter_run_id": "run",
                "pid": 1,
                "transaction_id": "tx",
                "ended_at": "2026-08-21T00:00:00Z",
                "stopped_at": "2026-08-21T00:00:01Z",
                "exit_code": 0,
            },
        }
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
        with self.assertRaisesRegex(
            NightlySolError, "adapter_conflict_binding_invalid"
        ):
            validate_adapter_result(result, batch=batch)


if __name__ == "__main__":
    unittest.main()
