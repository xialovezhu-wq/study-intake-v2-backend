#!/usr/bin/env python3

"""Current-canonical, zero-model replay of the three subject chain.

Every value used by this module is generated from the scenario name and is
written below a unittest temporary directory.  The test deliberately does not
replay an old machine, release, or private runtime tree.  The production
reopen assertion delegates to the repository's existing portable plugin test
fixture, which is synthetic and also lives below a temporary directory.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import preprocessor_core as core  # noqa: E402
from processing_plugin import ProcessingPluginError  # noqa: E402


SUBJECTS = ("math", "cs408", "english")
STAGES = ("analysis", "critical_review")


_SYNTHETIC_CS408_REF = core.model_mcp_item_ref(
    subject="cs408",
    generation="synthetic-cs408-compat-v1",
    collection="synthetic_subject_evidence",
    stable_id="cs408-compat-item",
    source_hash=core.sha256_value(
        {"fixture": "synthetic-cs408-compat-v1"}
    ),
)


# A small compatibility surface is kept for the canary-scanner tests that
# import this module's old helper names.  The values are sentinels, not paths;
# _payload materializes fresh synthetic current-core data on every call.
SEALED = {
    "cs408": {
        "analysis_output": {"synthetic_kind": "cs408_analysis"},
        "critical_output": {"synthetic_kind": "cs408_critical_review"},
    }
}


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AssertionError("synthetic payload sentinel is not a mapping")
    kind = value.get("synthetic_kind")
    if kind not in {"cs408_analysis", "cs408_critical_review"}:
        raise AssertionError("unknown synthetic payload sentinel")
    from tests import test_preprocessor as cs408_support

    analysis = cs408_support.v2_analysis(_SYNTHETIC_CS408_REF, complete=True)
    if kind == "cs408_analysis":
        return analysis
    return {
        "schema_version": "study-intake-luna-critical-review-v2",
        "verdict": "pass",
        "summary": "synthetic portable critical review",
        "revised_analysis": copy.deepcopy(analysis),
        "unsupported_claims": [],
        "evidence_misreads": [],
        "answer_safety_findings": [],
        "missing_analysis": [],
        "required_corrections": [],
        "sol_priority_checks": [],
        "correction_resolutions": [],
    }


def _plugin_sha256(value: Mapping[str, Any]) -> str:
    """Match the plugin-owned canonical JSON plus LF hash convention."""

    return hashlib.sha256(core.canonical_bytes(value) + b"\n").hexdigest()


def _write_plugin_json(root: Path, value: Mapping[str, Any]) -> tuple[str, Path]:
    payload = core.canonical_bytes(value) + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    path = root / "sha256" / digest[:2] / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise AssertionError("synthetic plugin object hash drift")
    if json.loads(path.read_text(encoding="utf-8")) != dict(value):
        raise AssertionError("synthetic plugin object round-trip drift")
    return digest, path


def _synthetic_call(
    *, subject: str, generation: str, stage: str, sequence: int
) -> tuple[dict[str, Any], str]:
    source_hash = core.sha256_value(
        {
            "fixture": "three-subject-current-canonical-v1",
            "subject": subject,
            "stage": stage,
            "sequence": sequence,
        }
    )
    collection = "synthetic_subject_evidence"
    stable_id = f"{subject}-{stage}-item-{sequence}"
    evidence_ref = core.model_mcp_item_ref(
        subject=subject,
        generation=generation,
        collection=collection,
        stable_id=stable_id,
        source_hash=source_hash,
    )
    item = {
        "collection": collection,
        "stable_id": stable_id,
        "source_hash": source_hash,
        "data_role": "synthetic_fixture",
        "evidence_ref": evidence_ref,
    }
    arguments = {
        "collection": "synthetic_subject_evidence",
        "page_size": 1,
        "cursor": None,
        "stage": stage,
    }
    result = {
        "ok": True,
        "schema_version": "study-read-mcp.v3",
        "subject": subject,
        "generation": generation,
        "items": [item],
        "formal_write_count": 0,
        "model_call_count": 0,
        "mcp_tool_call_count": 1,
    }
    call = {
        "sequence": sequence,
        "server": f"synthetic_{subject}_read",
        "tool": "list_records",
        "arguments": arguments,
        "arguments_sha256": core.sha256_value(arguments),
        "result": result,
        "result_sha256": core.sha256_value(result),
    }
    return call, evidence_ref


def _synthetic_stage(
    *, runtime: Path, subject: str, generation: str, stage: str
) -> tuple[core.StructuredStageResult, str]:
    call, evidence_ref = _synthetic_call(
        subject=subject,
        generation=generation,
        stage=stage,
        sequence=1 if stage == "analysis" else 2,
    )
    transcript = {
        "schema_version": "synthetic-model-driven-mcp-stage-transcript-v1",
        "fixture": "three-subject-current-canonical-v1",
        "subject": subject,
        "stage_name": f"{subject}_{stage}",
        "generation": generation,
        "calls": [call],
        "coverage": {
            "call_count": 1,
            "all_returned_pages_consumed": True,
            "unresolved_next_cursors": [],
            "duplicate_argument_count": 0,
            "host_semantic_prefetch": False,
        },
        "semantic_stage_count": 1,
        "provider_request_count": 2,
        "mcp_tool_call_count": 1,
        "model_call_count": 1,
        "formal_write_count": 0,
    }
    transcript_sha, transcript_path = _write_plugin_json(
        runtime / "synthetic-stage-transcripts", transcript
    )
    stage_payload = {
        "subject": subject,
        "stage": stage,
        "evidence_ref": evidence_ref,
        "fixture": "synthetic-current-canonical",
        "formal_write_count": 0,
        "proposal_only": True,
    }
    stage_result = core.StructuredStageResult(
        payload=copy.deepcopy(stage_payload),
        duration_ms=0,
        runtime_model=None,
        runtime_reasoning_effort=None,
        runtime_metadata_provenance="synthetic_fixture",
        runtime_identity_status="requested_unverified",
        output_sha256=core.sha256_value(stage_payload),
        stage_name=f"{subject}_{stage}",
        semantic_stage_count=1,
        provider_request_count=2,
        mcp_tool_call_count=1,
        mcp_transcript_sha256=transcript_sha,
        mcp_transcript_ref=(
            "study-intake-mcp-stage-transcript://sha256/" + transcript_sha
        ),
        mcp_calls=(copy.deepcopy(call),),
    )
    if transcript_path.stem != transcript_sha:
        raise AssertionError("synthetic transcript address mismatch")
    return stage_result, evidence_ref


def _synthetic_candidate(
    *, subject: str, capture_id: str, allowed_refs: tuple[str, ...]
) -> core.Candidate:
    return core.Candidate(
        subject=subject,
        capture_id=capture_id,
        study_date="2026-08-20",
        recorded_at="2026-08-20T00:00:00Z",
        input_fingerprint=core.sha256_value(
            {"fixture": "synthetic-current-canonical", "capture_id": capture_id}
        ),
        input_binding={
            "candidate_kind": "failure_capture",
            "source_kind": "synthetic_fixture",
            "image_evidence_refs": [],
        },
        model_input={"candidate_kind": "failure_capture"},
        allowed_evidence_refs=allowed_refs,
        image_paths=(),
        target_label=capture_id,
        canonical_state="awaiting_daily_curation",
        sol_state="pending_review",
    )


class ThreeSubjectSealedChainReplayTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="three-subject-synthetic-chain-"
        )
        self.runtime = Path(self.temporary.name) / "runtime"
        self.runtime.mkdir(parents=True)
        self.hmac_key = b"synthetic-three-subject-key-32b!"
        if len(self.hmac_key) != 32:
            raise AssertionError("synthetic HMAC key must be 32 bytes")
        self.candidate_release_id = core.sha256_value(
            {"fixture": "current-canonical-synthetic-release"}
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _english_analysis_fixture(
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        """Return a synthetic English grounding fragment for canary tests."""

        return {
            "items": [
                {
                    "item_id": "EN-SYNTHETIC-ITEM-001",
                    "sequence": 1,
                    "item": "synthetic",
                    "candidate_type": "单词",
                    "candidate_status": "familiarity_candidate",
                    "tier": "A",
                    "source_event_id": "SYNTHETIC-EN-EVENT-001",
                    "bank_status": "needs_check",
                    "bank_match_ids": [],
                    "mastered_status": "clear",
                    "mastery_proposal": None,
                    "grounding": {
                        "status": "passed",
                        "user_evidence_ref": "SYNTHETIC-EN-EVENT-001",
                        "writing_pattern": {
                            "status": "not_requested",
                            "reference_ids": [],
                            "note": "synthetic fixture",
                        },
                        "writing_vocabulary": {
                            "status": "not_requested",
                            "reference_ids": [],
                            "note": "synthetic fixture",
                        },
                        "syllabus_occurrence": {
                            "status": "not_requested",
                            "reference_ids": [],
                            "note": "synthetic fixture",
                        },
                        "sentence_pattern": {
                            "status": "not_requested",
                            "reference_ids": [],
                            "note": "synthetic fixture",
                        },
                        "old_word_sources": [],
                        "naturalness_check": "not requested in synthetic fixture",
                        "mcp_evidence_refs": list(evidence_refs),
                    },
                    "card": {
                        "meaning": "synthetic candidate meaning",
                        "source_translation": "synthetic source-bound candidate",
                        "usage": "adjective",
                        "review_note": "Sol must independently verify this proposal.",
                        "adopted_pattern": "",
                        "old_word_example": "",
                        "example_translation": "",
                        "structure_breakdown": "",
                        "review_old_words": [],
                    },
                }
            ]
        }

    @staticmethod
    def _fix_cs408_review(value: Mapping[str, Any]) -> dict[str, Any]:
        """Keep the canary compatibility helper current-core and synthetic."""

        fixed = copy.deepcopy(dict(value))
        old_ref = "analysis.question_structure.mechanism_structure[0]чаты?"
        exact_ref = "analysis.question_structure.mechanism_structure[0]"
        for section in (
            "unsupported_claims",
            "evidence_misreads",
            "answer_safety_findings",
            "missing_analysis",
            "required_corrections",
            "sol_priority_checks",
        ):
            for row in fixed.get(section) or []:
                row["analysis_refs"] = [
                    exact_ref if ref == old_ref else ref
                    for ref in row.get("analysis_refs", [])
                ]
        return fixed

    @staticmethod
    def _assert_formal_zero(value: Any) -> None:
        if isinstance(value, Mapping):
            if "formal_write_count" in value:
                if value["formal_write_count"] != 0:
                    raise AssertionError("synthetic fixture wrote formal data")
            for nested in value.values():
                ThreeSubjectSealedChainReplayTests._assert_formal_zero(nested)
        elif isinstance(value, list):
            for nested in value:
                ThreeSubjectSealedChainReplayTests._assert_formal_zero(nested)

    def _session(self, subject: str, capture_id: str) -> dict[str, Any]:
        seed = {"fixture": "synthetic-session-v1", "subject": subject}
        manifest_sha = core.sha256_value({**seed, "artifact": "manifest"})
        session_id = "SYNTH-" + core.sha256_value({**seed, "artifact": "id"})[:24]
        return {
            "subject": subject,
            "capture_id": capture_id,
            "read_session_id": session_id,
            "manifest_sha256": manifest_sha,
            "generation": f"synthetic-generation-{subject}-v1",
            "authority_fingerprint": core.sha256_value(
                {**seed, "artifact": "authority"}
            ),
            "authority_snapshot_manifest_sha256": core.sha256_value(
                {**seed, "artifact": "authority-snapshot"}
            ),
            "capture_freeze_receipt_sha256": core.sha256_value(
                {**seed, "artifact": "capture-freeze"}
            ),
            "opened_receipt_sha256": core.sha256_value(
                {**seed, "artifact": "opened-session"}
            ),
        }

    def _processing_binding(
        self, *, subject: str, capture_id: str, session: Mapping[str, Any]
    ) -> dict[str, Any]:
        core_binding = {
            "schema_version": "processing_binding_v2",
            "subject": subject,
            "capture_id": capture_id,
            "candidate_release_id": self.candidate_release_id,
            "read_session_id": session["read_session_id"],
            "read_session_manifest_sha256": session["manifest_sha256"],
            "plugin": {
                "id": "kaoyan-study-intake-synthetic",
                "version": "current-canonical-synthetic-v1",
            },
            "host_semantic_prefetch": False,
            "formal_write_count": 0,
        }
        return {
            **core_binding,
            "binding_sha256": _plugin_sha256(core_binding),
        }

    def _stage_receipt(
        self,
        *,
        subject: str,
        stage: str,
        result: core.StructuredStageResult,
        session: Mapping[str, Any],
        binding: Mapping[str, Any],
        result_sha256: str,
    ) -> dict[str, Any]:
        grounding = core.mcp_grounding_manifest((result,))
        call_receipt = {
            "schema_version": "synthetic-mcp-call-receipt-v1",
            "subject": subject,
            "stage": stage,
            "transcript_sha256": result.mcp_transcript_sha256,
            "calls_sha256": core.sha256_value(list(result.mcp_calls)),
            "formal_write_count": 0,
        }
        call_sha, _ = _write_plugin_json(
            self.runtime / "synthetic-call-receipts", call_receipt
        )
        return {
            "status": "ready",
            "prompt_version": f"synthetic_{subject}_{stage}_v1",
            "prompt_sha256": core.sha256_value(
                {"subject": subject, "stage": stage, "kind": "prompt"}
            ),
            "schema_sha256": core.sha256_value(
                {"subject": subject, "stage": stage, "kind": "schema"}
            ),
            "result_sha256": result_sha256,
            "output_sha256": result.output_sha256,
            "duration_ms": 0,
            "requested_model": "gpt-5.6-luna",
            "requested_reasoning_effort": "max",
            "runtime_model": None,
            "runtime_reasoning_effort": None,
            "runtime_metadata_provenance": "synthetic_fixture",
            "runtime_identity_status": "requested_unverified",
            "semantic_stage_count": 1,
            "provider_request_count": result.provider_request_count,
            "mcp_tool_call_count": result.mcp_tool_call_count,
            "model_call_count": 1,
            "formal_write_count": 0,
            "processing_binding": copy.deepcopy(dict(binding)),
            "processing_binding_sha256": binding["binding_sha256"],
            "capture_freeze_receipt_sha256": session[
                "capture_freeze_receipt_sha256"
            ],
            "capture_freeze_receipt_ref": (
                "study-intake-capture-freeze://sha256/"
                + session["capture_freeze_receipt_sha256"]
            ),
            "mcp_read_session_receipt_sha256": session["opened_receipt_sha256"],
            "mcp_read_session_receipt_ref": (
                "study-intake-mcp-read-session://sha256/"
                + session["opened_receipt_sha256"]
            ),
            "read_session_id": session["read_session_id"],
            "read_session_manifest_sha256": session["manifest_sha256"],
            "authority_snapshot_manifest_sha256": session[
                "authority_snapshot_manifest_sha256"
            ],
            "evidence_generation": session["generation"],
            "evidence_authority_fingerprint": session[
                "authority_fingerprint"
            ],
            "mcp_transcript_sha256": result.mcp_transcript_sha256,
            "mcp_transcript_ref": result.mcp_transcript_ref,
            "mcp_call_receipt_sha256": call_sha,
            "mcp_call_receipt_ref": (
                "study-intake-mcp-read-session-call://sha256/" + call_sha
            ),
            "pagination_coverage_complete": True,
            "mcp_grounding_manifest": grounding,
            "mcp_grounding_manifest_sha256": grounding["manifest_sha256"],
            "host_semantic_prefetch": False,
            "consumed_terminal_duplicate_read_count": 0,
        }

    def _final_receipt(
        self,
        *,
        subject: str,
        session: Mapping[str, Any],
        binding: Mapping[str, Any],
        stages: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, Any], str, Path]:
        unsigned = {
            "schema_version": "synthetic-mcp-read-session-receipt-v1",
            "phase": "complete",
            "subject": subject,
            "processing_binding_sha256": binding["binding_sha256"],
            "read_session_id": session["read_session_id"],
            "read_session_manifest_sha256": session["manifest_sha256"],
            "generation": session["generation"],
            "authority_fingerprint": session["authority_fingerprint"],
            "analysis_mcp_transcript_sha256": stages["analysis"][
                "mcp_transcript_sha256"
            ],
            "critical_review_mcp_transcript_sha256": stages[
                "critical_review"
            ]["mcp_transcript_sha256"],
            "model_mcp_tool_call_count": 2,
            "provider_request_count": 4,
            "provider_request_count_status": "synthetic_fixture_no_model",
            "pagination_coverage_complete": True,
            "formal_write_count": 0,
        }
        receipt = {
            **unsigned,
            "hmac_sha256": hmac.new(
                self.hmac_key,
                core.canonical_bytes(unsigned),
                hashlib.sha256,
            ).hexdigest(),
        }
        receipt_sha, receipt_path = _write_plugin_json(
            self.runtime / "synthetic-final-read-session-receipts", receipt
        )
        return (
            {
                "status": "complete",
                "receipt": receipt,
                "receipt_sha256": receipt_sha,
                "receipt_ref": "study-intake-mcp-read-session://sha256/"
                + receipt_sha,
                "model_mcp_tool_call_count": 2,
                "provider_request_count": 4,
                "provider_request_count_status": "synthetic_fixture_no_model",
                "pagination_coverage_complete": True,
                "formal_write_count": 0,
            },
            receipt_sha,
            receipt_path,
        )

    def _verify_final_hmac(self, receipt: Mapping[str, Any]) -> None:
        unsigned = {
            key: copy.deepcopy(value)
            for key, value in receipt.items()
            if key != "hmac_sha256"
        }
        expected = hmac.new(
            self.hmac_key,
            core.canonical_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(str(receipt.get("hmac_sha256") or ""), expected):
            raise ValueError("synthetic_final_receipt_hmac_invalid")

    def _build_subject_chain(self, subject: str, ordinal: int) -> dict[str, Any]:
        capture_id = f"SYNTH-{subject.upper()}-{ordinal:03d}"
        session = self._session(subject, capture_id)
        analysis_stage, analysis_ref = _synthetic_stage(
            runtime=self.runtime,
            subject=subject,
            generation=session["generation"],
            stage="analysis",
        )
        review_stage, review_ref = _synthetic_stage(
            runtime=self.runtime,
            subject=subject,
            generation=session["generation"],
            stage="critical_review",
        )
        analysis = copy.deepcopy(analysis_stage.payload)
        review = copy.deepcopy(review_stage.payload)
        if subject == "english":
            review["verdict"] = "confirmed"
        else:
            review["verdict"] = "pass"
            review["revised_analysis"] = copy.deepcopy(analysis)
        core.validate_model_stage_mcp_grounding(
            analysis,
            analysis_stage,
            subject=subject,
            stage_name=f"{subject}_analysis",
        )
        core.validate_model_stage_mcp_grounding(
            review,
            review_stage,
            subject=subject,
            stage_name=f"{subject}_critical_review",
            allowed_prior_refs=(analysis_ref,),
        )
        allowed_refs = tuple(sorted({analysis_ref, review_ref}))
        candidate = _synthetic_candidate(
            subject=subject,
            capture_id=capture_id,
            allowed_refs=allowed_refs,
        )
        binding = self._processing_binding(
            subject=subject, capture_id=capture_id, session=session
        )
        stages: dict[str, Any] = {
            "analysis": self._stage_receipt(
                subject=subject,
                stage="analysis",
                result=analysis_stage,
                session=session,
                binding=binding,
                result_sha256=core.sha256_value(analysis),
            ),
            "critical_review": self._stage_receipt(
                subject=subject,
                stage="critical_review",
                result=review_stage,
                session=session,
                binding=binding,
                result_sha256=core.sha256_value(review),
            ),
        }
        final_stage, final_sha, final_path = self._final_receipt(
            subject=subject,
            session=session,
            binding=binding,
            stages=stages,
        )
        stages["read_session"] = final_stage
        publication = core.processing_publication_fields(stages)
        result = core.ModelResult(
            analysis=copy.deepcopy(analysis),
            duration_ms=0,
            runtime_model=None,
            runtime_reasoning_effort=None,
            runtime_metadata_provenance="synthetic_fixture",
            pipeline_status="two_pass_ready",
            draft_analysis=copy.deepcopy(analysis),
            critical_review=copy.deepcopy(review),
            stage_receipts=copy.deepcopy(stages),
            semantic_stage_count=2,
            provider_request_count=0,
            mcp_tool_call_count=2,
        )
        subject_payload_sha = core.sha256_value(analysis)
        proposal = core.build_luna_proposal_v2(
            candidate=candidate,
            result=result,
            subject_payload_sha256=subject_payload_sha,
        )
        package = {
            "schema_version": "synthetic-three-subject-package-v1",
            "subject": subject,
            "capture_id": capture_id,
            "draft_analysis": copy.deepcopy(analysis),
            "critical_review": copy.deepcopy(review),
            "analysis": copy.deepcopy(analysis),
            "stage_receipts": copy.deepcopy(stages),
            "allowed_evidence_refs": list(allowed_refs),
            "luna_proposal": copy.deepcopy(proposal),
            "luna_proposal_sha256": core.sha256_value(proposal),
            "executed_model_call_count": 0,
            "formal_write_count": 0,
            **publication,
        }
        core.validate_package_luna_proposal(
            package, subject_payload_sha256=subject_payload_sha
        )
        self._assert_formal_zero(
            {
                "analysis": analysis,
                "review": review,
                "stages": stages,
                "proposal": proposal,
                "package": package,
            }
        )
        self._verify_final_hmac(final_stage["receipt"])
        return {
            "subject": subject,
            "capture_id": capture_id,
            "session": session,
            "analysis_stage": analysis_stage,
            "review_stage": review_stage,
            "stages": stages,
            "publication": publication,
            "proposal": proposal,
            "package": package,
            "final_receipt": final_stage["receipt"],
            "final_receipt_sha256": final_sha,
            "final_receipt_path": final_path,
        }

    def test_current_canonical_synthetic_tamper_and_grounding_fail_closed(
        self,
    ) -> None:
        chain = self._build_subject_chain("math", 1)

        tampered_binding = copy.deepcopy(chain["stages"])
        tampered_binding["analysis"]["processing_binding"]["binding_sha256"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(
            core.PreprocessorError, "^processing_stage_binding_incomplete$"
        ):
            core.processing_publication_fields(tampered_binding)

        tampered_final = copy.deepcopy(chain["final_receipt"])
        tampered_final["provider_request_count"] = 99
        with self.assertRaisesRegex(
            ValueError, "^synthetic_final_receipt_hmac_invalid$"
        ):
            self._verify_final_hmac(tampered_final)

        ungrounded = copy.deepcopy(chain["package"])
        ungrounded["draft_analysis"]["evidence_ref"] = (
            "mcp-item:math:" + "f" * 64
        )
        ungrounded["analysis"] = copy.deepcopy(ungrounded["draft_analysis"])
        with self.assertRaisesRegex(
            core.PreprocessorError, "^luna_proposal_mcp_grounding_invalid$"
        ):
            core.build_luna_proposal_v2(
                candidate=_synthetic_candidate(
                    subject="math",
                    capture_id=chain["capture_id"],
                    allowed_refs=tuple(chain["package"]["allowed_evidence_refs"]),
                ),
                result=core.ModelResult(
                    analysis=ungrounded["analysis"],
                    duration_ms=0,
                    runtime_model=None,
                    runtime_reasoning_effort=None,
                    runtime_metadata_provenance="synthetic_fixture",
                    draft_analysis=ungrounded["draft_analysis"],
                    critical_review=ungrounded["critical_review"],
                    stage_receipts=ungrounded["stage_receipts"],
                ),
                subject_payload_sha256=core.sha256_value(ungrounded["analysis"]),
            )

    def test_synthetic_three_subject_chain_has_six_stages_and_three_proposals(
        self,
    ) -> None:
        closures = [
            self._build_subject_chain(subject, ordinal)
            for ordinal, subject in enumerate(SUBJECTS, start=1)
        ]
        self.assertEqual({row["subject"] for row in closures}, set(SUBJECTS))
        self.assertEqual(
            len({row["session"]["read_session_id"] for row in closures}), 3
        )
        self.assertEqual(
            len(
                {
                    stage_sha
                    for row in closures
                    for stage_sha in (
                        row["stages"]["analysis"]["mcp_transcript_sha256"],
                        row["stages"]["critical_review"]["mcp_transcript_sha256"],
                    )
                }
            ),
            6,
        )
        self.assertEqual(
            len({row["final_receipt_sha256"] for row in closures}), 3
        )
        self.assertEqual(
            [row["proposal"]["review_status"] for row in closures],
            ["proposal_ready", "proposal_ready", "proposal_ready"],
        )
        for row in closures:
            self.assertEqual(row["proposal"]["formal_write_count"], 0)
            self.assertEqual(row["package"]["formal_write_count"], 0)
            self.assertEqual(row["package"]["executed_model_call_count"], 0)
            self.assertEqual(
                row["proposal"]["operations"][0]["operation"], "needs_review"
            )
            self.assertEqual(
                row["stages"]["analysis"]["read_session_id"],
                row["stages"]["critical_review"]["read_session_id"],
            )

    def test_three_subject_production_read_session_reopens_and_rejects_tamper(
        self,
    ) -> None:
        """Reopen the current portable host's synthetic artifacts for all subjects."""

        from tests.test_processing_plugin import ProcessingPluginHostTests

        from tests.test_three_subject_production_quality_closure import (
            ThreeSubjectProductionQualityClosureTests,
        )

        portable_mcp_python = (
            ThreeSubjectProductionQualityClosureTests._configure_portable_mcp_python()
        )
        ProcessingPluginHostTests.portable_mcp_python = portable_mcp_python
        for subject in SUBJECTS:
            with self.subTest(subject=subject):
                production = ProcessingPluginHostTests(methodName="runTest")
                production.setUp()
                try:
                    publication, stage_receipts = production._published_read_session(
                        subject
                    )
                    reopened = production.host.reopen_published_read_session(
                        subject=subject,
                        publication=publication,
                        stage_receipts=stage_receipts,
                    )
                    self.assertEqual(
                        set(reopened["stage_calls"]),
                        set(STAGES),
                    )
                    self.assertEqual(
                        len(reopened["stage_calls"]["analysis"]), 1
                    )
                    self.assertEqual(
                        len(reopened["stage_calls"]["critical_review"]), 1
                    )
                    final_stage = stage_receipts["read_session"]
                    validated = production.host.validate_final_model_read_session(
                        subject=subject,
                        context=reopened["context"],
                        finalized={
                            "receipt": copy.deepcopy(final_stage["receipt"]),
                            "receipt_sha256": final_stage["receipt_sha256"],
                            "receipt_ref": final_stage["receipt_ref"],
                        },
                    )
                    self.assertEqual(validated["phase"], "complete")
                    self.assertEqual(validated["formal_write_count"], 0)
                    tampered = copy.deepcopy(final_stage)
                    tampered["receipt"]["provider_request_count"] += 1
                    with self.assertRaisesRegex(
                        ProcessingPluginError,
                        "mcp_read_session_final_receipt_invalid",
                    ):
                        production.host.validate_final_model_read_session(
                            subject=subject,
                            context=reopened["context"],
                            finalized={
                                "receipt": tampered["receipt"],
                                "receipt_sha256": tampered["receipt_sha256"],
                                "receipt_ref": tampered["receipt_ref"],
                            },
                        )
                finally:
                    production.tearDown()


if __name__ == "__main__":
    unittest.main()
