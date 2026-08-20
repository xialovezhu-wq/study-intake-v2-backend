"""Isolated synthetic fixtures for the English A03/A04 replay tests.

The historical replay tests used to reopen private deployment artifacts.  This
module deliberately builds only small, deterministic contract fixtures in a
temporary directory.  Nothing here reads a deployment, release, user vault,
model output, or Capture artifact.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from preprocessor_core import sha256_value  # noqa: E402
from processing_plugin import (  # noqa: E402
    ProcessingPluginHost,
    _sha256_json_value,
)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = _canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise AssertionError(f"synthetic JSON hash self-check failed: {path}")
    return digest


def _write_hashed_json(base: Path, value: Mapping[str, Any]) -> tuple[Path, str]:
    payload = _canonical_json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    path = base / digest[:2] / f"{digest}.json"
    actual = _write_json(path, value)
    if actual != digest or path.stem != digest:
        raise AssertionError(f"synthetic content address self-check failed: {path}")
    return path, digest


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _mcp_ref(*, stage: str, ordinal: int, collection: str) -> str:
    return "mcp-item:english:" + _sha256_json_value(
        {
            "stage": stage,
            "ordinal": ordinal,
            "collection": collection,
            "fixture": "synthetic-a03-a04-v1",
        }
    )


def _grounding_manifest(
    calls: list[dict[str, Any]], *, transcript_sha256: str
) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for call in calls:
        result = call["result"]
        for item in result.get("items") or []:
            evidence_ref = str(item["evidence_ref"])
            row = entries.setdefault(
                evidence_ref,
                {
                    "evidence_ref": evidence_ref,
                    "subject": result["subject"],
                    "generation": result["generation"],
                    "collection": item["collection"],
                    "stable_id": item["stable_id"],
                    "source_hash": item["source_hash"],
                    "data_role": item["data_role"],
                    "consumed_in": [],
                },
            )
            row["consumed_in"].append(
                {
                    "transcript_sha256": transcript_sha256,
                    "call_sequence": call["sequence"],
                    "result_sha256": call["result_sha256"],
                }
            )
    core = {
        "schema_version": "model_mcp_grounding_manifest_v1",
        "items": [entries[key] for key in sorted(entries)],
        "item_count": len(entries),
        "host_semantic_prefetch": False,
        "formal_write_count": 0,
    }
    return {**core, "manifest_sha256": _sha256_json_value(core)}


def _a03_context(
    runtime_root: Path, *, candidate_release_id: str
) -> tuple[ProcessingPluginHost, dict[str, Any]]:
    key_path = runtime_root.parent / "synthetic-authority-key"
    key_path.write_bytes(b"synthetic-a03-authority-key-32-b")
    # The exact key bytes are synthetic and are intentionally local to this
    # temporary fixture.  Keep the same permission contract as production.
    os.chmod(key_path, 0o600)

    session = {
        "schema_version": "study-read-mcp-read-session.v3",
        "read_session_id": "synthetic-a03-read-session",
        "manifest_sha256": _digest("synthetic-a03-manifest"),
        "generation": "english-synthetic-generation-v1",
        "authority_fingerprint": _digest("synthetic-a03-authority"),
        "mcp_server_release": "english-synthetic-mcp-v1",
        "capture_id": "EN-SYNTHETIC-A03",
        "capture_manifest_sha256": _digest("synthetic-a03-capture-manifest"),
        "artifact_ids": ["capture-facts", "question-image"],
        "candidate_release_id": candidate_release_id,
        "subject": "english",
        "skill_id": "background-english-processing",
        "skill_version": "synthetic-v1",
        "plugin_version": "synthetic-plugin-v1",
        "authority_snapshot_manifest_sha256": None,
        "authority_snapshot_receipt_sha256": None,
        "formal_write_count": 0,
    }
    processing_binding = {
        "schema_version": "synthetic-processing-binding-v1",
        "subject": "english",
        "capture_id": session["capture_id"],
        "candidate_release_id": candidate_release_id,
    }
    processing_binding_sha256 = _sha256_json_value(processing_binding)
    capture_freeze_sha256 = _digest("synthetic-a03-capture-freeze")
    read_receipt_sha256 = _digest("synthetic-a03-read-session-receipt")
    context = {
        "mcp_read_session": session,
        "processing_binding": processing_binding,
        "processing_binding_sha256": processing_binding_sha256,
        "capture_freeze_receipt_sha256": capture_freeze_sha256,
        "capture_freeze_receipt_ref": (
            "study-intake-capture-freeze://sha256/" + capture_freeze_sha256
        ),
        "mcp_read_session_receipt_sha256": read_receipt_sha256,
        "mcp_read_session_receipt_ref": (
            "study-intake-mcp-read-session://sha256/" + read_receipt_sha256
        ),
    }

    host = object.__new__(ProcessingPluginHost)
    host.runtime_root = runtime_root.resolve()
    host.candidate_release_id = candidate_release_id
    host.require_authority_snapshot = False
    host.authority_key_path = key_path.resolve()
    host.validate_read_session_context = (  # type: ignore[method-assign]
        lambda *, subject, context: copy.deepcopy(dict(context))
    )
    return host, context


def _a03_call(
    *,
    stage: str,
    ordinal: int,
    session: Mapping[str, Any],
) -> dict[str, Any]:
    tool = (
        "get_task_context"
        if ordinal == 1
        else "read_task_artifact"
        if ordinal == 2
        else "search_records"
    )
    arguments = {
        "fixture_stage": stage,
        "ordinal": ordinal,
        "page_size": 1,
    }
    collections = ("task_artifact", "article_catalog")
    items = []
    for collection in collections:
        stable_id = f"{stage}-{collection}-{ordinal}"
        source_hash = _digest(f"{stage}:{collection}:{ordinal}:source")
        items.append(
            {
                "collection": collection,
                "stable_id": stable_id,
                "source_hash": source_hash,
                "data_role": "synthetic_fixture",
                "evidence_ref": _mcp_ref(
                    stage=stage, ordinal=ordinal, collection=collection
                ),
            }
        )
    route = {
        "caller_skill_id": session["skill_id"],
        "caller_skill_version": session["skill_version"],
        "plugin_version": session["plugin_version"],
        "route_request_id": session["read_session_id"],
        "evidence_scope_hash": session["manifest_sha256"],
        "read_route": "mcp_model_driven",
        "read_session_id": session["read_session_id"],
        "consumed_duplicate_read_count": 0,
    }
    read_session = {
        key: copy.deepcopy(session[key])
        for key in (
            "read_session_id",
            "manifest_sha256",
            "capture_id",
            "capture_manifest_sha256",
            "artifact_ids",
            "candidate_release_id",
            "subject",
            "generation",
            "authority_fingerprint",
            "skill_id",
            "skill_version",
            "authority_snapshot_manifest_sha256",
            "authority_snapshot_receipt_sha256",
            "formal_write_count",
        )
    }
    result = {
        "ok": True,
        "schema_version": "study-read-mcp.v3",
        "profile": "luna",
        "subject": "english",
        "server_release": session["mcp_server_release"],
        "adapter_release": session["mcp_server_release"],
        "request_id": f"{stage}-request-{ordinal}",
        "tool": tool,
        "generation": session["generation"],
        "authority_fingerprint": session["authority_fingerprint"],
        "formal_write_count": 0,
        "model_call_count": 0,
        "mcp_tool_call_count": 1,
        "read_session": read_session,
        "read_route": route,
        "items": items,
        "total_count": len(items),
        "returned_count": len(items),
        "offset": ordinal - 1,
        "next_cursor": None,
        "truncated": False,
        "complete": True,
    }
    return {
        "sequence": ordinal,
        "server": "kaoyan_english_read",
        "tool": tool,
        "arguments": arguments,
        "arguments_sha256": _sha256_json_value(arguments),
        "result": result,
        "result_sha256": _sha256_json_value(result),
    }


def _a03_stage(
    *,
    host: ProcessingPluginHost,
    context: Mapping[str, Any],
    runtime_root: Path,
    stage: str,
    prompt_version: str,
    call_count: int,
) -> tuple[dict[str, Any], Path, Path, dict[str, Any]]:
    session = context["mcp_read_session"]
    calls = [
        _a03_call(stage=stage, ordinal=ordinal, session=session)
        for ordinal in range(1, call_count + 1)
    ]
    transcript = {
        "schema_version": "model-driven-mcp-stage-transcript-v1",
        "stage_name": stage,
        "subject": "english",
        "read_session_id": session["read_session_id"],
        "read_session_manifest_sha256": session["manifest_sha256"],
        "generation": session["generation"],
        "authority_fingerprint": session["authority_fingerprint"],
        "calls": calls,
        "coverage": {
            "call_count": len(calls),
            "all_returned_pages_consumed": True,
            "unresolved_next_cursors": [],
            "duplicate_argument_count": 0,
            "host_semantic_prefetch": False,
        },
        "semantic_stage_count": 1,
        "provider_request_count": len(calls) + 1,
        "mcp_tool_call_count": len(calls),
        "model_call_count": 1,
        "formal_write_count": 0,
    }
    transcript_path, transcript_sha256 = _write_hashed_json(
        runtime_root / "private/reports/mcp-stage-transcripts/sha256", transcript
    )
    signed = host.sign_model_mcp_calls(
        subject="english",
        stage_name=prompt_version,
        context=context,
        calls=calls,
        transcript_sha256=transcript_sha256,
    )
    grounding = _grounding_manifest(calls, transcript_sha256=transcript_sha256)
    stage_receipt = {
        "status": "ready",
        "prompt_version": prompt_version,
        "processing_binding": copy.deepcopy(context["processing_binding"]),
        "processing_binding_sha256": context["processing_binding_sha256"],
        "capture_freeze_receipt_sha256": context[
            "capture_freeze_receipt_sha256"
        ],
        "capture_freeze_receipt_ref": context["capture_freeze_receipt_ref"],
        "mcp_read_session_receipt_sha256": context[
            "mcp_read_session_receipt_sha256"
        ],
        "mcp_read_session_receipt_ref": context[
            "mcp_read_session_receipt_ref"
        ],
        "read_session_id": session["read_session_id"],
        "read_session_manifest_sha256": session["manifest_sha256"],
        "evidence_generation": session["generation"],
        "evidence_authority_fingerprint": session["authority_fingerprint"],
        "host_semantic_prefetch": False,
        "pagination_coverage_complete": True,
        "consumed_terminal_duplicate_read_count": 0,
        "formal_write_count": 0,
        "mcp_transcript_sha256": transcript_sha256,
        "mcp_transcript_ref": (
            "study-intake-mcp-stage-transcript://sha256/" + transcript_sha256
        ),
        "mcp_call_receipt_sha256": signed["receipt_sha256"],
        "mcp_call_receipt_ref": signed["receipt_ref"],
        "mcp_grounding_manifest": grounding,
        "mcp_grounding_manifest_sha256": grounding["manifest_sha256"],
        "provider_request_count": len(calls) + 1,
        "mcp_tool_call_count": len(calls),
    }
    call_receipt_path = host._model_call_receipt_path(signed["receipt_sha256"])
    return stage_receipt, transcript_path, call_receipt_path, transcript


@dataclass
class SyntheticA03Fixture:
    temporary_directory: tempfile.TemporaryDirectory[str]
    runtime_root: Path
    host: ProcessingPluginHost
    context: dict[str, Any]
    stage_receipts: dict[str, dict[str, Any]]
    transcript_paths: dict[str, Path]
    call_receipt_paths: dict[str, Path]
    transcripts: dict[str, dict[str, Any]]
    scenario: dict[str, Any]

    def cleanup(self) -> None:
        self.temporary_directory.cleanup()


def build_synthetic_a03_fixture() -> SyntheticA03Fixture:
    temporary_directory = tempfile.TemporaryDirectory(prefix="study-intake-a03-")
    root = Path(temporary_directory.name).resolve()
    runtime_root = root / "runtime"
    runtime_root.mkdir(parents=True)
    candidate_release_id = _digest("synthetic-a03-candidate-release")
    host, context = _a03_context(
        runtime_root, candidate_release_id=candidate_release_id
    )

    stage_specs = {
        "analysis": (
            "english_analysis",
            "study-intake-english-luna-analysis-v1",
            3,
        ),
        "critical_review": (
            "english_critical_review",
            "study-intake-english-luna-critical-review-v2",
            2,
        ),
    }
    stage_receipts: dict[str, dict[str, Any]] = {}
    transcript_paths: dict[str, Path] = {}
    call_receipt_paths: dict[str, Path] = {}
    transcripts: dict[str, dict[str, Any]] = {}
    for stage, (stage_name, prompt_version, call_count) in stage_specs.items():
        receipt, transcript_path, call_receipt_path, transcript = _a03_stage(
            host=host,
            context=context,
            runtime_root=runtime_root,
            stage=stage_name,
            prompt_version=prompt_version,
            call_count=call_count,
        )
        stage_receipts[stage] = receipt
        transcript_paths[stage] = transcript_path
        call_receipt_paths[stage] = call_receipt_path
        transcripts[stage] = transcript
    scenario = {
        "schema_version": "synthetic-a03-replay-scenario-v1",
        "fixture_scope": "synthetic_contract_only",
        "formal_write_count": 0,
        "model_call_count": 1,
        "tool_call_count": {
            stage: len(transcripts[stage]["calls"])
            for stage in transcripts
        },
        "provider_request_count": {
            stage: transcripts[stage]["provider_request_count"]
            for stage in transcripts
        },
    }
    return SyntheticA03Fixture(
        temporary_directory=temporary_directory,
        runtime_root=runtime_root,
        host=host,
        context=context,
        stage_receipts=stage_receipts,
        transcript_paths=transcript_paths,
        call_receipt_paths=call_receipt_paths,
        transcripts=transcripts,
        scenario=scenario,
    )


def _a04_item(
    *, stage: str, index: int, tier: str, refs: tuple[str, str]
) -> dict[str, Any]:
    return {
        "item_id": f"EN-SYN-{index:03d}",
        "sequence": index,
        "item": f"Synthetic English sentence {index}",
        "candidate_type": "phrase",
        "candidate_status": "candidate",
        "tier": tier,
        "source_event_id": f"synthetic-event-{index}",
        "bank_status": "new",
        "bank_match_ids": [],
        "mastered_status": "unknown",
        "mastery_proposal": None,
        "grounding": {"mcp_evidence_refs": list(refs)},
        "card": {"front": f"Synthetic card {index}"},
    }


def _a04_calls(
    *, stage: str, count: int, generation: str
) -> list[dict[str, Any]]:
    tools = (
        "get_task_context",
        "read_task_artifact",
        "search_records",
        "search_records",
    )
    calls: list[dict[str, Any]] = []
    for ordinal in range(1, count + 1):
        tool = tools[ordinal - 1] if ordinal <= len(tools) else "list_records"
        if stage == "critical_review" and ordinal >= 3:
            arguments = {
                "page_size": 2 if ordinal == 3 else 1,
                "query": "permanent",
            }
        else:
            arguments = {
                "stage": stage,
                "ordinal": ordinal,
                "page_size": 1,
            }
        items = []
        for collection in ("task_artifact", "article_catalog"):
            source_hash = _digest(f"a04:{stage}:{ordinal}:{collection}:source")
            items.append(
                {
                    "collection": collection,
                    "stable_id": f"{stage}-{collection}-{ordinal}",
                    "source_hash": source_hash,
                    "data_role": "synthetic_fixture",
                    "evidence_ref": _mcp_ref(
                        stage=stage, ordinal=ordinal, collection=collection
                    ),
                }
            )
        result = {
            "ok": True,
            "subject": "english",
            "generation": generation,
            "items": items,
            "next_cursor": None,
            "complete": True,
        }
        calls.append(
            {
                "sequence": ordinal,
                "tool": tool,
                "arguments": arguments,
                "result": result,
            }
        )
    return calls


def _a04_raw_row(
    call: Mapping[str, Any], *, sequence: int, request_id: str
) -> dict[str, Any]:
    result = call["result"]
    return {
        "event_type": "tool_result",
        "sequence": sequence,
        "item": {
            "tool": call["tool"],
            "arguments": copy.deepcopy(call["arguments"]),
            "result": {
                "structured_content": {
                    "ok": True,
                    "request_id": request_id,
                    "items": copy.deepcopy(result["items"]),
                }
            },
        },
    }


def _a04_failure_row(*, sequence: int, request_id: str) -> dict[str, Any]:
    return {
        "event_type": "tool_result",
        "sequence": sequence,
        "item": {
            "tool": "search_records",
            "arguments": {"page_size": 48, "query": "permanent"},
            "result": {
                "structured_content": {
                    "ok": False,
                    "request_id": request_id,
                    "error": {
                        "code": "OUTPUT_LIMIT",
                        "message": "synthetic page exceeds output limit",
                    },
                    "items": [],
                }
            },
        },
    }


def _a04_output(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "payload": copy.deepcopy(payload),
        "duration_ms": 0,
        "runtime_model": None,
        "runtime_reasoning_effort": None,
        "runtime_metadata_provenance": "synthetic_fixture",
        "runtime_identity_status": "synthetic_unexecuted",
        "output_sha256": sha256_value(payload),
        "schema_sha256": _digest("synthetic-a04-provider-schema"),
    }


@dataclass
class SyntheticA04Fixture:
    temporary_directory: tempfile.TemporaryDirectory[str]
    analysis_output_path: Path
    critical_output_path: Path
    analysis_raw_path: Path
    critical_raw_path: Path
    analysis_transcript_path: Path
    critical_transcript_path: Path
    checkpoint_path: Path
    terminal_failure_path: Path
    scenario: dict[str, Any]

    def cleanup(self) -> None:
        self.temporary_directory.cleanup()


def build_synthetic_a04_fixture() -> SyntheticA04Fixture:
    temporary_directory = tempfile.TemporaryDirectory(prefix="study-intake-a04-")
    root = Path(temporary_directory.name).resolve()
    objects_root = root / "objects"
    generation = "english-synthetic-generation-a04-v1"
    analysis_calls = _a04_calls(
        stage="analysis", count=2, generation=generation
    )
    critical_calls = _a04_calls(
        stage="critical_review", count=4, generation=generation
    )

    analysis_refs = (
        analysis_calls[0]["result"]["items"][0]["evidence_ref"],
        analysis_calls[0]["result"]["items"][1]["evidence_ref"],
    )
    critical_refs = (
        critical_calls[0]["result"]["items"][0]["evidence_ref"],
        critical_calls[0]["result"]["items"][1]["evidence_ref"],
    )
    draft_items = [
        _a04_item(stage="analysis", index=1, tier="A", refs=analysis_refs),
        _a04_item(stage="analysis", index=2, tier="A", refs=analysis_refs),
    ]
    revised_items = [
        _a04_item(stage="critical_review", index=1, tier="A", refs=critical_refs),
        _a04_item(stage="critical_review", index=2, tier="B", refs=critical_refs),
    ]
    draft_analysis = {"items": draft_items}
    semantic_draft = {
        "items": copy.deepcopy(draft_items),
    }
    correction_path = "$.items[1].tier"
    findings = [
        {
            "correction_id": "CR-000",
            "code": "synthetic_blocking_correction",
            "severity": "blocking",
            "item_id": "EN-SYN-001",
            "message": "Synthetic required correction is intentionally unresolved.",
            "affected_json_paths": ["$.items[0].tier"],
        },
        {
            "correction_id": "CR-001",
            "code": "synthetic_tier_revision",
            "severity": "warning",
            "item_id": "EN-SYN-002",
            "message": "Synthetic warning correction is recovered.",
            "affected_json_paths": [correction_path],
        },
    ]
    corrections = [
        {
            "finding_id": "CR-000",
            "resolution": "unresolved",
            "affected_json_paths": ["$.items[0].tier"],
        },
        {
            "finding_id": "CR-001",
            "resolution": "applied",
            "affected_json_paths": [correction_path],
            "before": {
                "encoding": "canonical_json",
                "canonical_json": '"A"',
            },
            "after": {
                "encoding": "canonical_json",
                "canonical_json": '"B"',
            },
        },
    ]
    critical_payload = {
        "schema_version": "study-intake-english-critical-review-v1",
        "verdict": "reject",
        "draft_analysis_sha256": sha256_value(semantic_draft),
        "findings": findings,
        "correction_resolutions": corrections,
        "revised_items": revised_items,
    }
    analysis_payload = {"items": draft_items}
    analysis_output = _a04_output(analysis_payload)
    critical_output = _a04_output(critical_payload)
    analysis_output_path, analysis_output_sha256 = _write_hashed_json(
        objects_root / "model-stage-outputs", analysis_output
    )
    critical_output_path, critical_output_sha256 = _write_hashed_json(
        objects_root / "model-stage-outputs", critical_output
    )

    analysis_transcript = {
        "schema_version": "synthetic-model-mcp-stage-transcript-v1",
        "stage_name": "english_analysis",
        "subject": "english",
        "generation": generation,
        "calls": analysis_calls,
        "coverage": {
            "call_count": len(analysis_calls),
            "all_returned_pages_consumed": True,
            "unresolved_next_cursors": [],
            "duplicate_argument_count": 0,
            "host_semantic_prefetch": False,
        },
        "semantic_stage_count": 1,
        "provider_request_count": len(analysis_calls) + 1,
        "mcp_tool_call_count": len(analysis_calls),
        "model_call_count": 1,
        "formal_write_count": 0,
    }
    critical_transcript = {
        "schema_version": "synthetic-model-mcp-stage-transcript-v1",
        "stage_name": "english_critical_review",
        "subject": "english",
        "generation": generation,
        "calls": critical_calls,
        "coverage": {
            "call_count": len(critical_calls),
            "all_returned_pages_consumed": True,
            "unresolved_next_cursors": [],
            "duplicate_argument_count": 0,
            "host_semantic_prefetch": False,
        },
        "semantic_stage_count": 1,
        "provider_request_count": len(critical_calls) + 1,
        "mcp_tool_call_count": len(critical_calls),
        "model_call_count": 1,
        "formal_write_count": 0,
    }
    analysis_transcript_path, analysis_transcript_sha256 = _write_hashed_json(
        objects_root / "mcp-stage-transcripts", analysis_transcript
    )
    critical_transcript_path, critical_transcript_sha256 = _write_hashed_json(
        objects_root / "mcp-stage-transcripts", critical_transcript
    )

    analysis_raw_rows = [
        _a04_raw_row(call, sequence=index, request_id=f"analysis-{index}")
        for index, call in enumerate(analysis_calls, start=1)
    ]
    failed_request_id = "synthetic-failed-request-a04"
    critical_raw_rows = [
        _a04_raw_row(call, sequence=index, request_id=f"critical-{index}")
        for index, call in enumerate(critical_calls[:3], start=1)
    ]
    critical_raw_rows.append(
        _a04_failure_row(sequence=4, request_id=failed_request_id)
    )
    critical_raw_rows.append(
        _a04_raw_row(critical_calls[3], sequence=5, request_id="critical-5")
    )
    analysis_raw = {
        "schema_version": "synthetic-model-mcp-transport-v1",
        "mcp_item_count": len(analysis_raw_rows),
        "mcp_items": analysis_raw_rows,
    }
    critical_raw = {
        "schema_version": "synthetic-model-mcp-transport-v1",
        "mcp_item_count": len(critical_raw_rows),
        "mcp_items": critical_raw_rows,
    }
    analysis_raw_path, analysis_raw_sha256 = _write_hashed_json(
        objects_root / "model-mcp-transport", analysis_raw
    )
    critical_raw_path, critical_raw_sha256 = _write_hashed_json(
        objects_root / "model-mcp-transport", critical_raw
    )

    checkpoint = {
        "schema_version": "synthetic-a04-analysis-checkpoint-v1",
        "draft_analysis": draft_analysis,
        "formal_write_count": 0,
    }
    checkpoint_path, checkpoint_sha256 = _write_hashed_json(
        objects_root / "analysis-checkpoints", checkpoint
    )
    terminal_failure = {
        "schema_version": "synthetic-terminal-failure-v1",
        "failure_signature": "english_required_correction_unresolved",
        "failure_stage": "synthetic_two_stage_dispatch",
        "first_failure_path": None,
        "formal_write_count": 0,
        "sol_status": "disabled",
    }
    terminal_failure_path = root / "receipts/terminal-failure.json"
    terminal_failure_sha256 = _write_json(terminal_failure_path, terminal_failure)

    scenario = {
        "schema_version": "synthetic-a04-replay-scenario-v1",
        "fixture_scope": "synthetic_contract_only",
        "analysis_tool_call_count": len(analysis_calls),
        "critical_tool_call_count": len(critical_calls),
        "analysis_raw_item_count": len(analysis_raw_rows),
        "critical_raw_item_count": len(critical_raw_rows),
        "failed_attempt_count": 1,
        "failed_attempt_sequence": 4,
        "failed_request_id": failed_request_id,
        "recovery_sequence": critical_calls[3]["sequence"],
        "permanent_query": "permanent",
        "permanent_recovery_call_count": sum(
            call["arguments"].get("query") == "permanent"
            for call in critical_calls
        ),
        "semantic_stage_count": 1,
        "provider_request_count": {
            "analysis": len(analysis_calls) + 1,
            "critical_review": len(critical_calls) + 1,
        },
        "formal_write_count": 0,
        "executed_model_call_count": 0,
        "executed_formal_write_count": 0,
        "finding_count": len(findings),
        "corrected_finding_count": len(findings) - 1,
        "resolution_count": len(corrections),
        "corrected_resolution_count": len(corrections) - 1,
        "blocking_finding_id": "CR-000",
        "blocking_finding_index": 0,
        "unresolved_resolution_index": 0,
        "analysis_output_sha256": analysis_output_sha256,
        "critical_output_sha256": critical_output_sha256,
        "analysis_raw_sha256": analysis_raw_sha256,
        "critical_raw_sha256": critical_raw_sha256,
        "analysis_transcript_sha256": analysis_transcript_sha256,
        "critical_transcript_sha256": critical_transcript_sha256,
        "checkpoint_sha256": checkpoint_sha256,
        "terminal_failure_sha256": terminal_failure_sha256,
    }
    return SyntheticA04Fixture(
        temporary_directory=temporary_directory,
        analysis_output_path=analysis_output_path,
        critical_output_path=critical_output_path,
        analysis_raw_path=analysis_raw_path,
        critical_raw_path=critical_raw_path,
        analysis_transcript_path=analysis_transcript_path,
        critical_transcript_path=critical_transcript_path,
        checkpoint_path=checkpoint_path,
        terminal_failure_path=terminal_failure_path,
        scenario=scenario,
    )
