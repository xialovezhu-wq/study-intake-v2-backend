from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for directory in (
    ROOT / "lib",
    ROOT / "bin",
    ROOT / "tests",
    ROOT / "dashboard",
):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from concurrent_dispatch import (  # noqa: E402
    ConcurrentDispatcher,
    DispatchError,
    FrozenTask,
    LeaseStore,
    StageResult,
    dispatch_rule_binding,
)
from core_dispatch_bridge import (  # noqa: E402
    CoreCandidateRunner,
    EligibleFrozenCandidate,
    _required_producer_attestation_missing,
    scan_eligible_candidates,
)
from fixtures.scanner_worker_fake import make_scanner_worker_factory  # noqa: E402
from live_execution_gate import (  # noqa: E402
    LiveExecutionDenied,
    assert_external_launch_allowed,
)
from preprocessor_core import (  # noqa: E402
    Candidate,
    LOADED_CORE_SHA256,
    ModelResult,
    release_identity,
    sha256_value,
)
from preprocess_dispatcher import (  # noqa: E402
    ProductionDispatchRuntime,
    _run_once,
)
import server as dashboard_server  # noqa: E402
from tests.test_foreground_skill_binding_v3 import (  # noqa: E402
    _build_subject_fixture,
)
from tests.test_concurrent_dispatch import core_candidate  # noqa: E402


RELEASE_ID = "a" * 64
PROCESSING_CONTRACT = "9" * 64


def candidate(subject: str, capture_id: str, recorded_at: str) -> Candidate:
    return Candidate(
        subject=subject,
        capture_id=capture_id,
        study_date="2026-08-22",
        recorded_at=recorded_at,
        input_fingerprint=(capture_id.lower().replace("-", "") + "0" * 64)[:64],
        input_binding={
            "processing_contract_sha256": PROCESSING_CONTRACT,
            "submission_capture_id": capture_id,
        },
        model_input={"capture": {"value": capture_id}},
        allowed_evidence_refs=("capture.value",),
        image_paths=(),
        target_label=capture_id,
        canonical_state="awaiting_background_analysis",
        sol_state="pending_review",
    )


class LocalTrustedExecutionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name).resolve() / "runtime"
        self.runtime.mkdir(parents=True)
        self.subject_root = Path(self.temp.name).resolve() / "math"
        self.subject_root.mkdir()
        self.task = FrozenTask(
            {
                "subject": "math",
                "capture_id": "MFI-CAP-LOCAL-TRUSTED-001",
                "study_date": "2026-08-22",
                "recorded_at": "2026-08-22T12:00:00+08:00",
                "input_fingerprint": "1" * 64,
            }
        )
        self.owner_id = "local-dispatch-owner"
        decision = LeaseStore(self.runtime).claim(
            self.task.unit_sha256,
            self.owner_id,
            subject="math",
            task=self.task,
        )
        assert decision.lease is not None
        self.lease = decision.lease
        self.context = (
            self.runtime
            / "dispatch"
            / "contexts"
            / self.task.unit_sha256
            / f"fence-{self.lease.fence}"
        )
        self.context.mkdir(parents=True)
        self.config = {
            "execution_mode": "live_authorized",
            "runtime_root": str(self.runtime),
            "adapters": {
                "math": {
                    "enabled": True,
                    "repo_root": str(self.subject_root),
                }
            },
        }
        self.environment = {
            "STUDY_PREPROCESS_RUNTIME_ROOT": str(self.runtime),
            "STUDY_PREPROCESS_UNIT_SHA256": self.task.unit_sha256,
            "STUDY_PREPROCESS_LEASE_FENCE": str(self.lease.fence),
            "STUDY_PREPROCESS_LEASE_OWNER_ID": self.owner_id,
            "STUDY_PREPROCESS_CONTEXT_ROOT": str(self.context),
        }

    def call_gate(self, config: dict | None = None) -> dict:
        with mock.patch.dict(os.environ, self.environment, clear=False):
            return assert_external_launch_allowed(
                config or self.config,
                purpose="provider_model_request",
                command=[
                    "/Applications/ChatGPT.app/Contents/Resources/codex",
                    "exec",
                    "--model",
                    "gpt-5.6-terra",
                ],
            )

    def test_claimed_local_capture_does_not_require_manual_authorization(self) -> None:
        decision = self.call_gate()
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "local_trusted_claimed_capture")
        self.assertEqual(decision["subject"], "math")
        self.assertEqual(decision["unit_sha256"], self.task.unit_sha256)
        self.assertEqual(decision["formal_write_count"], 0)

    def test_local_trusted_policy_rejects_missing_claim_context_or_route(self) -> None:
        cases = {
            "missing_claim": {
                "STUDY_PREPROCESS_UNIT_SHA256": "f" * 64,
            },
            "missing_context": {
                "STUDY_PREPROCESS_CONTEXT_ROOT": str(
                    self.runtime / "dispatch" / "contexts" / "missing"
                ),
            },
        }
        for label, environment_update in cases.items():
            with self.subTest(label=label):
                environment = {**self.environment, **environment_update}
                with mock.patch.dict(os.environ, environment, clear=False):
                    with self.assertRaises(LiveExecutionDenied):
                        assert_external_launch_allowed(
                            self.config,
                            purpose="provider_model_request",
                            command=[
                                "/Applications/ChatGPT.app/Contents/Resources/codex",
                                "exec",
                            ],
                        )

        missing_route = json.loads(json.dumps(self.config))
        missing_route["adapters"] = {}
        with self.assertRaises(LiveExecutionDenied) as caught:
            self.call_gate(missing_route)
        self.assertEqual(caught.exception.code, "local_trusted_subject_route_missing")

    def test_local_trusted_policy_never_authorizes_formal_writer(self) -> None:
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaises(LiveExecutionDenied) as caught:
                assert_external_launch_allowed(
                    self.config,
                    purpose="formal_writer",
                    command=[sys.executable, "formal_writer.py"],
                )
        self.assertEqual(caught.exception.code, "manual_live_authorization_missing")

    def test_in_process_runner_can_prove_the_same_claim_explicitly(self) -> None:
        local_names = (
            "STUDY_PREPROCESS_RUNTIME_ROOT",
            "STUDY_PREPROCESS_UNIT_SHA256",
            "STUDY_PREPROCESS_LEASE_FENCE",
            "STUDY_PREPROCESS_LEASE_OWNER_ID",
            "STUDY_PREPROCESS_CONTEXT_ROOT",
        )
        task_identity = {
            "local_trusted": True,
            "runtime_root": str(self.runtime),
            "context_root": str(self.context),
            "unit_sha256": self.task.unit_sha256,
            "frozen_payload_sha256": self.task.frozen_payload_sha256,
            "subject": "math",
            "capture_id": "MFI-CAP-LOCAL-TRUSTED-001",
            "lease_owner_id": self.owner_id,
            "lease_fence": self.lease.fence,
        }
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in local_names:
                os.environ.pop(name, None)
            decision = assert_external_launch_allowed(
                self.config,
                purpose="provider_model_request",
                command=[
                    "/Applications/ChatGPT.app/Contents/Resources/codex",
                    "exec",
                ],
                task_identity=task_identity,
            )
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["unit_sha256"], self.task.unit_sha256)


class ScannerReductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name).resolve() / "runtime"
        self.runtime.mkdir(parents=True)
        self.config = {
            "timezone": "Asia/Shanghai",
            "runtime_root": str(self.runtime),
            "model": {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
        }

    def test_recorded_at_cutoff_applies_to_cs408_and_english_not_study_date(self) -> None:
        for subject in ("cs408", "english"):
            with self.subTest(subject=subject):
                scanner = make_scanner_worker_factory(
                    release_id=RELEASE_ID,
                    adapters={
                        subject: SimpleNamespace(
                            candidate_diagnostics={},
                            candidate_errors={},
                        )
                    },
                )
                if subject == "cs408":
                    template = core_candidate(700)
                    old = Candidate(
                        **{
                            **template.__dict__,
                            "capture_id": "CAP-cs408-OLD",
                            "recorded_at": "2026-08-22T09:59:59+08:00",
                            "input_fingerprint": "7" * 64,
                        }
                    )
                    new = Candidate(
                        **{
                            **template.__dict__,
                            "capture_id": "CAP-cs408-NEW",
                            "recorded_at": "2026-08-22T10:00:01+08:00",
                            "input_fingerprint": "8" * 64,
                        }
                    )
                else:
                    old = candidate(
                        subject,
                        f"CAP-{subject}-OLD",
                        "2026-08-22T09:59:59+08:00",
                    )
                    new = candidate(
                        subject,
                        f"CAP-{subject}-NEW",
                        "2026-08-22T10:00:01+08:00",
                    )
                scanner.set_candidates([(old, "new"), (new, "new")])
                frozen, decisions = scan_eligible_candidates(
                    self.config,
                    subject,
                    worker_factory=scanner,
                    producer_recorded_after="2026-08-22T10:00:00+08:00",
                    publish_evidence_readiness=False,
                )
                self.assertEqual(
                    [row.candidate.capture_id for row in frozen],
                    [new.capture_id],
                )
                excluded = next(
                    row for row in decisions if row["capture_id"] == old.capture_id
                )
                self.assertFalse(excluded["eligible"])
                self.assertEqual(excluded["reason"], "pre_cutoff_capture")
                self.assertEqual(excluded["formal_write_count"], 0)

    def test_missing_recorded_at_is_needs_review_not_historical_exclusion(self) -> None:
        scanner = make_scanner_worker_factory(
            release_id=RELEASE_ID,
            adapters={
                "english": SimpleNamespace(
                    candidate_diagnostics={}, candidate_errors={}
                )
            },
        )
        malformed = Candidate(
            **{
                **candidate(
                    "english",
                    "CAP-english-MISSING-TIME",
                    "2026-08-22T11:00:00+08:00",
                ).__dict__,
                "recorded_at": None,
            }
        )
        scanner.set_candidates([(malformed, "new")])
        frozen, decisions = scan_eligible_candidates(
            self.config,
            "english",
            worker_factory=scanner,
            producer_recorded_after="2026-08-22T10:00:00+08:00",
            publish_evidence_readiness=False,
        )
        self.assertEqual(frozen, [])
        self.assertEqual(decisions[0]["phase"], "needs_review")
        self.assertEqual(
            decisions[0]["reason"], "producer_recorded_at_invalid"
        )
        self.assertEqual(decisions[0]["formal_write_count"], 0)

    def test_missing_producer_attestation_is_warning_but_bytes_conflict_rejects(self) -> None:
        fixture = _build_subject_fixture(
            Path(self.temp.name).resolve(), "math", "attestation"
        )
        config = {
            **self.config,
            "execution_mode": "fixture",
            "adapters": {"math": {"repo_root": str(fixture["root"])}},
        }
        descriptor = json.loads(
            fixture["descriptor"].read_text(encoding="utf-8")
        )
        descriptor_binding = {
            "descriptor_path": str(fixture["descriptor"]),
            "descriptor_sha256": hashlib.sha256(
                fixture["descriptor"].read_bytes()
            ).hexdigest(),
            "attestation_required_after": descriptor[
                "attestation_required_after"
            ],
            "producer_source_closure_sha256": descriptor["producer"][
                "source_closure_sha256"
            ],
            "foreground_skill_sha256": descriptor["foreground_skill"][
                "installed_sha256"
            ],
        }
        component_lock = fixture["root"] / "component-lock.json"
        component_lock.write_text(
            json.dumps(
                {
                    "foreground_capture_contracts": {
                        "math": descriptor_binding
                    },
                    "fixture_contracts": {
                        "math": {"profile": "math-v1", "sha256": "6" * 64}
                    },
                    "skills": {
                        "background-math-processing": {
                            "version": "4.0.0",
                            "sha256": "3" * 64,
                        },
                        "multi-agent-read-orchestrate": {
                            "version": "1.0.0",
                            "sha256": "4" * 64,
                        },
                    },
                    "multi_agent": {
                        "roles": {
                            "orchestrator": {"model": "gpt-5.6-terra"},
                            "reader": {"model": "gpt-5.6-luna"},
                            "critical_reviewer": {"model": "gpt-5.6-terra"},
                        }
                    },
                    "agent_configs": {"fixture": "5" * 64},
                    "luna_mcp_servers": {
                        "math": {
                            "server_name": "kaoyan_math_read",
                            "launch_mode": "subject-server",
                        }
                    },
                    "formal_write_count": 0,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        config["processing_plugin"] = {
            "component_lock_path": str(component_lock)
        }
        template = core_candidate(900, subject="math")
        row = Candidate(
            **{
                **template.__dict__,
                "capture_id": "MFI-CAP-ATTESTATION-0001",
                "recorded_at": "2026-08-22T11:00:00+08:00",
                "input_binding": {
                    **template.input_binding,
                    "original_content_hash": "1" * 64,
                },
            }
        )
        scanner = make_scanner_worker_factory(
            release_id=RELEASE_ID,
            adapters={
                "math": SimpleNamespace(
                    candidate_diagnostics={}, candidate_errors={}
                )
            },
        )
        scanner.set_candidates([(row, "new")])
        frozen, decisions = scan_eligible_candidates(
            config,
            "math",
            worker_factory=scanner,
            publish_evidence_readiness=False,
        )
        self.assertEqual(len(frozen), 1, decisions)
        self.assertEqual(frozen[0].candidate.capture_id, row.capture_id)
        self.assertIn(
            "producer_attestation_missing",
            decisions[0]["normalization_warnings"],
        )

        attestation_root = (
            fixture["root"]
            / "数学一回滚复习系统"
            / "快速入库绑定证明"
        )
        attestation_root.mkdir(parents=True)
        (attestation_root / f"{row.capture_id}.json").write_text(
            '{"conflicting":"bytes"}\n', encoding="utf-8"
        )
        frozen, decisions = scan_eligible_candidates(
            config,
            "math",
            worker_factory=scanner,
            publish_evidence_readiness=False,
        )
        self.assertEqual(frozen, [])
        self.assertEqual(decisions[0]["reason"], "foreground_skill_binding_mismatch")
        self.assertEqual(decisions[0]["phase"], "needs_review")

    def test_partial_microbatch_attestation_gap_is_still_missing_not_conflict(self) -> None:
        fixture = _build_subject_fixture(
            Path(self.temp.name).resolve(), "english", "partial-attestation"
        )
        events = [
            {
                "event_id": f"EVT-20260822-{index:016X}",
                "occurred_at": f"2026-08-22T11:0{index}:00+08:00",
            }
            for index in (1, 2)
        ]
        hashes = {event["event_id"]: sha256_value(event) for event in events}
        row = Candidate(
            subject="english",
            capture_id="EN-20260822-PARTIALATTEST01",
            study_date="2026-08-22",
            recorded_at="2026-08-22T11:02:00+08:00",
            input_fingerprint="3" * 64,
            input_binding={
                "processing_contract_sha256": PROCESSING_CONTRACT,
                "capture_event_sha256": hashes,
            },
            model_input={"batch_events": events},
            allowed_evidence_refs=(),
            image_paths=(),
            target_label="partial attestation",
            canonical_state="awaiting_background_analysis",
            sol_state="pending_review",
        )
        spec = importlib.util.spec_from_file_location(
            "partial_english_attestation", fixture["helper"]
        )
        assert spec and spec.loader
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.publish_attestation(
            descriptor_path=fixture["descriptor"],
            repo_root=fixture["root"],
            subject="english",
            capture_id=events[0]["event_id"],
            capture_content_sha256=hashes[events[0]["event_id"]],
            recorded_at=events[0]["occurred_at"],
        )
        config = {
            "execution_mode": "fixture",
            "adapters": {
                "english": {"repo_root": str(fixture["root"])}
            },
        }
        self.assertTrue(
            _required_producer_attestation_missing(config, row)
        )


class SourceOnlyRehearsalControlTests(unittest.TestCase):
    class ZeroModelRunner:
        @staticmethod
        def _stage(stage: str, task: FrozenTask) -> StageResult:
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
            return self._stage("analysis", task)

        def run_critical_review(
            self,
            task: FrozenTask,
            _draft: object,
            _context: object,
        ) -> StageResult:
            return self._stage("critical_review", task)

    @staticmethod
    def _config(runtime_root: Path) -> dict:
        return OrdinaryLocalSubmitTests._config(runtime_root)

    @staticmethod
    def _producer_authority() -> dict:
        core = {
            "schema_version": "study-intake-producer-authority-v1",
            "subject": "math",
            "release_id": LOADED_CORE_SHA256,
            "loaded_core_sha256": LOADED_CORE_SHA256,
            "processing_contract_sha256": PROCESSING_CONTRACT,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "formal_write_count": 0,
        }
        return {**core, "authority_fingerprint": sha256_value(core)}

    def test_source_only_exact_run_once_uses_existing_high_water(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime_root = root / "runtime"
            config = self._config(runtime_root)
            self.assertNotIn("release", config)
            source_release_id, source_provenance = release_identity(config)
            self.assertEqual(source_release_id, LOADED_CORE_SHA256)
            self.assertEqual(source_provenance, "loaded_core_only")
            producer_fixture = _build_subject_fixture(
                root, "math", "source-only-rehearsal"
            )
            descriptor_path = producer_fixture["descriptor"]
            descriptor = json.loads(
                descriptor_path.read_text(encoding="utf-8")
            )
            descriptor["attestation_required_after"] = (
                "2099-01-01T00:00:00Z"
            )
            descriptor_core = {
                key: value
                for key, value in descriptor.items()
                if key != "descriptor_content_sha256"
            }
            descriptor["descriptor_content_sha256"] = hashlib.sha256(
                (
                    json.dumps(
                        descriptor_core,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
            descriptor_path.write_text(
                json.dumps(descriptor, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            config["adapters"] = {
                "math": {
                    "enabled": True,
                    "repo_root": str(producer_fixture["root"]),
                }
            }
            cutoff = "2026-08-22T10:00:00+08:00"

            def math_candidate(
                index: int, capture_id: str, recorded_at: str
            ) -> Candidate:
                template = core_candidate(index, subject="math")
                return Candidate(
                    **{
                        **template.__dict__,
                        "capture_id": capture_id,
                        "study_date": "2026-08-22",
                        "recorded_at": recorded_at,
                        "input_fingerprint": hashlib.sha256(
                            capture_id.encode("utf-8")
                        ).hexdigest(),
                        "input_binding": {
                            **template.input_binding,
                            "original_content_hash": hashlib.sha256(
                                f"source:{capture_id}".encode("utf-8")
                            ).hexdigest(),
                        },
                    }
                )

            target = math_candidate(
                950,
                "MFI-CAP-SOURCE-ONLY-TARGET",
                "2026-08-22T10:00:02+08:00",
            )
            sibling = math_candidate(
                951,
                "MFI-CAP-SOURCE-ONLY-SIBLING",
                "2026-08-22T10:00:03+08:00",
            )
            historical = math_candidate(
                952,
                "MFI-CAP-SOURCE-ONLY-HISTORICAL",
                "2026-08-22T10:00:00+08:00",
            )
            scanner = make_scanner_worker_factory(
                release_id=LOADED_CORE_SHA256,
                eligible_candidates=[
                    (historical, "new"),
                    (target, "new"),
                    (sibling, "new"),
                ],
                scan_statuses={
                    "math": {
                        "pending": [
                            {
                                "event_id": historical.capture_id,
                                "recorded_at": historical.recorded_at,
                                "study_date": historical.study_date,
                            },
                            {
                                "event_id": "MFI-CAP-SOURCE-ONLY-INVALID",
                                "recorded_at": "invalid",
                                "study_date": "2026-08-22",
                            },
                            {
                                "event_id": target.capture_id,
                                "recorded_at": target.recorded_at,
                                "study_date": target.study_date,
                            },
                            {
                                "event_id": sibling.capture_id,
                                "recorded_at": sibling.recorded_at,
                                "study_date": sibling.study_date,
                            },
                        ]
                    }
                },
                adapters={
                    "math": SimpleNamespace(
                        candidate_diagnostics={},
                        candidate_errors={},
                    )
                },
            )
            runtime = ProductionDispatchRuntime(
                config,
                "math",
                root / "config.json",
                scan_worker_factory=scanner,
                ordinary_local_capture=True,
            )
            self.assertFalse(runtime.production_canary)
            runtime.dispatcher.lease_store.begin_subject_drain("math")
            runtime.dispatcher.lease_store.activate_production_canary(
                "math",
                release_id=LOADED_CORE_SHA256,
                producer_authority=self._producer_authority(),
                activated_at=cutoff,
                continuous_concurrency_limit=20,
            )
            gate = (
                runtime.dispatcher.lease_store.production_canary_status_read_only(
                    "math",
                    expected_release_id=LOADED_CORE_SHA256,
                )
            )
            assert gate is not None
            self.assertEqual(gate["state"], "armed")
            self.assertEqual(
                gate["producer_high_watermark"]["recorded_at"], cutoff
            )
            self.assertEqual(
                gate["release_id"], LOADED_CORE_SHA256
            )

            _frozen, visibility = scan_eligible_candidates(
                {
                    **config,
                    "runtime_root": str(root / "visibility-runtime"),
                },
                "math",
                worker_factory=scanner,
                producer_recorded_after=cutoff,
                publish_evidence_readiness=False,
            )
            visibility_by_capture = {
                row.get("capture_id"): row for row in visibility
            }
            self.assertEqual(
                visibility_by_capture[historical.capture_id]["phase"],
                "historical_cutoff",
            )
            self.assertEqual(
                visibility_by_capture[
                    "MFI-CAP-SOURCE-ONLY-INVALID"
                ]["phase"],
                "needs_review",
            )

            runtime.dispatcher.runner_factory = (
                lambda _task, _context: self.ZeroModelRunner()
            )
            original_scan_and_submit = runtime.scan_and_submit
            captured_decisions: list[dict] = []

            def capture_scan_and_submit(**kwargs: object):
                handles, decisions = original_scan_and_submit(**kwargs)
                captured_decisions.extend(decisions)
                return handles, decisions

            runtime.scan_and_submit = capture_scan_and_submit  # type: ignore[method-assign]
            with (
                mock.patch(
                    "preprocess_dispatcher.ProductionDispatchRuntime",
                    return_value=runtime,
                ),
                mock.patch(
                    "preprocess_dispatcher._write_subject_projections"
                ),
                mock.patch(
                    "preprocessor_core.CodexRunner._invoke_subprocess",
                    side_effect=AssertionError("Provider tripwire invoked"),
                ) as provider_tripwire,
                mock.patch(
                    "preprocess_dispatcher.ProcessingPluginHost",
                    side_effect=AssertionError("MCP tripwire invoked"),
                ) as mcp_tripwire,
            ):
                result = _run_once(
                    config,
                    "math",
                    root / "config.json",
                    capture_id=target.capture_id,
                )
            self.assertEqual(result["status"], "drained")
            self.assertEqual(
                result["eligible_count"],
                1,
                {"calls": scanner.calls, "decisions": captured_decisions},
            )
            self.assertEqual(
                result["results"][0]["completion"]["capture_id"],
                target.capture_id,
            )
            self.assertNotEqual(
                result["results"][0]["completion"]["capture_id"],
                sibling.capture_id,
            )
            self.assertTrue(
                result["results"][0]["completion"]["report_json_ref"]
            )
            self.assertEqual(result["formal_write_count"], 0)
            provider_tripwire.assert_not_called()
            mcp_tripwire.assert_not_called()
            scanner_calls = [
                row
                for row in scanner.calls
                if row["method"] == "eligible_candidates"
            ]
            self.assertEqual(
                scanner_calls[-1]["kwargs"]["capture_allowlist"],
                frozenset({target.capture_id}),
            )
            queue_root = (
                runtime_root
                / "dispatch/state/production-canary-queue"
            )
            self.assertEqual(
                list(queue_root.rglob("*.json"))
                if queue_root.exists()
                else [],
                [],
            )


class OrdinaryLocalSubmitTests(unittest.TestCase):
    @staticmethod
    def _config(runtime_root: Path) -> dict:
        return {
            "execution_mode": "live_authorized",
            "runtime_root": str(runtime_root),
            "timezone": "Asia/Shanghai",
            "model": {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
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
            "math_deep_v2": {
                "soft_runtime_warning_seconds": 60,
                "stall_timeout_seconds": 60,
                "stall_probe_interval_seconds": 1,
                "stall_probe_required_consecutive_failures": 2,
            },
        }

    def _runtime(self, runtime_root: Path) -> ProductionDispatchRuntime:
        return ProductionDispatchRuntime(
            self._config(runtime_root),
            "math",
            runtime_root.parent / "config.json",
            ordinary_local_capture=True,
        )

    def test_signed_operational_control_gates_ordinary_scan_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve() / "runtime"
            runtime = self._runtime(runtime_root)
            status = {
                "draining": True,
                "active_count": 0,
                "claimed_total": 0,
            }
            states = {
                "armed": (True, True, "running"),
                "failed_drained": (False, False, "paused"),
                "paused_drained": (False, False, "paused"),
                "consumer_disabled": (False, False, "paused"),
            }
            for label, (expected_allowed, consumer_enabled, daemon_status) in (
                ("armed", states["armed"]),
                ("failed_drained", states["failed_drained"]),
                ("paused_drained", states["paused_drained"]),
                ("consumer_disabled", states["consumer_disabled"]),
            ):
                with self.subTest(state=label):
                    gate_state = "armed" if label == "consumer_disabled" else label
                    gate = {
                        "schema_version": "study-intake-production-canary-state-v3",
                        "status": "production_canary_active",
                        "subject": "math",
                        "release_id": LOADED_CORE_SHA256,
                        "state": gate_state,
                        "luna_consumer_enabled": consumer_enabled,
                    }
                    with mock.patch.object(
                        runtime.dispatcher.lease_store,
                        "production_canary_status_read_only",
                        return_value=gate,
                    ):
                        control = runtime.ordinary_operational_control(status)
                    self.assertEqual(control["allowed"], expected_allowed)
                    self.assertEqual(control["daemon_status"], daemon_status)
                    self.assertEqual(control["gate_state"], gate_state)

    def test_run_once_obeys_same_pause_without_clearing_drain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve() / "runtime"
            config = self._config(runtime_root)
            fake_runtime = mock.Mock()
            fake_runtime.ordinary_operational_control.return_value = {
                "allowed": False,
                "reason": "ordinary_control_paused_drained",
                "daemon_status": "paused",
                "gate_state": "paused_drained",
            }
            fake_runtime.dispatcher.lease_store.subject_status.return_value = {
                "draining": True,
                "active_count": 0,
                "claimed_total": 0,
            }
            fake_runtime.subject_sol.read_subject.return_value = {}
            with (
                mock.patch(
                    "preprocess_dispatcher.ProductionDispatchRuntime",
                    return_value=fake_runtime,
                ),
                mock.patch("preprocess_dispatcher._write_subject_projections"),
            ):
                result = _run_once(
                    config,
                    "math",
                    Path(temporary).resolve() / "config.json",
                )
            fake_runtime.dispatcher.lease_store.clear_subject_drain.assert_not_called()
            fake_runtime.scan_and_submit.assert_not_called()
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["eligible_count"], 0)

    def test_armed_control_keeps_direct_submit_out_of_canary_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve() / "runtime"
            runtime_root.mkdir()
            runtime = self._runtime(runtime_root)
            frozen_candidate = candidate(
                "math",
                "MFI-CAP-ORDINARY-CONTROL-001",
                "2026-08-22T12:30:00+08:00",
            )
            frozen_task = FrozenTask.from_candidate(frozen_candidate)
            unit = EligibleFrozenCandidate(
                frozen_task, frozen_candidate, "new_capture"
            )
            decision = {
                "subject": "math",
                "capture_id": frozen_candidate.capture_id,
                "unit_sha256": frozen_task.unit_sha256,
                "eligible": True,
                "model_enqueue_allowed": True,
                "formal_write_count": 0,
            }
            runtime._local_capture_recorded_after = mock.Mock(
                return_value="2026-08-22T10:00:00+08:00"
            )
            runtime.ordinary_operational_control = mock.Mock(
                return_value={
                    "allowed": True,
                    "reason": "ordinary_control_armed",
                    "daemon_status": "running",
                    "gate_state": "armed",
                }
            )
            runtime.dispatcher.submit = mock.Mock(return_value=object())
            runtime.dispatcher.lease_store.materialize_production_canary_task = (
                mock.Mock()
            )
            with (
                mock.patch(
                    "preprocess_dispatcher.scan_eligible_candidates",
                    return_value=([unit], [decision]),
                ),
                mock.patch("preprocess_dispatcher._write_subject_projections"),
            ):
                handles, _decisions = runtime.scan_and_submit()
            self.assertEqual(len(handles), 1)
            runtime.dispatcher.submit.assert_called_once_with(frozen_task)
            runtime.dispatcher.lease_store.materialize_production_canary_task.assert_not_called()

    def test_live_daemon_mode_uses_existing_submit_without_canary_or_subject_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve() / "runtime"
            runtime_root.mkdir()
            config = self._config(runtime_root)
            frozen_candidate = candidate(
                "math",
                "MFI-CAP-ORDINARY-LOCAL-001",
                "2026-08-22T12:30:00+08:00",
            )
            frozen_task = FrozenTask.from_candidate(frozen_candidate)
            unit = EligibleFrozenCandidate(
                frozen_task, frozen_candidate, "new_capture"
            )
            decision = {
                "subject": "math",
                "capture_id": frozen_candidate.capture_id,
                "unit_sha256": frozen_task.unit_sha256,
                "eligible": True,
                "model_enqueue_allowed": True,
                "formal_write_count": 0,
            }
            dispatcher_instance = mock.Mock()
            dispatcher_instance.submit.return_value = object()
            dispatcher_instance.lease_store.subject_status.return_value = {
                "active_count": 1,
                "draining": False,
            }
            with (
                mock.patch(
                    "preprocess_dispatcher.ConcurrentDispatcher",
                    return_value=dispatcher_instance,
                ) as dispatcher_class,
                mock.patch(
                    "preprocess_dispatcher.SubjectSolRuntimeStore"
                ) as subject_sol_class,
            ):
                runtime = ProductionDispatchRuntime(
                    config,
                    "math",
                    Path(temporary) / "config.json",
                    ordinary_local_capture=True,
                )
            self.assertFalse(runtime.production_canary)
            dispatcher_class.assert_called_once()
            self.assertFalse(
                dispatcher_class.call_args.kwargs["production_canary"]
            )
            runtime._local_capture_recorded_after = mock.Mock(
                return_value="2026-08-22T10:00:00+08:00"
            )
            runtime.ordinary_operational_control = mock.Mock(
                return_value={
                    "allowed": True,
                    "reason": "ordinary_control_armed",
                    "daemon_status": "running",
                    "gate_state": "armed",
                }
            )
            runtime._prepare_batch_before_submit = mock.Mock()
            with (
                mock.patch(
                    "preprocess_dispatcher.scan_eligible_candidates",
                    return_value=([unit], [decision]),
                ) as scan,
                mock.patch(
                    "preprocess_dispatcher._write_subject_projections"
                ),
            ):
                handles, decisions = runtime.scan_and_submit()
            self.assertEqual(len(handles), 1)
            self.assertEqual(decisions, [decision])
            scan.assert_called_once_with(
                config,
                "math",
                producer_recorded_after="2026-08-22T10:00:00+08:00",
            )
            dispatcher_instance.submit.assert_called_once_with(frozen_task)
            runtime._prepare_batch_before_submit.assert_not_called()
            subject_sol_class.return_value.luna_admission.assert_not_called()
            self.assertEqual(
                runtime._direct_controlled_candidates[
                    frozen_task.unit_sha256
                ],
                (frozen_candidate, "new_capture"),
            )

    def test_local_completion_projects_report_refs_without_subject_sol_batch(self) -> None:
        runtime = object.__new__(ProductionDispatchRuntime)
        runtime.subject = "math"
        runtime.ordinary_local_capture = True
        runtime.production_canary = False
        runtime.subject_sol = mock.Mock()
        runtime.dispatcher = mock.Mock()
        runtime._direct_controlled_candidates = {
            "e" * 64: (object(), "actual_foreground_capture")
        }
        completion = {
            "subject": "math",
            "capture_id": "MFI-CAP-LOCAL-COMPLETE-001",
            "release_id": "a" * 64,
            "outcome": "succeeded",
            "package_ref": "study-intake-package://sha256/" + "b" * 64,
            "report_json_ref": "study-intake-report://sha256/" + "c" * 64,
            "report_markdown_ref": "study-intake-report://sha256/" + "d" * 64,
        }
        runtime.dispatcher.lease_store.verify_authoritative_completion.return_value = {
            "completion": completion
        }
        result = SimpleNamespace(
            unit_sha256="e" * 64,
            completion=completion,
            outcome="succeeded",
            error_code=None,
        )
        handle = mock.Mock(done=True)
        handle.unit_sha256 = "e" * 64
        handle.wait.return_value = result

        persisted = runtime.persist_finished_luna_with_status([handle])
        projected = persisted["projected"]

        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["capture_id"], completion["capture_id"])
        self.assertEqual(projected[0]["package_ref"], completion["package_ref"])
        self.assertEqual(projected[0]["formal_write_count"], 0)
        runtime.subject_sol.record_verified_luna_completion.assert_not_called()
        self.assertNotIn("e" * 64, runtime._direct_controlled_candidates)


class AnalysisPackageTerminalBridgeTests(unittest.TestCase):
    class FakeAnalysisPackageRunner:
        def __init__(self) -> None:
            self._dispatch_process_lifecycle = None
            self.config: dict[str, object] = {}

        def bind_dispatch_process_lifecycle(
            self, *, task: object, lease: object, lease_store: object
        ) -> None:
            self._dispatch_process_lifecycle = {
                "task": task,
                "lease": lease,
                "lease_store": lease_store,
            }

        def run(self, candidate_value: Candidate) -> ModelResult:
            stages = []
            for index, (stage, model) in enumerate(
                (
                    ("terra_analysis", "gpt-5.6-terra"),
                    ("luna_analysis", "gpt-5.6-luna"),
                    ("terra_final", "gpt-5.6-terra"),
                ),
                start=1,
            ):
                def digest(label: str) -> str:
                    return hashlib.sha256(
                        f"{stage}:{label}".encode("utf-8")
                    ).hexdigest()

                raw_digest = digest("raw")
                report_digest = digest("report")
                execution_digest = digest("analysis-execution")
                normalization_digest = digest("analysis-normalization")
                stage_execution_digest = digest("stage-execution")
                stage_normalization_digest = digest("stage-normalization")
                process_identity_digest = digest("process-identity")
                process_exit_digest = digest("process-exit")
                normalization_status = (
                    "incomplete" if stage == "luna_analysis" else "complete"
                )
                warning_rows = (
                    [
                        {
                            "code": "synthetic_luna_incomplete",
                            "stage": stage,
                            "kind": "normalization_warning",
                        }
                    ]
                    if normalization_status == "incomplete"
                    else []
                )
                receipt = {
                    "status": "ready",
                    "requested_model": model,
                    "requested_reasoning_effort": "max",
                    "runtime_model": model,
                    "runtime_reasoning_effort": "max",
                    "runtime_metadata_provenance": (
                        "codex_json_attestation_v1"
                    ),
                    "runtime_identity_status": "confirmed",
                    "duration_ms": index,
                    "semantic_stage_count": 1,
                    "provider_request_count": index + 1,
                    "mcp_tool_call_count": index,
                    "model_call_count": 1,
                    "raw_output_object_sha256": raw_digest,
                    "raw_output_object_ref": (
                        "study-intake-model-stage-raw-output://sha256/"
                        + raw_digest
                    ),
                    "stage_execution_receipt_sha256": stage_execution_digest,
                    "stage_execution_receipt_ref": (
                        "study-intake-model-stage-execution://sha256/"
                        + stage_execution_digest
                    ),
                    "stage_normalization_receipt_sha256": (
                        stage_normalization_digest
                    ),
                    "stage_normalization_receipt_ref": (
                        "study-intake-model-stage-normalization://sha256/"
                        + stage_normalization_digest
                    ),
                    "normalization_status": normalization_status,
                    "normalization_warning_count": len(warning_rows),
                    "normalization_warnings": warning_rows,
                    "provider_process_identity_sha256": (
                        process_identity_digest
                    ),
                    "provider_process_exit_sha256": process_exit_digest,
                    "provider_returncode": 0,
                    "execution_status": "completed",
                    "formal_write_count": 0,
                }
                receipt_sha256 = hashlib.sha256(
                    (
                        json.dumps(
                            receipt,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                ).hexdigest()
                stages.append(
                    {
                        "stage": stage,
                        "requested_model": model,
                        "requested_reasoning_effort": "max",
                        "read_only": True,
                        "report_sha256": report_digest,
                        "report_ref": (
                            "study-intake-analysis-stage-report://sha256/"
                            + report_digest
                        ),
                        "raw_output_sha256": raw_digest,
                        "raw_output_ref": (
                            "study-intake-model-stage-raw-output://sha256/"
                            + raw_digest
                        ),
                        "execution_receipt_sha256": execution_digest,
                        "execution_receipt_ref": (
                            "study-intake-analysis-stage-execution-receipt://sha256/"
                            + execution_digest
                        ),
                        "normalization_receipt_sha256": normalization_digest,
                        "normalization_receipt_ref": (
                            "study-intake-analysis-stage-normalization-receipt://sha256/"
                            + normalization_digest
                        ),
                        "normalization_status": normalization_status,
                        "runtime": {
                            "requested_model": model,
                            "requested_reasoning_effort": "max",
                            "runtime_model": model,
                            "runtime_reasoning_effort": "max",
                            "runtime_metadata_provenance": (
                                "codex_json_attestation_v1"
                            ),
                            "duration_ms": index,
                        },
                        "receipt": receipt,
                        "receipt_sha256": receipt_sha256,
                        "formal_write_count": 0,
                    }
                )
            package = {
                "schema_version": "study-intake-analysis-package-v1",
                "package_id": "ANPKG-LOCAL-TERMINAL-001",
                "capture_id": candidate_value.capture_id,
                "subject": candidate_value.subject,
                "study_date": candidate_value.study_date,
                "package_sha256": "7" * 64,
                "package_ref": (
                    "study-intake-analysis-package://sha256/" + "7" * 64
                ),
                "stage_order": [
                    "terra_analysis",
                    "luna_analysis",
                    "terra_final",
                ],
                "stages": stages,
                "warnings": ["synthetic_luna_incomplete"],
                "status": "ready_for_nightly",
                "formal_write_count": 0,
            }
            return ModelResult(
                analysis=package,
                duration_ms=sum(
                    row["receipt"]["duration_ms"] for row in stages
                ),
                runtime_model="gpt-5.6-terra",
                runtime_reasoning_effort="max",
                runtime_metadata_provenance="synthetic_zero_model",
                pipeline_status="analysis_package_ready",
                draft_analysis={"summary": "analysis"},
                critical_review={"summary": "final"},
                stage_receipts={
                    row["stage"]: row["receipt"] for row in stages
                },
                semantic_stage_count=3,
                provider_request_count=sum(
                    row["receipt"]["provider_request_count"]
                    for row in stages
                ),
                mcp_tool_call_count=sum(
                    row["receipt"]["mcp_tool_call_count"]
                    for row in stages
                ),
            )

    class FakeWorker:
        def __init__(self, release_id: str) -> None:
            self.release_id = release_id
            self.runner = (
                AnalysisPackageTerminalBridgeTests.FakeAnalysisPackageRunner()
            )

    def test_cached_critical_reuses_existing_provider_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary).resolve() / "runtime"
            candidate_value = candidate(
                "math",
                "MFI-CAP-CACHED-CRITICAL-PROGRESS-001",
                "2026-08-22T12:45:00+08:00",
            )
            rule = dispatch_rule_binding(
                release_id=RELEASE_ID,
                subject="math",
                subject_processing_contract_sha256=PROCESSING_CONTRACT,
            )
            payload = dict(
                FrozenTask.from_candidate(candidate_value).frozen_payload
            )
            payload["dispatch_contract"] = {
                "schema_version": (
                    "study-intake-dispatch-release-binding-v1"
                ),
                **rule,
                "loaded_core_sha256": "8" * 64,
                "dispatch_reason": "actual_foreground_capture",
                "requested_service_tier": None,
                "fast_mode_requested": False,
                "fast_mode_effective": "not_requested",
            }
            task = FrozenTask(payload)
            store = LeaseStore(runtime)
            observed_progress = []

            class CachedCriticalRunner:
                executes_full_two_pass_in_analysis = True

                def run_analysis(inner_self, task_value, context):
                    store.publish_stage_progress(
                        task_value,
                        context.lease,
                        stage_name="math_critical_review",
                        progress_kind="stage_transition",
                        stdout_bytes=11,
                        stderr_bytes=7,
                        mcp_tool_call_count=3,
                    )
                    return StageResult(
                        payload={"stage": "analysis"},
                        runtime_model="gpt-5.6-luna",
                        runtime_reasoning_effort="max",
                        runtime_metadata_provenance=(
                            "codex_json_attestation_v1"
                        ),
                        runtime_identity_status="confirmed",
                    )

                def run_critical_review(
                    inner_self, task_value, _draft_analysis, context
                ):
                    observed_progress.append(
                        store.latest_stage_progress(
                            task_value,
                            context.lease,
                            stage_name="math_critical_review",
                        )
                    )
                    return StageResult(
                        payload={"stage": "critical_review"},
                        runtime_model="gpt-5.6-luna",
                        runtime_reasoning_effort="max",
                        runtime_metadata_provenance=(
                            "codex_json_attestation_v1"
                        ),
                        runtime_identity_status="confirmed",
                    )

            dispatcher = ConcurrentDispatcher(
                runtime, lambda _task, _context: CachedCriticalRunner()
            )
            result = dispatcher.submit(task).wait(5)
            self.assertEqual(result.outcome, "succeeded", result.error_code)
            self.assertEqual(len(observed_progress), 1)
            progress = observed_progress[0]
            self.assertEqual(progress["stdout_bytes"], 11)
            self.assertEqual(progress["stderr_bytes"], 7)
            self.assertEqual(progress["mcp_tool_call_count"], 3)

    def test_analysis_package_ready_reaches_existing_terminal_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary).resolve() / "runtime"
            candidate_value = candidate(
                "math",
                "MFI-CAP-ANALYSIS-PACKAGE-001",
                "2026-08-22T12:45:00+08:00",
            )
            rule = dispatch_rule_binding(
                release_id=RELEASE_ID,
                subject="math",
                subject_processing_contract_sha256=PROCESSING_CONTRACT,
            )
            payload = dict(
                FrozenTask.from_candidate(candidate_value).frozen_payload
            )
            payload["dispatch_contract"] = {
                "schema_version": (
                    "study-intake-dispatch-release-binding-v1"
                ),
                **rule,
                "loaded_core_sha256": "8" * 64,
                "dispatch_reason": "actual_foreground_capture",
                "requested_service_tier": None,
                "fast_mode_requested": False,
                "fast_mode_effective": "not_requested",
            }
            task = FrozenTask(payload)
            config = {
                "execution_mode": "live_authorized",
                "runtime_root": str(runtime),
                "model": {
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "max",
                },
                "consumer_stage_chain": {"enabled": True},
                "analysis_package_v1": {"enabled": True},
            }
            dispatcher = ConcurrentDispatcher(
                runtime,
                lambda _task, _context: CoreCandidateRunner(
                    config,
                    candidate_value,
                    "actual_foreground_capture",
                    LeaseStore(runtime),
                    worker_factory=lambda _config: self.FakeWorker(
                        RELEASE_ID
                    ),
                ),
            )

            result = dispatcher.submit(task).wait(5)

            self.assertEqual(result.outcome, "succeeded", result.error_code)
            self.assertIsNotNone(result.completion)
            self.assertEqual(
                result.completion["capture_id"],
                candidate_value.capture_id,
            )
            self.assertTrue(result.completion["package_ref"])
            self.assertTrue(result.completion["report_json_ref"])
            self.assertEqual(result.completion.get("formal_write_count", 0), 0)

            receipt = json.loads(
                Path(result.completion["receipt_path"]).read_text(
                    encoding="utf-8"
                )
            )
            package = json.loads(
                Path(result.completion["package_path"]).read_text(
                    encoding="utf-8"
                )
            )
            authority = package["analysis"]["analysis_package_authority"]
            self.assertEqual(authority["execution_status"], "succeeded")
            self.assertEqual(authority["quality_status"], "issues_found")
            self.assertEqual(
                authority["report_disposition"], "needs_sol_review"
            )
            observed = receipt["observed_stage_runtime"]["analysis"]
            stages = observed["analysis_package_stages"]
            self.assertEqual(
                [row["stage"] for row in stages],
                ["terra_analysis", "luna_analysis", "terra_final"],
            )
            self.assertEqual(
                [row["requested_model"] for row in stages],
                ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-terra"],
            )
            self.assertTrue(
                all(row["provider_process_identity_sha256"] for row in stages)
            )
            self.assertTrue(
                all(row["provider_process_exit_sha256"] for row in stages)
            )
            projected, _, _, _ = dashboard_server._dispatch_stage_receipts(
                receipt
            )
            self.assertEqual(
                list(projected),
                ["terra_analysis", "luna_analysis", "terra_final"],
            )
            self.assertEqual(
                sum(row["provider_request_count"] for row in projected.values()),
                sum(
                    row["provider_request_count"]
                    for row in authority["stages"]
                ),
            )

            duplicate = dispatcher.submit(task).wait(1)
            self.assertEqual(duplicate.status, "deduplicated")
            self.assertEqual(
                duplicate.completion["package_ref"],
                result.completion["package_ref"],
            )

    def test_analysis_package_rejects_missing_authority_and_count_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary).resolve() / "runtime"
            candidate_value = candidate(
                "math",
                "MFI-CAP-ANALYSIS-PACKAGE-INVALID-001",
                "2026-08-22T12:45:00+08:00",
            )
            config = {
                "execution_mode": "live_authorized",
                "runtime_root": str(runtime),
                "model": {
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "max",
                },
                "consumer_stage_chain": {"enabled": True},
                "analysis_package_v1": {"enabled": True},
            }
            bridge = CoreCandidateRunner(
                config,
                candidate_value,
                "actual_foreground_capture",
                LeaseStore(runtime),
            )
            original = self.FakeAnalysisPackageRunner().run(candidate_value)
            cases = {}
            missing_raw = json.loads(json.dumps(original.analysis))
            missing_raw["stages"][0].pop("raw_output_ref")
            cases["missing_raw"] = ModelResult(
                **{**original.__dict__, "analysis": missing_raw}
            )
            missing_process = json.loads(json.dumps(original.analysis))
            missing_process["stages"][1]["receipt"].pop(
                "provider_process_exit_sha256"
            )
            cases["missing_process"] = ModelResult(
                **{**original.__dict__, "analysis": missing_process}
            )
            count_drift = ModelResult(
                **{
                    **original.__dict__,
                    "provider_request_count": (
                        original.provider_request_count + 1
                    ),
                }
            )
            cases["count_drift"] = count_drift
            for label, model_result in cases.items():
                with self.subTest(case=label), self.assertRaisesRegex(
                    DispatchError, "analysis_package_"
                ):
                    bridge._analysis_package_stage_result(
                        model_result, "analysis"
                    )


if __name__ == "__main__":
    unittest.main()
