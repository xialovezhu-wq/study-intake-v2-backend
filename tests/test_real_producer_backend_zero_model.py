from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = ROOT.parent
MATH_ROOT = CANONICAL_ROOT / "kaoyan-math"
CS408_ROOT = CANONICAL_ROOT / "kaoyan-408"
ENGLISH_ROOT = CANONICAL_ROOT / "kaoyan-english"
for directory in (ROOT / "lib", ROOT / "bin", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from concurrent_dispatch import (  # noqa: E402
    ConcurrentDispatcher,
    FrozenTask,
    StageResult,
)
from core_dispatch_bridge import scan_eligible_candidates  # noqa: E402
from preprocessor_core import (  # noqa: E402
    Candidate,
    Cs408Adapter,
    EnglishAdapter,
    MathAdapter,
)
from tests import test_english_adapter as backend_english_tests  # noqa: E402


RELEASE_ID = "a" * 64
PROCESSING_CONTRACT = "9" * 64
EXPECTED_MAIN_HEADS = {
    MATH_ROOT: "8dfe49dcb6e0784a716ac87248039212737ed63b",
    CS408_ROOT: "09e811e97f7e99ece7cab1751aca86ec92a7c41e",
    ENGLISH_ROOT: "2121171cc59194e41f5a4863723295593c3d0c58",
}
SUBJECT_REPOS_AVAILABLE = all(
    path.is_dir() for path in (MATH_ROOT, CS408_ROOT, ENGLISH_ROOT)
)


def load_module(name: str, path: Path, *, import_root: Path | None = None):
    if import_root is not None and str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActualAdapterWorker:
    def __init__(
        self,
        subject: str,
        adapter: Any,
        status_provider: Callable[[str | None], Mapping[str, Any]],
        *,
        transform: Callable[[Candidate], Candidate] | None = None,
    ) -> None:
        self.subject = subject
        self.adapter = adapter
        self.adapters = {subject: adapter}
        self.release_id = RELEASE_ID
        self.status_provider = status_provider
        self.transform = transform

    def scan_statuses(
        self, study_date: str | None, *, only_subject: str | None = None
    ) -> dict[str, Mapping[str, Any]]:
        if only_subject not in {None, self.subject}:
            return {}
        return {self.subject: self.status_provider(study_date)}

    def eligible_candidates(
        self,
        subject: str,
        study_date: str,
        *,
        capture_allowlist: frozenset[str] | None = None,
        controlled_replay: bool = False,
        **_kwargs: Any,
    ) -> list[tuple[Candidate, str]]:
        del study_date
        if subject != self.subject:
            return []
        rows = self.adapter.candidates(
            self.status_provider(None),
            capture_allowlist=capture_allowlist,
            controlled_replay=controlled_replay,
        )
        if self.transform is not None:
            rows = [self.transform(row) for row in rows]
        return [(row, "actual_foreground_capture") for row in rows]


class BoundaryRunner:
    def __init__(
        self,
        counts: dict[str, int],
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        self.counts = counts
        self.entered = entered
        self.release = release

    @staticmethod
    def stage(stage: str, task: FrozenTask) -> StageResult:
        return StageResult(
            payload={"stage": stage, "unit_sha256": task.unit_sha256},
            runtime_model="gpt-5.6-luna",
            runtime_reasoning_effort="max",
            runtime_metadata_provenance="codex_json_attestation_v1",
            runtime_identity_status="confirmed",
            duration_ms=1,
        )

    def run_analysis(self, task: FrozenTask, _context: object) -> StageResult:
        self.counts["analysis"] += 1
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("boundary release timeout")
        return self.stage("analysis", task)

    def run_critical_review(
        self, task: FrozenTask, _draft: Mapping[str, Any], _context: object
    ) -> StageResult:
        self.counts["critical_review"] += 1
        return self.stage("critical_review", task)


def nonzero_formal_write_values(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key == "formal_write_count" and nested != 0:
                found.append(nested)
            found.extend(nonzero_formal_write_values(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(nonzero_formal_write_values(nested))
    return found


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def forbidden_control_keys(value: Any) -> set[str]:
    forbidden = {
        "release_id",
        "activation_id",
        "dispatcher_authority",
        "dashboard_status",
        "formal_state",
    }
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in forbidden:
                found.add(str(key))
            found.update(forbidden_control_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(forbidden_control_keys(nested))
    return found


@unittest.skipUnless(
    SUBJECT_REPOS_AVAILABLE,
    "canonical Math, CS408, and English source repositories are required",
)
class RealProducerBackendZeroModelTests(unittest.TestCase):
    def assert_canonical_main_sources(
        self, repo: Path, relative_paths: tuple[str, ...]
    ) -> None:
        head = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        self.assertEqual(head, EXPECTED_MAIN_HEADS[repo])
        for relative in relative_paths:
            main_bytes = subprocess.run(
                ["git", "show", f"main:{relative}"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(
                (repo / relative).read_bytes(),
                main_bytes,
                f"working copy drifted from canonical main: {relative}",
            )

    def scan_config(self, runtime: Path, subject: str, repo: Path) -> dict:
        return {
            "timezone": "Asia/Shanghai",
            "runtime_root": str(runtime),
            "model": {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
            "adapters": {subject: {"repo_root": str(repo)}},
        }

    def scan_actual(
        self,
        *,
        runtime: Path,
        subject: str,
        repo: Path,
        worker: ActualAdapterWorker,
    ) -> tuple[FrozenTask, list[dict[str, Any]]]:
        frozen, decisions = scan_eligible_candidates(
            self.scan_config(runtime, subject, repo),
            subject,
            worker_factory=lambda _config: worker,
            publish_evidence_readiness=False,
        )
        eligible = [row for row in decisions if row.get("eligible") is True]
        self.assertEqual(len(frozen), 1, decisions)
        self.assertEqual(len(eligible), 1, decisions)
        self.assertEqual(
            frozen[0].candidate.capture_id,
            eligible[0]["capture_id"],
        )
        self.assertEqual(eligible[0]["formal_write_count"], 0)
        return frozen[0].task, decisions

    def assert_actual_cutoff_excludes(
        self,
        *,
        runtime: Path,
        subject: str,
        repo: Path,
        worker: ActualAdapterWorker,
    ) -> None:
        frozen, _decisions = scan_eligible_candidates(
            self.scan_config(runtime, subject, repo),
            subject,
            worker_factory=lambda _config: worker,
            producer_recorded_after="2099-01-01T00:00:00+00:00",
            publish_evidence_readiness=False,
        )
        self.assertEqual(frozen, [])

    def assert_runner_boundary_once(
        self, runtime: Path, task: FrozenTask
    ) -> dict[str, Any]:
        counts = {"analysis": 0, "critical_review": 0}
        entered = threading.Event()
        release = threading.Event()
        with (
            mock.patch(
                "preprocessor_core.CodexRunner._invoke_subprocess",
                side_effect=AssertionError("real Provider path invoked"),
            ) as provider_call,
            mock.patch(
                "preprocess_dispatcher.ProcessingPluginHost",
                side_effect=AssertionError("production MCP host invoked"),
            ) as mcp_host,
        ):
            first = ConcurrentDispatcher(
                runtime,
                lambda _task, _context: BoundaryRunner(
                    counts, entered, release
                ),
            )
            first_handle = first.submit(task)
            self.assertTrue(entered.wait(5))

            concurrent_duplicate = ConcurrentDispatcher(
                runtime,
                lambda _task, _context: BoundaryRunner(
                    counts, entered, release
                ),
            ).submit(task)
            active_result = concurrent_duplicate.wait(1)
            self.assertEqual(active_result.status, "deduplicated_active")

            release.set()
            completed = first_handle.wait(5)
            self.assertEqual(completed.outcome, "succeeded")
            self.assertTrue(first.drain(timeout=5))
            completed_duplicate = ConcurrentDispatcher(
                runtime,
                lambda _task, _context: BoundaryRunner(
                    counts, entered, release
                ),
            ).submit(task).wait(1)
            self.assertEqual(completed_duplicate.status, "deduplicated")
        provider_call.assert_not_called()
        mcp_host.assert_not_called()
        self.assertEqual(counts, {"analysis": 1, "critical_review": 1})

        claim_events = 0
        event_root = (
            runtime
            / "dispatch/state/task-events"
            / task.unit_sha256
            / "fence-1"
        )
        for index_path in event_root.glob("*.json"):
            index = json.loads(index_path.read_text(encoding="utf-8"))
            event = json.loads(
                Path(index["event_path"]).read_text(encoding="utf-8")
            )
            claim_events += int(event.get("event") == "claim")
        self.assertEqual(claim_events, 1)

        for path in runtime.rglob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(nonzero_formal_write_values(value), [], path)
        safety = {
            "real_terra_call_count": provider_call.call_count,
            "real_luna_call_count": provider_call.call_count,
            "real_provider_model_request_count": provider_call.call_count,
            "production_mcp_task_count": mcp_host.call_count,
            "formal_write_count": 0,
            "production_runtime_write_count": int(
                runtime.resolve().is_relative_to(
                    Path.home()
                    / ".codex/study-intake-preprocessor"
                )
            ),
        }
        self.assertTrue(all(value == 0 for value in safety.values()))
        return {
            "claim_count": claim_events,
            "runner_boundary_reached": True,
            **safety,
        }

    def test_math_actual_quick_intake_to_backend_runner_boundary(self) -> None:
        self.assert_canonical_main_sources(
            MATH_ROOT,
            (
                "数学一回滚复习系统/scripts/quick_intake.py",
                "数学一回滚复习系统/scripts/producer_binding_attestation.py",
            ),
        )
        source_tests = load_module(
            "real_math_quick_intake_tests",
            MATH_ROOT / "tests/test_quick_intake.py",
        )
        case = source_tests.QuickIntakeTests(methodName="runTest")
        case.setUp()
        try:
            payload = case.payload(
                attempt_id="warmup:WQ-real-shape:QI-real-shape",
                study_date="2026-08-22",
            )
            payload["episode_evidence"]["teaching_turns"] = [
                {
                    "speaker": "user",
                    "kind": "reasoning",
                    "text": "我先检查两个平面的法向量。",
                    "origin": "user_observed",
                },
                {
                    "speaker": "assistant",
                    "kind": "hint",
                    "text": "再核对交线方向与两个法向量的关系。",
                    "origin": "source_verified",
                },
            ]
            receipt = case.invoke_record(payload)
            copied_script = (
                case.base
                / "数学一回滚复习系统/scripts/quick_intake.py"
            )
            copied_script.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                MATH_ROOT / "数学一回滚复习系统/scripts/quick_intake.py",
                copied_script,
            )
            shutil.copy2(
                MATH_ROOT
                / "数学一回滚复习系统/scripts/producer_binding_attestation.py",
                copied_script.with_name("producer_binding_attestation.py"),
            )
            adapter = MathAdapter(
                {
                    "enabled": True,
                    "repo_root": str(case.base),
                    "status_script": str(copied_script),
                    "python_path": sys.executable,
                    "adapter_version": "actual-math-v1",
                    "deep_v2_mode": "production",
                    "max_images": 8,
                    "processing_contract": {
                        "processing_contract_sha256": PROCESSING_CONTRACT,
                        "adapter_build_sha256": "8" * 64,
                    },
                },
                {"status_timeout_seconds": 10},
            )
            worker = ActualAdapterWorker(
                "math",
                adapter,
                adapter.status,
                transform=adapter.deep_candidate,
            )
            runtime = case.base / "isolated-backend-runtime"
            task, decisions = self.scan_actual(
                runtime=runtime,
                subject="math",
                repo=case.base,
                worker=worker,
            )
            self.assertEqual(
                task.frozen_payload["capture_id"], receipt["event_id"]
            )
            unknown_status = copy.deepcopy(adapter.status(None))
            unknown_status["pending"][0]["future_display_field"] = True
            unknown_worker = ActualAdapterWorker(
                "math",
                adapter,
                lambda _date: unknown_status,
                transform=adapter.deep_candidate,
            )
            unknown_frozen, unknown_decisions = scan_eligible_candidates(
                self.scan_config(
                    case.base / "unknown-runtime", "math", case.base
                ),
                "math",
                worker_factory=lambda _config: unknown_worker,
                publish_evidence_readiness=False,
            )
            self.assertEqual(len(unknown_frozen), 1)
            self.assertEqual(
                unknown_frozen[0].candidate.capture_id, receipt["event_id"]
            )
            self.assertIn(
                "producer_unknown_fields_ignored",
                unknown_decisions[0]["normalization_warnings"],
            )
            raw_ledger_sha256 = hashlib.sha256(
                case.events_path.read_bytes()
            ).hexdigest()
            self.assertRegex(raw_ledger_sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(forbidden_control_keys(receipt), set())
            self.assert_runner_boundary_once(runtime, task)
            self.assertEqual(
                hashlib.sha256(case.events_path.read_bytes()).hexdigest(),
                raw_ledger_sha256,
            )

            replay = case.invoke_record(
                payload,
                name="capture-replay.json",
            )
            self.assertEqual(replay["status"], "noop")
            self.assertEqual(replay["event_id"], receipt["event_id"])
            self.assertEqual(
                [row["capture_id"] for row in decisions if row["eligible"]],
                [receipt["event_id"]],
            )
            self.assert_actual_cutoff_excludes(
                runtime=case.base / "cutoff-runtime",
                subject="math",
                repo=case.base,
                worker=worker,
            )
        finally:
            case.doCleanups()

    def test_cs408_actual_managed_and_ordinary_producers_to_backend(self) -> None:
        self.assert_canonical_main_sources(
            CS408_ROOT,
            (
                "scripts/managed_408_current_turn.py",
                "scripts/intake_fact_capture_408.py",
                "scripts/capture_hot_writer_408.py",
            ),
        )
        managed_tests = load_module(
            "real_cs408_managed_tests",
            CS408_ROOT
            / "tests/test_morning_review_prepared_pack_managed_hot_408.py",
            import_root=CS408_ROOT,
        )
        managed = managed_tests.PreparedPackManagedHotPath408Tests(
            methodName="runTest"
        )
        managed.setUp()
        try:
            private_root = Path(managed.tmp.name) / "private-real-shape"
            shown = managed_tests.prepared_pack.show_item(
                managed.repo,
                managed_tests.SESSION_ID,
                managed_tests.ITEM_ID,
            )
            prepared = managed_tests.prepared_pack.prepare_current_turn(
                managed.repo,
                session_id=managed_tests.SESSION_ID,
                item_id=managed_tests.ITEM_ID,
                choice="C",
                confidence="high",
                prompt_level="L3",
                request_id="real-shape-managed-408",
                event_time="2026-08-22T10:30:00+08:00",
                display_surface_sha256=shown["surface_sha256"],
                private_root=private_root,
            )
            capsule = managed_tests.current_evidence.read_evaluation_capsule(
                prepared["grader_capsule_locator"],
                expected_sha256=prepared["grader_capsule_sha256"],
                private_root=private_root,
            )
            receipt = managed_tests.current_turn.run_current_question_turn(
                managed.repo,
                prepared["context"],
                feedback_text="提示后完成的隔离 real-shape 回归反馈。",
                private_evaluation=capsule["evaluation_evidence"],
                private_root=private_root,
            )
            _handoff_binding, handoff = (
                managed_tests.current_evidence.read_background_handoff_for_capture(
                    receipt["capture_id"], private_root=private_root
                )
            )
            resolution = json.loads(
                (
                    private_root
                    / "turns"
                    / f"{handoff['resolution_receipt_sha256']}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(resolution),
                {
                    "schema",
                    "status",
                    "attestation_schema",
                    "context_id",
                    "session_id",
                    "item_id",
                    "capture_id",
                    "capture_receipt_sha256",
                    "evidence_manifest_sha256",
                    "buffer_freeze_receipt_sha256",
                    "interaction_trace_sha256",
                    "event_time",
                    "advance_allowed",
                    "formal_write_count",
                },
                resolution,
            )
            status_script = CS408_ROOT / "scripts/intake_fact_capture_408.py"
            adapter = Cs408Adapter(
                {
                    "enabled": True,
                    "repo_root": str(managed.repo),
                    "status_script": str(status_script),
                    "python_path": sys.executable,
                    "adapter_version": "actual-cs408-v1",
                    "deep_v2_enabled": True,
                    "processing_contract": {
                        "processing_contract_sha256": PROCESSING_CONTRACT,
                        "status_script_sha256": hashlib.sha256(
                            status_script.read_bytes()
                        ).hexdigest(),
                        "controlled_contract_sha256": hashlib.sha256(
                            (
                                ROOT
                                / "schemas/luna-cs408-controlled-contract-v3.json"
                            ).read_bytes()
                        ).hexdigest(),
                    },
                    "private_current_question_root": str(private_root),
                    "controlled_contract_path": str(
                        ROOT / "schemas/luna-cs408-controlled-contract-v3.json"
                    ),
                    "max_bundle_bytes": 262144,
                },
                {"status_timeout_seconds": 10},
            )
            worker = ActualAdapterWorker("cs408", adapter, adapter.status)
            runtime = Path(managed.tmp.name) / "isolated-backend-runtime"
            task, _decisions = self.scan_actual(
                runtime=runtime,
                subject="cs408",
                repo=managed.repo,
                worker=worker,
            )
            self.assertEqual(
                task.frozen_payload["capture_id"], receipt["capture_id"]
            )
            unknown_status = copy.deepcopy(adapter.status(None))
            unknown_status["captures"][receipt["capture_id"]][
                "future_display_field"
            ] = True
            unknown_worker = ActualAdapterWorker(
                "cs408", adapter, lambda _date: unknown_status
            )
            unknown_frozen, unknown_decisions = scan_eligible_candidates(
                self.scan_config(
                    Path(managed.tmp.name) / "unknown-runtime",
                    "cs408",
                    managed.repo,
                ),
                "cs408",
                worker_factory=lambda _config: unknown_worker,
                publish_evidence_readiness=False,
            )
            self.assertEqual(len(unknown_frozen), 1)
            self.assertIn(
                "producer_unknown_fields_ignored",
                unknown_decisions[0]["normalization_warnings"],
            )
            capture_preimage = hashlib.sha256(
                managed.capture_ledger.read_bytes()
            ).hexdigest()
            private_preimage = tree_sha256(private_root)
            self.assertEqual(forbidden_control_keys(receipt), set())
            self.assert_runner_boundary_once(runtime, task)
            self.assertEqual(
                hashlib.sha256(managed.capture_ledger.read_bytes()).hexdigest(),
                capture_preimage,
            )
            self.assertEqual(tree_sha256(private_root), private_preimage)
            self.assert_actual_cutoff_excludes(
                runtime=Path(managed.tmp.name) / "cutoff-runtime",
                subject="cs408",
                repo=managed.repo,
                worker=worker,
            )
        finally:
            managed.tearDown()

        ordinary_tests = load_module(
            "real_cs408_ordinary_tests",
            CS408_ROOT / "tests/test_intake_fact_capture_408.py",
            import_root=CS408_ROOT,
        )
        ordinary_tests.IntakeFactCaptureTests.setUpClass()
        ordinary = ordinary_tests.IntakeFactCaptureTests(methodName="runTest")
        try:
            with tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary).resolve()
                payload = ordinary._write(repo, ordinary._payload())
                receipt = ordinary_tests.capture.capture(
                    payload, repo_root=repo
                )
                replay = ordinary_tests.capture.capture(
                    payload, repo_root=repo
                )
                self.assertEqual(replay["status"], "ALREADY_CAPTURED")
                self.assertEqual(replay["capture_id"], receipt["capture_id"])
                status_script = (
                    CS408_ROOT / "scripts/intake_fact_capture_408.py"
                )
                adapter = Cs408Adapter(
                    {
                        "enabled": True,
                        "repo_root": str(repo),
                        "status_script": str(status_script),
                        "python_path": sys.executable,
                        "adapter_version": "actual-cs408-ordinary-v1",
                        "deep_v2_enabled": True,
                        "processing_contract": {
                            "processing_contract_sha256": PROCESSING_CONTRACT,
                            "status_script_sha256": hashlib.sha256(
                                status_script.read_bytes()
                            ).hexdigest(),
                            "controlled_contract_sha256": hashlib.sha256(
                                (
                                    ROOT
                                    / "schemas/luna-cs408-controlled-contract-v3.json"
                                ).read_bytes()
                            ).hexdigest(),
                        },
                        "private_current_question_root": str(
                            repo / "private-current-question"
                        ),
                        "controlled_contract_path": str(
                            ROOT
                            / "schemas/luna-cs408-controlled-contract-v3.json"
                        ),
                    },
                    {"status_timeout_seconds": 10},
                )
                worker = ActualAdapterWorker(
                    "cs408", adapter, adapter.status
                )
                frozen, decisions = scan_eligible_candidates(
                    self.scan_config(
                        repo / "isolated-backend-runtime", "cs408", repo
                    ),
                    "cs408",
                    worker_factory=lambda _config: worker,
                    publish_evidence_readiness=False,
                )
                self.assertEqual(frozen, [])
                matching = next(
                    row
                    for row in decisions
                    if row.get("capture_id") == receipt["capture_id"]
                )
                self.assertFalse(matching["eligible"])
                self.assertEqual(matching["formal_write_count"], 0)
        finally:
            ordinary_tests.IntakeFactCaptureTests.tearDownClass()

    def test_english_actual_immutable_closed_and_quick_flush_to_backend(self) -> None:
        self.assert_canonical_main_sources(
            ENGLISH_ROOT,
            (
                "english_pipeline/cli.py",
                "english_pipeline/events.py",
                "english_pipeline/views.py",
                "english_pipeline/quick_flush.py",
            ),
        )
        source_tests = load_module(
            "real_english_pipeline_tests",
            ENGLISH_ROOT / "tests/english_pipeline/test_pipeline.py",
            import_root=ENGLISH_ROOT,
        )
        adapter_case = backend_english_tests.EnglishAdapterTests(
            methodName="runTest"
        )
        adapter_case.setUp()
        try:
            article_id = "RAW-REAL-SHAPE-001"
            sentences = [
                f"A practice-safe real-shape sentence {index}."
                for index in range(1, 6)
            ]
            article_path, _locator, source_hash = (
                source_tests.make_source_object(
                    adapter_case.repo,
                    source_id=article_id,
                    slug="real-shape",
                    sentences=sentences,
                )
            )
            source_tests.make_formal_repo(adapter_case.repo)
            receipts: list[dict[str, Any]] = []
            first_capture_args: list[str] | None = None
            for index, sentence in enumerate(sentences, 1):
                raw_request_path = (
                    adapter_case.base / f"raw-turn-{index}.json"
                )
                raw_request_path.write_text(
                    json.dumps(
                        {
                            "event_type": "english_raw_dialogue_turn_v1",
                            "idempotency_key": f"real-shape-raw-{index}",
                            "occurred_at": f"2026-08-22T09:{index:02d}:00Z",
                            "messages": [
                                {
                                    "role": "user",
                                    "message_id": f"real-shape-user-{index}",
                                    "timestamp": f"2026-08-22T09:{index:02d}:00Z",
                                    "content": f"Practice-safe turn {index}.",
                                },
                                {
                                    "role": "assistant",
                                    "message_id": f"real-shape-assistant-{index}",
                                    "timestamp": f"2026-08-22T09:{index:02d}:30Z",
                                    "content": f"Practice-safe reply {index}.",
                                    "complete": True,
                                },
                            ],
                            "attachments": [],
                            "context_identity": {
                                "conversation_id": "real-shape-conversation",
                                "thread_id": "real-shape-thread",
                                "workspace_id": "real-shape-workspace",
                                "assistant_context_id": "real-shape-assistant-context",
                            },
                            "resolution_status": "resolved",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                raw_code, raw_receipt, raw_stderr = source_tests.run_cli(
                    [
                        "capture-raw-turn",
                        "--repo-root",
                        str(adapter_case.repo),
                        "--state-dir",
                        str(adapter_case.state),
                        "--input-json",
                        str(raw_request_path),
                    ]
                )
                self.assertEqual((raw_code, raw_stderr), (0, ""))
                assert isinstance(raw_receipt, dict)
                capture_args = [
                    "capture",
                    "--repo-root",
                    str(adapter_case.repo),
                    "--state-dir",
                    str(adapter_case.state),
                    "--idempotency-key",
                    f"real-shape-{index}",
                    "--source-id",
                    article_id,
                    "--source-article",
                    str(article_path),
                    "--article-sha256",
                    source_hash,
                    "--sentence-id",
                    f"S{index:02d}",
                    "--source-sentence",
                    sentence,
                    "--occurred-at",
                    f"2026-08-22T10:{index:02d}:00Z",
                    "--parent-raw-capture-id",
                    str(raw_receipt["capture_id"]),
                ]
                if index == 1:
                    capture_args.append("--quick-flush")
                    first_capture_args = list(capture_args)
                code, receipt, stderr = source_tests.run_cli(
                    capture_args
                )
                self.assertEqual((code, stderr), (0, ""))
                assert isinstance(receipt, dict)
                receipts.append(receipt)

            assert first_capture_args is not None
            replay_code, replay_receipt, replay_stderr = source_tests.run_cli(
                first_capture_args
            )
            self.assertEqual((replay_code, replay_stderr), (0, ""))
            assert isinstance(replay_receipt, dict)
            self.assertTrue(replay_receipt["replayed"])
            self.assertEqual(
                replay_receipt["capture_id"], receipts[0]["capture_id"]
            )

            events = source_tests.load_events(adapter_case.state)
            sentence_events = [
                row for row in events if row["event_type"] == "sentence_captured"
            ]
            completion_code, completion, completion_stderr = (
                source_tests.run_cli(
                    [
                        "complete-article",
                        "--repo-root",
                        str(adapter_case.repo),
                        "--state-dir",
                        str(adapter_case.state),
                        "--source-id",
                        article_id,
                        "--idempotency-key",
                        "real-shape-complete",
                        "--date",
                        "2026-08-22",
                    ]
                )
            )
            self.assertEqual(
                (completion_code, completion_stderr), (0, "")
            )
            assert isinstance(completion, dict)
            quick_flush = receipts[0]["quick_flush"]
            english_config = copy.deepcopy(
                adapter_case.config["adapters"]["english"]
            )
            english_config["processing_contract"] = {
                "processing_contract_sha256": PROCESSING_CONTRACT,
                "requested_model": "gpt-5.6-luna",
                "requested_reasoning_effort": "max",
            }
            adapter = EnglishAdapter(
                english_config, adapter_case.config["worker"]
            )
            adapter._event_normalization_warnings = {}
            future_event = {
                **sentence_events[0],
                "future_display_field": True,
            }
            normalized_future_event = adapter._validate_event(future_event)
            self.assertEqual(
                normalized_future_event["event_id"],
                sentence_events[0]["event_id"],
            )
            self.assertIn(
                "producer_unknown_fields_ignored",
                adapter._event_normalization_warnings[
                    sentence_events[0]["event_id"]
                ],
            )
            worker = ActualAdapterWorker(
                "english", adapter, adapter.status
            )
            runtime = adapter_case.base / "isolated-backend-runtime"
            events_preimage = tree_sha256(adapter_case.state / "events")
            frozen, decisions = scan_eligible_candidates(
                self.scan_config(
                    runtime, "english", adapter_case.repo
                ),
                "english",
                worker_factory=lambda _config: worker,
                publish_evidence_readiness=False,
            )
            self.assertEqual(len(frozen), 2, decisions)
            self.assertEqual(
                sorted(
                    row.task.frozen_payload["input_binding"][
                        "batch_trigger"
                    ]
                    for row in frozen
                ),
                ["article_completed", "explicit_quick_intake"],
            )
            capture_ids = {
                capture_id
                for row in frozen
                for capture_id in row.task.frozen_payload[
                    "input_binding"
                ][
                    "capture_event_ids"
                ]
            }
            self.assertEqual(
                capture_ids,
                {
                    *(row["event_id"] for row in sentence_events),
                    completion["completion_event_id"],
                },
            )
            self.assertEqual(completion["formal_write_count"], 0)
            self.assertEqual(quick_flush["formal_write_count"], 0)
            for row in frozen:
                self.assert_runner_boundary_once(runtime, row.task)
            self.assertEqual(
                tree_sha256(adapter_case.state / "events"),
                events_preimage,
            )
            self.assertEqual(forbidden_control_keys(completion), set())
            self.assertEqual(forbidden_control_keys(quick_flush), set())
            self.assert_actual_cutoff_excludes(
                runtime=adapter_case.base / "cutoff-runtime",
                subject="english",
                repo=adapter_case.repo,
                worker=worker,
            )
        finally:
            adapter_case.tearDown()


if __name__ == "__main__":
    unittest.main()
