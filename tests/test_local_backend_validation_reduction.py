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
for directory in (ROOT / "lib", ROOT / "bin", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from concurrent_dispatch import (  # noqa: E402
    ConcurrentDispatcher,
    FrozenTask,
    LeaseStore,
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
    ModelResult,
    sha256_value,
)
from preprocess_dispatcher import ProductionDispatchRuntime  # noqa: E402
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


class OrdinaryLocalSubmitTests(unittest.TestCase):
    def test_live_daemon_mode_uses_existing_submit_without_canary_or_subject_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve() / "runtime"
            runtime_root.mkdir()
            config = {
                "execution_mode": "live_authorized",
                "runtime_root": str(runtime_root),
                "timezone": "Asia/Shanghai",
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
        runtime.subject_sol = mock.Mock()
        runtime.dispatcher = mock.Mock()
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
        handle.wait.return_value = result

        projected = runtime._persist_finished_luna_unchecked([handle])

        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["capture_id"], completion["capture_id"])
        self.assertEqual(projected[0]["package_ref"], completion["package_ref"])
        self.assertEqual(projected[0]["formal_write_count"], 0)
        runtime.subject_sol.record_verified_luna_completion.assert_not_called()


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

        def run(self, _candidate: Candidate) -> ModelResult:
            stages = [
                {
                    "stage": stage,
                    "report_sha256": digest,
                    "report_ref": (
                        "study-intake-analysis-stage-report://sha256/"
                        + digest
                    ),
                    "raw_output_sha256": raw_digest,
                    "raw_output_ref": (
                        "study-intake-model-stage-raw-output://sha256/"
                        + raw_digest
                    ),
                    "normalization_status": "incomplete",
                }
                for stage, digest, raw_digest in (
                    ("terra_analysis", "1" * 64, "2" * 64),
                    ("luna_analysis", "3" * 64, "4" * 64),
                    ("terra_final", "5" * 64, "6" * 64),
                )
            ]
            package = {
                "schema_version": "study-intake-analysis-package-v1",
                "package_id": "ANPKG-LOCAL-TERMINAL-001",
                "package_sha256": "7" * 64,
                "package_ref": (
                    "study-intake-analysis-package://sha256/" + "7" * 64
                ),
                "stages": stages,
                "formal_write_count": 0,
            }
            return ModelResult(
                analysis=package,
                duration_ms=3,
                runtime_model="gpt-5.6-terra",
                runtime_reasoning_effort="max",
                runtime_metadata_provenance="synthetic_zero_model",
                pipeline_status="analysis_package_ready",
                draft_analysis={"summary": "analysis"},
                critical_review={"summary": "final"},
                stage_receipts={},
                semantic_stage_count=3,
                provider_request_count=0,
                mcp_tool_call_count=0,
            )

    class FakeWorker:
        def __init__(self, release_id: str) -> None:
            self.release_id = release_id
            self.runner = (
                AnalysisPackageTerminalBridgeTests.FakeAnalysisPackageRunner()
            )

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

            self.assertEqual(result.outcome, "succeeded")
            self.assertIsNotNone(result.completion)
            self.assertEqual(
                result.completion["capture_id"],
                candidate_value.capture_id,
            )
            self.assertTrue(result.completion["package_ref"])
            self.assertTrue(result.completion["report_json_ref"])
            self.assertEqual(result.completion.get("formal_write_count", 0), 0)


if __name__ == "__main__":
    unittest.main()
