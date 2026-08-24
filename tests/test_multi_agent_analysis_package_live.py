from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from analysis_package_store import AnalysisPackageStore  # noqa: E402
from analysis_package_v2 import reopen_analysis_package_v2  # noqa: E402
from preprocessor_core import (  # noqa: E402
    Candidate,
    CodexRunner,
    PreprocessorError,
    StructuredStageResult,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ref(kind: str, digest: str) -> str:
    return f"study-intake-{kind}://sha256/{digest}"


def _json_sha(value: Any) -> str:
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


def _candidate(
    *,
    subject: str = "math",
    capture_id: str = "CAP-LIVE-MULTI-001",
    recorded_at: str = "2026-08-24T09:00:00+08:00",
) -> Candidate:
    return Candidate(
        subject=subject,
        capture_id=capture_id,
        study_date="2026-08-24",
        recorded_at=recorded_at,
        input_fingerprint="1" * 64,
        input_binding={"synthetic": True},
        model_input={"capture": {"facts": "fixture only"}},
        allowed_evidence_refs=(),
        image_paths=(),
        target_label=capture_id,
        canonical_state="awaiting_background_analysis",
        sol_state="pending_review",
    )


class _OverlapProbe:
    def __init__(self, branch_count: int) -> None:
        self.branch_count = branch_count
        self.started = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            if self.active == self.branch_count:
                self.started.set()
        if not self.started.wait(timeout=2):
            raise AssertionError("Luna branches did not overlap")

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class _FakeProcessingHost:
    def __init__(self, branch_id: str, subject: str) -> None:
        self.branch_id = branch_id
        self.subject = subject
        self.calls: list[dict[str, Any]] = []

    def finalize_investigation_read_session(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        self.assert_finalization(kwargs)
        digest = _sha("final-session:" + self.branch_id)
        return {
            "receipt": {
                "schema_version": "mcp_investigation_session_receipt_v1",
                "branch_id": self.branch_id,
                "formal_write_count": 0,
            },
            "receipt_sha256": digest,
            "receipt_ref": _ref("mcp-investigation-session-receipt", digest),
        }

    def finalize_failed_investigation_read_session(
        self, **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs["branch_id"] != self.branch_id:
            raise AssertionError("failed branch finalizer identity drift")
        if kwargs["status"] not in {"failed", "cancelled", "timed_out"}:
            raise AssertionError("failed branch status was not terminal")
        if kwargs["execution_artifacts"]["formal_write_count"] != 0:
            raise AssertionError("failed branch attempted a formal write")
        digest = _sha("failed-final-session:" + self.branch_id)
        return {
            "receipt": {
                "schema_version": "mcp_investigation_session_receipt_v1",
                "branch_id": self.branch_id,
                "status": kwargs["status"],
                "formal_write_count": 0,
            },
            "receipt_sha256": digest,
            "receipt_ref": _ref(
                "mcp-investigation-session-receipt", digest
            ),
        }

    def assert_finalization(self, kwargs: dict[str, Any]) -> None:
        stage_receipt = kwargs["stage_receipt"]
        if kwargs["branch_id"] != self.branch_id:
            raise AssertionError("branch finalizer identity drift")
        if stage_receipt["branch_id"] != self.branch_id:
            raise AssertionError("stage receipt lost branch identity")
        if stage_receipt["provider_stage_name"] != f"{self.subject}_luna_analysis":
            raise AssertionError("unexpected Luna provider stage")


class _FakeBranchRunner:
    def __init__(
        self,
        *,
        branch_id: str,
        candidate: Candidate,
        probe: _OverlapProbe,
        failures: set[str],
        runtime_root: Path,
    ) -> None:
        self.branch_id = branch_id
        self.candidate = candidate
        self.probe = probe
        self.failures = failures
        self.runtime_root = runtime_root
        self._processing_host = _FakeProcessingHost(
            branch_id, candidate.subject
        )
        self._active_processes: set[Any] = set()
        self._process_lock = threading.Lock()
        self.context: dict[str, Any] | None = None
        self.execute_calls: list[dict[str, Any]] = []

    def _background_context(self, candidate: Candidate) -> dict[str, Any]:
        if candidate is not self.candidate:
            raise AssertionError("branch received a different Candidate")
        session_id = "SESSION-" + self.branch_id.upper()
        session_core = {
            "schema_version": "study-read-mcp-read-session.v4",
            "subject": candidate.subject,
            "read_session_id": session_id,
            "candidate_release_id": "a" * 64,
            "generation": "fixture-generation",
            "authority_fingerprint": _sha("authority:shared"),
            "authority_snapshot_manifest_sha256": _sha(
                "authority-snapshot:shared"
            ),
            "capture_id": candidate.capture_id,
            "artifact_ids": ["artifact-dialogue"],
            "formal_write_count": 0,
        }
        manifest_sha = _json_sha(session_core)
        session_manifest = {
            **session_core,
            "manifest_sha256": manifest_sha,
        }
        manifest_path = (
            self.runtime_root
            / "private"
            / "mcp-read-sessions"
            / "sha256"
            / manifest_sha[:2]
            / f"{manifest_sha}.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                session_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        opened_sha = _sha("opened:" + self.branch_id)
        self.context = {
            "processing_binding": {"binding_sha256": _sha("binding:" + self.branch_id)},
            "mcp_read_session": {
                "subject": candidate.subject,
                "read_session_id": session_id,
                "manifest_sha256": manifest_sha,
                "generation": "fixture-generation",
                "authority_fingerprint": _sha("authority:shared"),
                "authority_snapshot_manifest_sha256": _sha(
                    "authority-snapshot:shared"
                ),
                "artifact_ids": ["artifact-dialogue"],
            },
            "mcp_read_session_receipt": {"formal_write_count": 0},
            "mcp_read_session_receipt_sha256": opened_sha,
            "mcp_read_session_receipt_ref": _ref(
                "mcp-read-session-receipt", opened_sha
            ),
            "capture_freeze_receipt": {"formal_write_count": 0},
            "capture_freeze_receipt_sha256": _sha("freeze:" + self.branch_id),
            "capture_freeze_receipt_ref": _ref(
                "capture-freeze-receipt", _sha("freeze:" + self.branch_id)
            ),
            "processing_skill": {"id": "fixture-skill", "version": "1"},
        }
        return self.context

    def _execute_prompt(self, **kwargs: Any) -> StructuredStageResult:
        self.execute_calls.append(kwargs)
        if kwargs.get("processing_context") is not self.context:
            raise AssertionError("Luna lost its branch-local read context")
        self.probe.enter()
        try:
            if self.branch_id in self.failures:
                raise PreprocessorError("synthetic_luna_branch_failure")
            evidence_ref = (
                f"mcp-item:{self.candidate.subject}:"
                f"{self.branch_id}:full-body"
            )
            raw_sha = _sha("raw:" + self.branch_id)
            stage_sha = _sha("stage:" + self.branch_id)
            normalization_sha = _sha("normalization:" + self.branch_id)
            transcript_sha = _sha("transcript:" + self.branch_id)
            return StructuredStageResult(
                payload={
                    "schema_version": "luna_investigation_draft_v1",
                    "subject": self.candidate.subject,
                    "capture_id": self.candidate.capture_id,
                    "branch_id": self.branch_id,
                    "summary": "FULL-LUNA-BODY-" + self.branch_id,
                    "findings": ["finding-" + self.branch_id],
                    "conflicts": ["conflict-" + self.branch_id],
                    "missing_evidence": ["gap-" + self.branch_id],
                    "confidence": "high",
                    "evidence_refs": [evidence_ref],
                    "formal_write_count": 0,
                },
                duration_ms=7,
                runtime_model="gpt-5.6-luna",
                runtime_reasoning_effort="max",
                runtime_metadata_provenance="fixture_attestation",
                runtime_identity_status="confirmed",
                output_sha256=_sha("output:" + self.branch_id),
                schema_sha256=_sha("schema:luna"),
                raw_output_object_sha256=raw_sha,
                raw_output_object_ref=_ref("model-stage-raw", raw_sha),
                stage_execution_receipt_sha256=stage_sha,
                stage_execution_receipt_ref=_ref(
                    "model-stage-execution-receipt", stage_sha
                ),
                stage_normalization_receipt_sha256=normalization_sha,
                stage_normalization_receipt_ref=_ref(
                    "model-stage-normalization-receipt", normalization_sha
                ),
                stage_name=f"{self.candidate.subject}_luna_analysis",
                semantic_stage_count=1,
                provider_request_count=1,
                mcp_tool_call_count=1,
                mcp_transcript_sha256=transcript_sha,
                mcp_transcript_ref=_ref("mcp-transcript", transcript_sha),
                mcp_calls=(
                    {
                        "sequence": 1,
                        "server": "kaoyan_math_read",
                        "tool": "get_task_context",
                        "arguments": {},
                        "evidence_ref": evidence_ref,
                    },
                ),
            )
        finally:
            self.probe.leave()

    def _stage_receipt(self, result: StructuredStageResult, **kwargs: Any) -> dict[str, Any]:
        if self.context is None:
            raise AssertionError("stage receipt created before read session")
        session = self.context["mcp_read_session"]
        mcp_call_sha = _sha("mcp-call:" + self.branch_id)
        return {
            "status": "ready",
            "branch_id": self.branch_id,
            "provider_stage_name": f"{self.candidate.subject}_luna_analysis",
            "read_session_id": session["read_session_id"],
            "read_session_manifest_sha256": session["manifest_sha256"],
            "evidence_generation": session["generation"],
            "evidence_authority_fingerprint": session["authority_fingerprint"],
            "mcp_call_receipt_sha256": mcp_call_sha,
            "mcp_call_receipt_ref": _ref("mcp-call-receipt", mcp_call_sha),
            "mcp_transcript_sha256": result.mcp_transcript_sha256,
            "mcp_transcript_ref": result.mcp_transcript_ref,
            "raw_output_object_sha256": result.raw_output_object_sha256,
            "raw_output_object_ref": result.raw_output_object_ref,
            "stage_execution_receipt_sha256": result.stage_execution_receipt_sha256,
            "stage_execution_receipt_ref": result.stage_execution_receipt_ref,
            "stage_normalization_receipt_sha256": (
                result.stage_normalization_receipt_sha256
            ),
            "stage_normalization_receipt_ref": result.stage_normalization_receipt_ref,
            "mcp_tool_call_count": 1,
            "provider_request_count": 2,
            "pagination_coverage_complete": True,
            "mcp_read_session_receipt_ref": self.context[
                "mcp_read_session_receipt_ref"
            ],
            "mcp_grounding_manifest": {
                "items": [
                    {
                        "evidence_ref": (
                            f"mcp-item:{self.candidate.subject}:"
                            f"{self.branch_id}:full-body"
                        ),
                        "source_hash": _sha("source:" + self.branch_id),
                    }
                ]
            },
            "formal_write_count": 0,
        }


class _FakeLiveRunner(CodexRunner):
    def __init__(
        self,
        *,
        runtime_root: Path,
        branch_count: int,
        failures: set[str] = frozenset(),
        subject: str = "math",
        capture_id: str = "CAP-LIVE-MULTI-001",
    ) -> None:
        self.branch_count = branch_count
        self.failures = set(failures)
        self.subject = subject
        self.capture_id = capture_id
        self.probe = _OverlapProbe(branch_count)
        self.parent_execute_calls: list[dict[str, Any]] = []
        self.children: dict[str, _FakeBranchRunner] = {}
        self.final_prompt = ""
        super().__init__(
            {
                "execution_mode": "live_authorized",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "max",
                "max_images": 8,
                "authority_release_id": "a" * 64,
                "analysis_package_v2": {
                    "enabled": True,
                    "terra_initial_output_schema": str(
                        ROOT / "schemas/terra-initial-draft-v1.json"
                    ),
                    "luna_investigation_output_schema": str(
                        ROOT / "schemas/luna-investigation-draft-v1.json"
                    ),
                    "terra_final_output_schema": str(
                        ROOT / "schemas/terra-final-draft-v1.json"
                    ),
                    "physical_branch_slots": 4,
                    "max_prompt_bytes": 524288,
                    "max_output_bytes": 524288,
                },
            },
            runtime_root,
        )

    def _new_multi_agent_branch_runner(
        self, candidate: Candidate, branch_id: str
    ) -> _FakeBranchRunner:
        child = _FakeBranchRunner(
            branch_id=branch_id,
            candidate=candidate,
            probe=self.probe,
            failures=self.failures,
            runtime_root=self.runtime_root,
        )
        self.children[branch_id] = child
        return child

    def _multi_agent_skill_binding(self) -> dict[str, str]:
        return {
            "id": "multi-agent-read-orchestrate",
            "version": "fixture-v1",
            "sha256": _sha("fixture orchestrate skill"),
        }

    def _multi_agent_activation_id(self, subject: str) -> str:
        return _sha("activation:" + subject)

    def _execute_prompt(self, **kwargs: Any) -> StructuredStageResult:
        self.parent_execute_calls.append(kwargs)
        if kwargs.get("processing_context") is not None:
            raise AssertionError("Terra must not receive a processing context")
        if kwargs.get("subject") is not None:
            raise AssertionError("Terra must not bind a subject MCP")
        stage_name = kwargs["stage_name"]
        if stage_name == f"{self.subject}_analysis":
            payload = {
                "schema_version": "terra_initial_draft_v1",
                "subject": self.subject,
                "capture_id": self.capture_id,
                "learning_sections": {
                    "task_summary": "fixture task",
                    "known_facts": ["capture is immutable"],
                    "open_questions": ["identity", "method", "relation"],
                },
                "proposed_branches": [
                    {
                        "branch_id": f"branch-{index:02d}",
                        "purpose": f"purpose-{index:02d}",
                        "rationale": f"rationale-{index:02d}",
                        "collection_scope": [{
                            "math": "formal_card_catalog",
                            "cs408": "formal_wrong_item_catalog",
                            "english": "article_catalog",
                        }[self.subject]],
                        "expected_evidence_kinds": ["record"],
                    }
                    for index in range(1, self.branch_count + 1)
                ],
                "warnings": [],
                "formal_write_count": 0,
            }
        elif stage_name == f"{self.subject}_critical_review":
            self.final_prompt = kwargs["prompt"]
            payload = {
                "schema_version": "terra_final_draft_v1",
                "subject": self.subject,
                "capture_id": self.capture_id,
                "branch_assessments": [
                    {
                        "branch_id": f"branch-{index:02d}",
                        "input_kind": (
                            "diagnostic"
                            if f"branch-{index:02d}" in self.failures
                            else "report"
                        ),
                        "disposition": (
                            "request_more_evidence"
                            if f"branch-{index:02d}" in self.failures
                            else "adopt"
                        ),
                        "assessment": f"assessment-{index:02d}",
                        "evidence_refs": [],
                    }
                    for index in range(1, self.branch_count + 1)
                ],
                "subject_sections": {
                    "learning_summary": "final fixture synthesis",
                    "cross_branch_synthesis": "all full bodies considered",
                    "recommended_next_step": "Sol review",
                },
                "proposals": [],
                "conflicts": [],
                "gaps": [],
                "checklist": ["verify evidence"],
                "warnings": [],
                "formal_write_count": 0,
            }
        else:
            raise AssertionError(f"unexpected parent stage: {stage_name}")
        return StructuredStageResult(
            payload=payload,
            duration_ms=11,
            runtime_model="gpt-5.6-terra",
            runtime_reasoning_effort="max",
            runtime_metadata_provenance="fixture_attestation",
            runtime_identity_status="confirmed",
            output_sha256=_sha("output:" + stage_name),
            schema_sha256=_sha("schema:" + stage_name),
            stage_name=stage_name,
            semantic_stage_count=1,
            provider_request_count=1,
            mcp_tool_call_count=0,
        )

    def _stage_receipt(self, result: StructuredStageResult, **kwargs: Any) -> dict[str, Any]:
        raw_sha = _sha("terra-raw:" + str(result.stage_name))
        execution_sha = _sha("terra-execution:" + str(result.stage_name))
        normalization_sha = _sha("terra-normalization:" + str(result.stage_name))
        return {
            "status": "ready",
            "stage_name": result.stage_name,
            "semantic_stage_count": 1,
            "provider_request_count": 1,
            "mcp_tool_call_count": 0,
            "raw_output_object_sha256": raw_sha,
            "raw_output_object_ref": _ref("model-stage-raw-output", raw_sha),
            "stage_execution_receipt_sha256": execution_sha,
            "stage_execution_receipt_ref": _ref(
                "model-stage-execution", execution_sha
            ),
            "stage_normalization_receipt_sha256": normalization_sha,
            "stage_normalization_receipt_ref": _ref(
                "model-stage-normalization", normalization_sha
            ),
            "formal_write_count": 0,
        }


class MultiAgentAnalysisPackageLiveTests(unittest.TestCase):
    def _run(
        self,
        branch_count: int,
        failures: set[str] = frozenset(),
        *,
        subject: str = "math",
        capture_id: str = "CAP-LIVE-MULTI-001",
    ) -> tuple[_FakeLiveRunner, Any, Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        runtime_root = Path(temporary.name)
        runner = _FakeLiveRunner(
            runtime_root=runtime_root,
            branch_count=branch_count,
            failures=failures,
            subject=subject,
            capture_id=capture_id,
        )
        result = runner.run_analysis_package_v2(
            _candidate(subject=subject, capture_id=capture_id)
        )
        return runner, result, runtime_root, temporary

    def test_three_and_four_luna_branches_overlap_with_unique_sessions(self) -> None:
        for branch_count in (3, 4):
            with self.subTest(branch_count=branch_count):
                runner, result, _, temporary = self._run(branch_count)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(runner.probe.maximum_active, branch_count)
                sessions = {
                    child.context["mcp_read_session"]["read_session_id"]
                    for child in runner.children.values()
                    if child.context is not None
                }
                self.assertEqual(len(sessions), branch_count)
                self.assertEqual(len(runner.children), branch_count)
                self.assertEqual(
                    result.pipeline_status,
                    "multi_agent_analysis_package_ready",
                )

    def test_hosted_synthetic_trial_requires_exactly_three_luna_branches(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runner = _FakeLiveRunner(
            runtime_root=Path(temporary.name),
            branch_count=4,
        )
        runner.config["execution_mode"] = "hosted_synthetic"
        with self.assertRaisesRegex(
            PreprocessorError,
            "hosted_synthetic_luna_branch_count_invalid",
        ):
            runner.run_analysis_package_v2(_candidate())
        prompt = runner.parent_execute_calls[0]["prompt"]
        self.assertIn(
            "Propose exactly three independent Luna investigations",
            prompt,
        )
        self.assertNotIn("three or four", prompt)

    def test_each_subject_two_captures_persists_complete_dual_report_sets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runtime_root = Path(folder)
            store = AnalysisPackageStore(runtime_root)
            for subject in ("math", "cs408", "english"):
                expected_capture_ids: list[str] = []
                for ordinal, branch_count in ((1, 3), (2, 4)):
                    capture_id = (
                        f"CAP-V2-{subject.upper()}-{ordinal:02d}"
                    )
                    expected_capture_ids.append(capture_id)
                    failures = {"branch-04"} if branch_count == 4 else set()
                    runner = _FakeLiveRunner(
                        runtime_root=runtime_root,
                        branch_count=branch_count,
                        failures=failures,
                        subject=subject,
                        capture_id=capture_id,
                    )
                    result = runner.run_analysis_package_v2(
                        _candidate(
                            subject=subject,
                            capture_id=capture_id,
                            recorded_at=(
                                "2026-08-24T09:00:"
                                f"{ordinal:02d}+08:00"
                            ),
                        )
                    )
                    self.assertEqual(
                        result.pipeline_status,
                        "multi_agent_analysis_package_ready",
                    )
                    self.assertEqual(
                        len(result.analysis["luna_outputs"]), branch_count
                    )
                    self.assertEqual(
                        len(result.analysis["ordered_luna_reports"]),
                        branch_count - len(failures),
                    )
                    self.assertEqual(
                        len(result.analysis["ordered_luna_diagnostics"]),
                        len(failures),
                    )
                    self.assertEqual(result.analysis["formal_write_count"], 0)
                    self.assertEqual(runner._active_processes, set())
                    self.assertTrue(
                        all(
                            child._active_processes == set()
                            for child in runner.children.values()
                        )
                    )
                packages = store.packages_for(
                    subject=subject,
                    capture_intake_date_value="2026-08-24",
                )
                self.assertEqual(
                    [row["capture_id"] for row in packages],
                    expected_capture_ids,
                )

    def test_terra_is_mcp_free_and_receives_full_reports_and_diagnostics(self) -> None:
        runner, result, _, temporary = self._run(4, {"branch-04"})
        self.addCleanup(temporary.cleanup)
        self.assertEqual(len(runner.parent_execute_calls), 2)
        self.assertTrue(
            all(call.get("processing_context") is None for call in runner.parent_execute_calls)
        )
        self.assertEqual(result.mcp_tool_call_count, 3)
        for index in range(1, 4):
            self.assertIn(f"FULL-LUNA-BODY-branch-{index:02d}", runner.final_prompt)
            self.assertIn(f"conflict-branch-{index:02d}", runner.final_prompt)
        self.assertIn("branch-04", runner.final_prompt)
        self.assertIn('"status": "failed"', runner.final_prompt)
        self.assertEqual(len(result.analysis["ordered_luna_reports"]), 3)
        self.assertEqual(len(result.analysis["ordered_luna_diagnostics"]), 1)

    def test_every_successful_luna_report_has_complete_execution_artifacts(self) -> None:
        runner, result, _, temporary = self._run(4, {"branch-03"})
        self.addCleanup(temporary.cleanup)
        for binding in result.analysis["ordered_luna_reports"]:
            artifacts = binding["execution_artifacts"]
            self.assertTrue(artifacts["read_session_id"])
            self.assertTrue(artifacts["opened_session_receipt_ref"])
            self.assertTrue(artifacts["final_session_receipt_ref"])
            self.assertTrue(artifacts["raw_output_ref"])
            self.assertTrue(artifacts["stage_execution_receipt_ref"])
            self.assertTrue(artifacts["normalization_receipt_ref"])
        for branch_id, child in runner.children.items():
            self.assertEqual(len(child._processing_host.calls), 1)
            if branch_id == "branch-03":
                self.assertEqual(
                    child._processing_host.calls[0]["status"], "failed"
                )
                self.assertEqual(
                    child._processing_host.calls[0]["execution_artifacts"][
                        "formal_write_count"
                    ],
                    0,
                )

    def test_all_failed_branches_stop_before_terra_final_and_publication(self) -> None:
        failures = {"branch-01", "branch-02", "branch-03"}
        with tempfile.TemporaryDirectory() as folder:
            runner = _FakeLiveRunner(
                runtime_root=Path(folder), branch_count=3, failures=failures
            )
            with self.assertRaisesRegex(
                PreprocessorError, "multi_agent_all_luna_failed"
            ):
                runner.run_analysis_package_v2(_candidate())
            self.assertEqual(
                [call["stage_name"] for call in runner.parent_execute_calls],
                ["math_analysis"],
            )
            self.assertEqual(AnalysisPackageStore(Path(folder)).packages_for(
                subject="math", capture_intake_date_value="2026-08-24"
            ), [])

    def test_first_read_session_failure_preserves_the_original_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runner = _FakeLiveRunner(
                runtime_root=Path(folder), branch_count=3
            )

            class BrokenContext:
                def _background_context(self, _candidate):
                    raise PreprocessorError(
                        "background_mcp_client_failed",
                        diagnostic={
                            "returncode": "2",
                            "stderr_tail": "synthetic sealed client failure",
                        },
                    )

            runner._new_multi_agent_branch_runner = (  # type: ignore[method-assign]
                lambda _candidate, _branch_id: BrokenContext()
            )
            with self.assertRaisesRegex(
                PreprocessorError, "background_mcp_client_failed"
            ) as raised:
                runner.run_analysis_package_v2(_candidate())
            self.assertEqual(
                raised.exception.diagnostic,
                {
                    "returncode": "2",
                    "stderr_tail": "synthetic sealed client failure",
                },
            )

    def test_result_topology_persistence_reopen_and_process_closure(self) -> None:
        runner, result, runtime_root, temporary = self._run(4, {"branch-04"})
        self.addCleanup(temporary.cleanup)
        self.assertEqual(result.pipeline_status, "multi_agent_analysis_package_ready")
        self.assertEqual(result.draft_analysis["schema_version"], "terra_initial_analysis_v1")
        self.assertEqual(result.critical_review["schema_version"], "terra_final_report_v2")
        self.assertEqual(result.semantic_stage_count, 5)
        self.assertEqual(result.provider_request_count, 9)
        self.assertEqual(result.mcp_tool_call_count, 3)
        self.assertEqual(
            list(result.stage_receipts),
            [
                "terra_initial",
                "luna_investigations",
                "terra_final",
                "analysis_package_stages",
            ],
        )
        investigations = result.stage_receipts["luna_investigations"]
        self.assertEqual(list(investigations), [
            "branch-01", "branch-02", "branch-03", "branch-04"
        ])
        self.assertEqual(investigations["branch-04"]["status"], "failed")
        topology = result.stage_receipts["analysis_package_stages"]
        self.assertEqual(len(topology), 6)
        self.assertEqual(topology[0]["stage"], "terra_initial")
        self.assertEqual(topology[-1]["stage"], "terra_final")
        self.assertEqual(result.analysis["formal_write_count"], 0)
        reopened = reopen_analysis_package_v2(
            AnalysisPackageStore(runtime_root), result.analysis["package_sha256"]
        )
        self.assertEqual(
            reopened,
            {
                key: value
                for key, value in result.analysis.items()
                if key not in {"package_sha256", "package_ref"}
            },
        )
        self.assertEqual(runner._active_processes, set())
        self.assertTrue(
            all(child._active_processes == set() for child in runner.children.values())
        )


if __name__ == "__main__":
    unittest.main()
