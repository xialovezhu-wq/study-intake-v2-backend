from __future__ import annotations

import copy
import datetime as dt
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
ACTUAL_ROOTS = {
    "math": Path(
        os.environ.get(
            "STUDY_INTAKE_ACTUAL_MATH_ROOT",
            Path.home() / "Documents/kaoyan-math",
        )
    ),
    "cs408": Path(
        os.environ.get(
            "STUDY_INTAKE_ACTUAL_CS408_ROOT",
            Path.home() / "Documents/kaoyan-408",
        )
    ),
    "english": Path(
        os.environ.get(
            "STUDY_INTAKE_ACTUAL_ENGLISH_ROOT",
            Path.home() / "Documents/kaoyan-english",
        )
    ),
}
DESCRIPTOR_RELATIVE_PATHS = {
    "math": Path("数学一回滚复习系统/schema/producer-binding-v1.json"),
    "cs408": Path("schema/producer-binding-v1.json"),
    "english": Path("schema/english_pipeline/producer-binding-v1.json"),
}
for directory in (ROOT / "lib", ROOT / "bin", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from concurrent_dispatch import (  # noqa: E402
    ConcurrentDispatcher,
    FrozenTask,
    LeaseStore,
    StageResult,
)
from core_dispatch_bridge import scan_eligible_candidates  # noqa: E402
from preprocessor_core import (  # noqa: E402
    Candidate,
    Cs408Adapter,
    EnglishAdapter,
    LOADED_CORE_SHA256,
    MathAdapter,
)
from preprocess_dispatcher import ProductionDispatchRuntime  # noqa: E402
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
ACTUAL_PRODUCERS_AVAILABLE = all(
    (root / DESCRIPTOR_RELATIVE_PATHS[subject]).is_file()
    for subject, root in ACTUAL_ROOTS.items()
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


class ImmediateZeroModelRunner:
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

    def run_analysis(
        self, task: FrozenTask, _context: object
    ) -> StageResult:
        return self.stage("analysis", task)

    def run_critical_review(
        self,
        task: FrozenTask,
        _draft: Mapping[str, Any],
        _context: object,
    ) -> StageResult:
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


def canonical_lf_sha256(value: Any) -> str:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_descriptor(subject: str) -> tuple[Path, dict[str, Any]]:
    root = ACTUAL_ROOTS[subject]
    path = root / DESCRIPTOR_RELATIVE_PATHS[subject]
    if not path.is_file():
        raise AssertionError(f"H4_SKIPPED_MISSING_ACTUAL_ROOT:{subject}")
    value = json.loads(path.read_text(encoding="utf-8"))
    core = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != "descriptor_content_sha256"
    }
    if (
        value.get("schema_version") != "producer_binding_descriptor_v1"
        or value.get("subject") != subject
        or value.get("formal_write_count") != 0
        or value.get("descriptor_content_sha256")
        != canonical_lf_sha256(core)
    ):
        raise AssertionError(f"actual descriptor invalid: {subject}")
    source_rows = value.get("producer", {}).get("source_files")
    if not isinstance(source_rows, list) or not source_rows:
        raise AssertionError(f"actual source closure missing: {subject}")
    normalized_sources = []
    for row in source_rows:
        source_path = Path(str(row.get("path") or ""))
        if (
            not source_path.is_file()
            or sha256_file(source_path) != row.get("sha256")
        ):
            raise AssertionError(f"actual source drift: {subject}:{source_path}")
        normalized_sources.append(
            {"path": str(source_path), "sha256": row["sha256"]}
        )
    if value["producer"].get("source_closure_sha256") != canonical_lf_sha256(
        normalized_sources
    ):
        raise AssertionError(f"actual source closure invalid: {subject}")
    contract_rows = value.get("capture_contract", {}).get("files")
    if not isinstance(contract_rows, list) or not contract_rows:
        raise AssertionError(f"actual capture contract missing: {subject}")
    for row in contract_rows:
        contract_path = Path(str(row.get("path") or ""))
        if (
            not contract_path.is_file()
            or sha256_file(contract_path) != row.get("sha256")
        ):
            raise AssertionError(
                f"actual capture contract drift: {subject}:{contract_path}"
            )
    return path, value


def install_actual_descriptor_overlay(
    subject: str, fixture_root: Path
) -> dict[str, Any]:
    fixture_root = fixture_root.resolve()
    actual_root = ACTUAL_ROOTS[subject].resolve()
    actual_path, descriptor = actual_descriptor(subject)
    source_preimage = {
        str(Path(row["path"])): sha256_file(Path(row["path"]))
        for row in descriptor["producer"]["source_files"]
    }
    overlay = copy.deepcopy(descriptor)
    source_rows: list[dict[str, str]] = []
    for row in descriptor["producer"]["source_files"]:
        source = Path(row["path"])
        relative = source.relative_to(actual_root)
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_rows.append(
            {"path": str(target), "sha256": sha256_file(target)}
        )
    overlay["producer"]["source_files"] = source_rows
    overlay["producer"]["source_closure_sha256"] = canonical_lf_sha256(
        source_rows
    )
    binding_root = fixture_root / ".h4-production-binding"
    skill_rows = overlay["foreground_skill"]
    for role in ("authoritative", "installed"):
        source = Path(skill_rows[f"{role}_path"])
        target = binding_root / "skills" / role / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        skill_rows[f"{role}_path"] = str(target)
        skill_rows[f"{role}_sha256"] = sha256_file(target)
    contract_rows: list[dict[str, str]] = []
    for index, row in enumerate(descriptor["capture_contract"]["files"]):
        source = Path(row["path"])
        target = binding_root / "contracts" / f"{index:02d}-{source.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        contract_rows.append(
            {"path": str(target), "sha256": sha256_file(target)}
        )
    overlay["capture_contract"]["files"] = contract_rows
    overlay["capture_contract"]["files_sha256"] = canonical_lf_sha256(
        contract_rows
    )
    overlay_core = {
        key: copy.deepcopy(value)
        for key, value in overlay.items()
        if key != "descriptor_content_sha256"
    }
    overlay["descriptor_content_sha256"] = canonical_lf_sha256(
        overlay_core
    )
    descriptor_path = fixture_root / DESCRIPTOR_RELATIVE_PATHS[subject]
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(
        json.dumps(overlay, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    attestation_root = fixture_root / str(
        overlay["attestation_relative_root"]
    )
    if attestation_root.exists():
        shutil.rmtree(attestation_root)
    lock = {
        "foreground_capture_contracts": {
            subject: {
                "descriptor_path": str(descriptor_path),
                "descriptor_sha256": sha256_file(descriptor_path),
                "attestation_required_after": overlay[
                    "attestation_required_after"
                ],
                "producer_source_closure_sha256": overlay["producer"][
                    "source_closure_sha256"
                ],
                "foreground_skill_sha256": overlay["foreground_skill"][
                    "installed_sha256"
                ],
            }
        },
        "formal_write_count": 0,
    }
    lock_path = binding_root / "component-lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "actual_descriptor_path": actual_path,
        "actual_descriptor_sha256": sha256_file(actual_path),
        "descriptor_path": descriptor_path,
        "component_lock_path": lock_path,
        "source_preimage": source_preimage,
    }


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
    SUBJECT_REPOS_AVAILABLE and ACTUAL_PRODUCERS_AVAILABLE,
    (
        "H4_SKIPPED_MISSING_ACTUAL_ROOT: canonical and actual Math, CS408, "
        "and English Producer repositories are required"
    ),
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

    def assert_actual_production_runtime_once(
        self,
        *,
        subject: str,
        fixture_root: Path,
        worker: ActualAdapterWorker,
        target_capture_id: str,
        overlay: Mapping[str, Any],
    ) -> dict[str, Any]:
        runtime_root = (
            fixture_root.parent
            / f".{fixture_root.name}-h4-{subject}-runtime"
        )
        release_manifest = (
            fixture_root.parent
            / f".{fixture_root.name}-h4-{subject}-release.json"
        )
        release_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "study-intake-preprocessor-release-v2",
                    "release_id": RELEASE_ID,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        profile_name = {
            "math": "math_deep_v2",
            "cs408": "cs408_deep_v2",
            "english": "english_two_pass_v1",
        }[subject]
        config = {
            "execution_mode": "live_authorized",
            "runtime_root": str(runtime_root),
            "timezone": "Asia/Shanghai",
            "release": {"manifest_path": str(release_manifest)},
            "model": {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
            "adapters": {
                subject: {
                    "enabled": True,
                    "repo_root": str(fixture_root),
                }
            },
            "dispatch": {
                "production_canary": {
                    "enabled": True,
                    "status": "production_canary_active",
                    "admission": "first_post_activation_producer_capture",
                    "keep_backlog_drained": True,
                    "post_activation_only": True,
                    "initial_canary_inflight_limit": 1,
                    "continuous_concurrency_limit": 20,
                }
            },
            profile_name: {
                "soft_runtime_warning_seconds": 60,
                "stall_timeout_seconds": 60,
                "stall_probe_interval_seconds": 1,
                "stall_probe_required_consecutive_failures": 2,
            },
        }
        runtime = ProductionDispatchRuntime(
            config,
            subject,
            fixture_root.parent / f"h4-{subject}-config.json",
            scan_worker_factory=lambda _config: worker,
            ordinary_local_capture=True,
        )
        runtime.config["processing_plugin"] = {
            "component_lock_path": str(overlay["component_lock_path"])
        }
        available = worker.eligible_candidates(
            subject,
            "2026-08-22",
            capture_allowlist=None,
            controlled_replay=False,
        )
        target = next(
            row
            for row, _reason in available
            if row.capture_id == target_capture_id
        )
        exact_allowlist = (
            frozenset(
                str(capture_id)
                for capture_id in target.input_binding.get(
                    "capture_event_ids", []
                )
            )
            if subject == "english"
            and isinstance(
                target.input_binding.get("capture_event_ids"), list
            )
            else frozenset({target_capture_id})
        )
        self.assertTrue(exact_allowlist)
        processing_contract_sha256 = target.input_binding.get(
            "processing_contract_sha256"
        )
        self.assertIsInstance(processing_contract_sha256, str)
        self.assertIsInstance(target.recorded_at, str)
        target_recorded_at = dt.datetime.fromisoformat(
            str(target.recorded_at).replace("Z", "+00:00")
        )
        self.assertIsNotNone(target_recorded_at.utcoffset())
        activated_at = (
            target_recorded_at - dt.timedelta(seconds=1)
        ).isoformat()
        authority_core = {
            "schema_version": "study-intake-producer-authority-v1",
            "subject": subject,
            "release_id": RELEASE_ID,
            "loaded_core_sha256": LOADED_CORE_SHA256,
            "processing_contract_sha256": processing_contract_sha256,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "formal_write_count": 0,
        }
        producer_authority = {
            **authority_core,
            "authority_fingerprint": hashlib.sha256(
                json.dumps(
                    authority_core,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        store = runtime.dispatcher.lease_store
        store.begin_subject_drain(subject)
        store.activate_production_canary(
            subject,
            release_id=RELEASE_ID,
            producer_authority=producer_authority,
            activated_at=activated_at,
            continuous_concurrency_limit=20,
        )
        runtime.dispatcher.runner_factory = (
            lambda _task, _context: ImmediateZeroModelRunner()
        )
        fixture_preimage = tree_sha256(fixture_root)
        source_preimage = dict(overlay["source_preimage"])
        with (
            mock.patch(
                "preprocessor_core.CodexRunner._invoke_subprocess",
                side_effect=AssertionError("real Provider path invoked"),
            ) as provider_call,
            mock.patch(
                "preprocess_dispatcher.ProcessingPluginHost",
                side_effect=AssertionError("production MCP host invoked"),
            ) as mcp_host,
            mock.patch("preprocess_dispatcher._write_subject_projections"),
        ):
            handles, decisions = runtime.scan_and_submit(
                capture_allowlist=exact_allowlist
            )
            self.assertEqual(len(handles), 1, decisions)
            self.assertTrue(runtime.dispatcher.drain(timeout=5))
            results = [handle.wait(1) for handle in handles]
            projected = runtime.persist_finished_luna(handles)
        provider_call.assert_not_called()
        mcp_host.assert_not_called()
        self.assertEqual(len(projected), 1)
        self.assertEqual(results[0].outcome, "succeeded")
        completion = results[0].completion
        assert isinstance(completion, Mapping)
        self.assertEqual(completion["capture_id"], target_capture_id)
        self.assertTrue(completion["package_ref"])
        self.assertTrue(completion["report_json_ref"])
        self.assertTrue(Path(completion["package_path"]).is_file())
        verified = store.verify_authoritative_completion(
            subject,
            target_capture_id,
            expected_release_id=RELEASE_ID,
            expected_unit_sha256=results[0].unit_sha256,
        )
        self.assertEqual(
            verified["completion"]["report_json_ref"],
            completion["report_json_ref"],
        )
        history = store.verify_task_event_history(
            results[0].unit_sha256,
            expected_release_id=RELEASE_ID,
        )
        self.assertEqual(
            sum(
                row["event"].get("event") == "claim"
                for row in history["events"]
            ),
            1,
        )
        self.assertEqual(tree_sha256(fixture_root), fixture_preimage)
        for raw_path, digest in source_preimage.items():
            self.assertEqual(sha256_file(Path(raw_path)), digest)
        for path in runtime_root.rglob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(nonzero_formal_write_values(value), [], path)
        return {
            "subject": subject,
            "capture_id": target_capture_id,
            "claim_count": 1,
            "provider_request_count": 0,
            "mcp_tool_call_count": 0,
            "formal_write_count": 0,
            "actual_descriptor_sha256": overlay[
                "actual_descriptor_sha256"
            ],
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
            capture_count = (
                2
                if os.environ.get("STUDY_MINIMUM_PIPELINE_TWO_CAPTURES") == "1"
                else 1
            )
            payloads: list[dict[str, Any]] = []
            receipts: list[dict[str, Any]] = []
            for index in range(capture_count):
                payload = case.payload(
                    attempt_id=(
                        "warmup:WQ-real-shape:QI-real-shape"
                        if capture_count == 1
                        else f"warmup:WQ-real-shape:QI-real-shape-{index + 1:02d}"
                    ),
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
                payloads.append(payload)
                receipts.append(
                    case.invoke_record(
                        payload,
                        name=(
                            "capture.json"
                            if capture_count == 1
                            else f"capture-{index + 1:02d}.json"
                        ),
                    )
                )
            payload = payloads[0]
            receipt = receipts[0]
            target_capture_ids = [row["event_id"] for row in receipts]
            self.assertEqual(len(set(target_capture_ids)), capture_count)
            overlay = install_actual_descriptor_overlay("math", case.base)
            copied_script = (
                case.base
                / "数学一回滚复习系统/scripts/quick_intake.py"
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
            target_kwargs = (
                {"target_capture_ids": target_capture_ids}
                if capture_count == 2
                else {"target_capture_id": receipt["event_id"]}
            )
            h4 = self.assert_actual_production_runtime_once(
                subject="math",
                fixture_root=case.base,
                worker=worker,
                overlay=overlay,
                **target_kwargs,
            )
            self.assertEqual(h4["provider_request_count"], 0)
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
            two_captures = (
                os.environ.get("STUDY_MINIMUM_PIPELINE_TWO_CAPTURES") == "1"
            )
            turns = [
                {
                    "item_id": managed_tests.ITEM_ID,
                    "choice": "C",
                    "prompt_level": "L3",
                    "request_id": "real-shape-managed-408",
                    "event_time": "2026-08-22T10:30:00+08:00",
                    "feedback_text": "提示后完成的隔离 real-shape 回归反馈。",
                }
            ]
            if two_captures:
                turns.append(
                    {
                        "item_id": "MQ-02",
                        "choice": "B",
                        "prompt_level": "none",
                        "request_id": "real-shape-managed-408-02",
                        "event_time": "2026-08-22T10:31:00+08:00",
                        "feedback_text": "独立完成后继边界的隔离 real-shape 回归反馈。",
                    }
                )
            receipts: list[dict[str, Any]] = []
            for turn in turns:
                shown = managed_tests.prepared_pack.show_item(
                    managed.repo,
                    managed_tests.SESSION_ID,
                    turn["item_id"],
                )
                prepared = managed_tests.prepared_pack.prepare_current_turn(
                    managed.repo,
                    session_id=managed_tests.SESSION_ID,
                    item_id=turn["item_id"],
                    choice=turn["choice"],
                    confidence="high",
                    prompt_level=turn["prompt_level"],
                    request_id=turn["request_id"],
                    event_time=turn["event_time"],
                    display_surface_sha256=shown["surface_sha256"],
                    private_root=private_root,
                )
                capsule = managed_tests.current_evidence.read_evaluation_capsule(
                    prepared["grader_capsule_locator"],
                    expected_sha256=prepared["grader_capsule_sha256"],
                    private_root=private_root,
                )
                captured = managed_tests.current_turn.run_current_question_turn(
                    managed.repo,
                    prepared["context"],
                    feedback_text=turn["feedback_text"],
                    private_evaluation=capsule["evaluation_evidence"],
                    private_root=private_root,
                )
                receipts.append(captured)
                _handoff_binding, handoff = (
                    managed_tests.current_evidence.read_background_handoff_for_capture(
                        captured["capture_id"], private_root=private_root
                    )
                )
                resolution = json.loads(
                    (
                        private_root
                        / "turns"
                        / f"{handoff['resolution_receipt_sha256']}.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(resolution["item_id"], turn["item_id"])
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
            receipt = receipts[0]
            target_capture_ids = [row["capture_id"] for row in receipts]
            self.assertEqual(len(set(target_capture_ids)), len(receipts))
            overlay = install_actual_descriptor_overlay(
                "cs408", managed.repo
            )
            for name in (
                "intake_lib_408.py",
                "bounded_jsonl_index_408.py",
                "linked_practice_source_408.py",
                "question_source_attestation_408.py",
                "review_feedback_loop.py",
            ):
                source = ACTUAL_ROOTS["cs408"] / "scripts" / name
                target = managed.repo / "scripts" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            status_script = managed.repo / "scripts/intake_fact_capture_408.py"
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
            target_kwargs = (
                {"target_capture_ids": target_capture_ids}
                if two_captures
                else {"target_capture_id": receipt["capture_id"]}
            )
            h4 = self.assert_actual_production_runtime_once(
                subject="cs408",
                fixture_root=managed.repo,
                worker=worker,
                overlay=overlay,
                **target_kwargs,
            )
            self.assertEqual(h4["mcp_tool_call_count"], 0)
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
            overlay = install_actual_descriptor_overlay(
                "english", adapter_case.repo
            )
            for source in sorted(
                (ACTUAL_ROOTS["english"] / "english_pipeline").glob(
                    "*.py"
                )
            ):
                target = adapter_case.repo / "english_pipeline" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            for relative in (
                "scripts/build_old_word_memory_curve_index.py",
                "scripts/build_review_status_proposals.py",
                "scripts/select_bbdc_foundation.py",
            ):
                source = ACTUAL_ROOTS["english"] / relative
                target = adapter_case.repo / relative
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    self.assertEqual(sha256_file(target), sha256_file(source))
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
            quick_flush_candidate = next(
                row.candidate
                for row in frozen
                if row.task.frozen_payload["input_binding"].get(
                    "batch_trigger"
                )
                == "explicit_quick_intake"
            )
            two_captures = (
                os.environ.get("STUDY_MINIMUM_PIPELINE_TWO_CAPTURES") == "1"
            )
            target_capture_ids = [quick_flush_candidate.capture_id]
            if two_captures:
                completed_candidate = next(
                    row.candidate
                    for row in frozen
                    if row.task.frozen_payload["input_binding"].get(
                        "batch_trigger"
                    )
                    == "article_completed"
                )
                target_capture_ids.append(completed_candidate.capture_id)
                self.assertEqual(len(set(target_capture_ids)), 2)
            target_kwargs = (
                {"target_capture_ids": target_capture_ids}
                if two_captures
                else {"target_capture_id": quick_flush_candidate.capture_id}
            )
            h4 = self.assert_actual_production_runtime_once(
                subject="english",
                fixture_root=adapter_case.repo,
                worker=worker,
                overlay=overlay,
                **target_kwargs,
            )
            self.assertEqual(h4["formal_write_count"], 0)
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
