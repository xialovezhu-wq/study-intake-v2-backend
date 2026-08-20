"""Temporary, content-addressed fixtures for the E8A and sealed MCP replays.

The builders in this module intentionally contain no user, deployment, release,
transcript, or Capture data.  They exercise the production replay contracts
with deterministic scenario-derived values and verify every file hash before a
fixture is returned to a test.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from preprocessor_core import (  # type: ignore[import-not-found]
    StructuredStageResult,
    json_file_bytes,
    mcp_grounding_manifest,
    model_mcp_item_ref,
    sha256_text,
    sha256_value,
)


def write_content_addressed_json(
    base: Path, value: Mapping[str, Any]
) -> tuple[Path, str]:
    """Write one canonical JSON object and self-check its content address."""

    payload = json_file_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    path = base / digest[:2] / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest or path.stem != digest:
        raise AssertionError(f"synthetic content-addressed hash drift: {path}")
    return path, digest


@dataclass(frozen=True)
class SyntheticE8aFixture:
    """All in-memory values and temporary file hashes for the E8A scenario."""

    root: Path
    output: dict[str, Any]
    provider_schema: dict[str, Any]
    transcript: dict[str, Any]
    raw_transport: dict[str, Any]
    output_path: Path
    provider_schema_path: Path
    transcript_path: Path
    raw_transport_path: Path
    output_file_sha256: str
    provider_schema_sha256: str
    transcript_sha256: str
    raw_transport_sha256: str
    stage: StructuredStageResult
    manifest: dict[str, Any]
    artifact_ref: str
    library_ref: str
    context_ref: str
    collection_counts: dict[str, int]
    failed_sequence: int
    failed_request_id: str
    failed_arguments: dict[str, Any]
    failed_result: dict[str, Any]
    generation: str


def _synthetic_item(
    *,
    generation: str,
    collection: str,
    stable_id: str,
    scenario: str,
) -> dict[str, str]:
    source_hash = sha256_value(
        {
            "scenario": scenario,
            "collection": collection,
            "stable_id": stable_id,
        }
    )
    return {
        "collection": collection,
        "stable_id": stable_id,
        "source_hash": source_hash,
        "data_role": "synthetic_fixture",
        "evidence_ref": model_mcp_item_ref(
            subject="english",
            generation=generation,
            collection=collection,
            stable_id=stable_id,
            source_hash=source_hash,
        ),
    }


def _synthetic_call(
    *,
    sequence: int,
    tool: str,
    arguments: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    generation: str,
) -> dict[str, Any]:
    result = {
        "ok": True,
        "schema_version": "study-read-mcp.v3",
        "subject": "english",
        "generation": generation,
        "items": [copy.deepcopy(dict(item)) for item in items],
    }
    arguments_copy = copy.deepcopy(dict(arguments))
    return {
        "sequence": sequence,
        "server": "kaoyan_english_read",
        "tool": tool,
        "arguments": arguments_copy,
        "arguments_sha256": sha256_value(arguments_copy),
        "result": result,
        "result_sha256": sha256_value(result),
    }


def build_synthetic_e8a_fixture(root: Path) -> SyntheticE8aFixture:
    """Build one deterministic English grounding replay in ``root``.

    The scenario deliberately has several independently generated calls so the
    manifest still exercises task-context, task-artifact, article-catalog, and
    search grounding roles.  The failed request is stored only in raw
    transport; it is never included in the canonical transcript or manifest.
    """

    root = root.resolve()
    scenario = "synthetic-e8a-english-grounding-v1"
    generation = "english-synthetic-grounding-v1"
    capture_id = "EN-SYNTHETIC-GROUNDING-001"
    artifact_id = "synthetic-capture-facts"

    calls: list[dict[str, Any]] = []
    collection_counts: Counter[str] = Counter()

    context_item = _synthetic_item(
        generation=generation,
        collection="task_context",
        stable_id=capture_id,
        scenario=scenario,
    )
    calls.append(
        _synthetic_call(
            sequence=1,
            tool="get_task_context",
            arguments={},
            items=[context_item],
            generation=generation,
        )
    )
    collection_counts["task_context"] += 1

    artifact_item = _synthetic_item(
        generation=generation,
        collection="task_artifact",
        stable_id=artifact_id,
        scenario=scenario,
    )
    calls.append(
        _synthetic_call(
            sequence=2,
            tool="read_task_artifact",
            arguments={"artifact_id": artifact_id, "max_bytes": 16384},
            items=[artifact_item],
            generation=generation,
        )
    )
    collection_counts["task_artifact"] += 1

    sequence = 3
    for ordinal in range(1, 61):
        stable_id = f"synthetic-article-{ordinal:03d}"
        item = _synthetic_item(
            generation=generation,
            collection="article_catalog",
            stable_id=stable_id,
            scenario=scenario,
        )
        calls.append(
            _synthetic_call(
                sequence=sequence,
                tool="get_records",
                arguments={
                    "collection": "article_catalog",
                    "record_id": stable_id,
                    "page_size": 1,
                },
                items=[item],
                generation=generation,
            )
        )
        collection_counts["article_catalog"] += 1
        sequence += 1

    for ordinal, item_count in enumerate((5, 4), start=1):
        items = [
            _synthetic_item(
                generation=generation,
                collection="search",
                stable_id=f"synthetic-search-{ordinal}-{index:02d}",
                scenario=scenario,
            )
            for index in range(1, item_count + 1)
        ]
        calls.append(
            _synthetic_call(
                sequence=sequence,
                tool="search_records",
                arguments={
                    "query": f"synthetic-search-query-{ordinal}",
                    "page_size": item_count,
                },
                items=items,
                generation=generation,
            )
        )
        collection_counts["search"] += item_count
        sequence += 1

    transcript = {
        "schema_version": "model-driven-mcp-stage-transcript-v1",
        "stage_name": "english_analysis",
        "subject": "english",
        "generation": generation,
        "calls": copy.deepcopy(calls),
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
    transcript_path, transcript_sha256 = write_content_addressed_json(
        root / "mcp-stage-transcripts", transcript
    )

    provider_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object"},
            }
        },
    }
    provider_schema_path, provider_schema_sha256 = write_content_addressed_json(
        root / "provider-schemas", provider_schema
    )
    output_payload = {"items": []}
    output = {
        "schema_version": "synthetic-english-analysis-output-v1",
        "payload": output_payload,
        "duration_ms": 1,
        "runtime_model": None,
        "runtime_reasoning_effort": None,
        "runtime_metadata_provenance": "unavailable",
        "runtime_identity_status": "requested_unverified",
        "output_sha256": sha256_value(output_payload),
        "schema_sha256": provider_schema_sha256,
    }
    output_path, output_file_sha256 = write_content_addressed_json(
        root / "model-stage-outputs", output
    )

    stage = StructuredStageResult(
        payload=copy.deepcopy(output_payload),
        duration_ms=int(output["duration_ms"]),
        runtime_model=output["runtime_model"],
        runtime_reasoning_effort=output["runtime_reasoning_effort"],
        runtime_metadata_provenance=str(output["runtime_metadata_provenance"]),
        runtime_identity_status=str(output["runtime_identity_status"]),
        output_sha256=str(output["output_sha256"]),
        schema_sha256=provider_schema_sha256,
        provider_schema_sha256=provider_schema_sha256,
        semantic_stage_count=int(transcript["semantic_stage_count"]),
        provider_request_count=int(transcript["provider_request_count"]),
        mcp_tool_call_count=int(transcript["mcp_tool_call_count"]),
        mcp_transcript_sha256=transcript_sha256,
        mcp_transcript_ref=(
            "study-intake-mcp-stage-transcript://sha256/" + transcript_sha256
        ),
        mcp_calls=tuple(copy.deepcopy(calls)),
    )
    manifest = mcp_grounding_manifest((stage,))
    manifest_refs = {
        str(row["evidence_ref"]): row for row in manifest["items"]
    }
    artifact_ref = next(
        ref
        for ref, row in manifest_refs.items()
        if row["collection"] == "task_artifact"
    )
    library_ref = next(
        ref
        for ref, row in manifest_refs.items()
        if row["collection"] not in {"task_context", "task_artifact"}
    )
    context_ref = next(
        ref
        for ref, row in manifest_refs.items()
        if row["collection"] == "task_context"
    )

    failed_request_id = sha256_text(scenario + ":failed-request")[:16]
    failed_arguments = {
        "query": "synthetic-failed-search",
        "page_size": 48,
    }
    failed_result = {
        "ok": False,
        "schema_version": "study-read-mcp.v3",
        "server_release": "synthetic-english-mcp-v1",
        "request_id": failed_request_id,
        "tool": "search_records",
        "error": {
            "code": "OUTPUT_LIMIT",
            "message": "synthetic output limit",
            "retryable": False,
        },
        "items": [],
        "formal_write_count": 0,
        "model_call_count": 0,
        "mcp_tool_call_count": 0,
    }
    failed_item = {
        "type": "mcp_tool_call",
        "server": "kaoyan_english_read",
        "tool": "search_records",
        "arguments": failed_arguments,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        failed_result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
            "structured_content": copy.deepcopy(failed_result),
        },
    }
    transport_items = [
        {
            "sequence": int(call["sequence"]),
            "event_type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": call["server"],
                "tool": call["tool"],
                "arguments": copy.deepcopy(call["arguments"]),
                "result": copy.deepcopy(call["result"]),
            },
        }
        for call in calls
    ]
    transport_items.append(
        {
            "sequence": len(transport_items) + 1,
            "event_type": "item.completed",
            "item": failed_item,
        }
    )
    raw_transport = {
        "schema_version": "model-driven-mcp-transport-v1",
        "stage_name": "english_analysis",
        "subject": "english",
        "generation": generation,
        "mcp_items": transport_items,
        "mcp_item_count": len(transport_items),
        "canonical_validation_status": "pending",
        "formal_write_count": 0,
    }
    raw_transport_path, raw_transport_sha256 = write_content_addressed_json(
        root / "model-mcp-transport", raw_transport
    )

    return SyntheticE8aFixture(
        root=root,
        output=output,
        provider_schema=provider_schema,
        transcript=transcript,
        raw_transport=raw_transport,
        output_path=output_path,
        provider_schema_path=provider_schema_path,
        transcript_path=transcript_path,
        raw_transport_path=raw_transport_path,
        output_file_sha256=output_file_sha256,
        provider_schema_sha256=provider_schema_sha256,
        transcript_sha256=transcript_sha256,
        raw_transport_sha256=raw_transport_sha256,
        stage=stage,
        manifest=manifest,
        artifact_ref=artifact_ref,
        library_ref=library_ref,
        context_ref=context_ref,
        collection_counts=dict(collection_counts),
        failed_sequence=len(transport_items),
        failed_request_id=failed_request_id,
        failed_arguments=copy.deepcopy(failed_arguments),
        failed_result=copy.deepcopy(failed_result),
        generation=generation,
    )


@dataclass(frozen=True)
class SyntheticSealedTransportFixture:
    """Content-addressed raw MCP transport plus its synthetic read session."""

    root: Path
    session: dict[str, Any]
    transport: dict[str, Any]
    session_path: Path
    transport_path: Path
    session_file_sha256: str
    transport_file_sha256: str
    events: tuple[str, ...]


def build_synthetic_sealed_transport(
    root: Path,
    *,
    session: Mapping[str, Any],
    events: Sequence[str],
) -> SyntheticSealedTransportFixture:
    """Persist synthetic MCP events and verify their content-addressed files."""

    root = root.resolve()
    session_value = copy.deepcopy(dict(session))
    session_path, session_file_sha256 = write_content_addressed_json(
        root / "mcp-read-sessions", session_value
    )
    transport_items: list[dict[str, Any]] = []
    for sequence, raw_event in enumerate(events, start=1):
        event = json.loads(raw_event)
        item = event.get("item") if isinstance(event, Mapping) else None
        if not isinstance(item, Mapping):
            raise AssertionError("synthetic sealed MCP event item missing")
        item_copy = copy.deepcopy(dict(item))
        result = item_copy.get("result")
        if isinstance(result, Mapping) and "content" not in result:
            result_value = copy.deepcopy(dict(result))
            item_copy["result"] = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            result_value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                ],
                "structured_content": result_value,
            }
        transport_items.append(
            {
                "sequence": sequence,
                "event_type": str(event.get("type") or "item.completed"),
                "item": item_copy,
            }
        )
    stdout = ("\n".join(events) + "\n").encode("utf-8")
    transport = {
        "schema_version": "model-driven-mcp-transport-v1",
        "stage_name": "english_analysis",
        "subject": "english",
        "read_session_manifest_sha256": session_value.get("manifest_sha256"),
        "generation": session_value.get("generation"),
        "authority_fingerprint": session_value.get("authority_fingerprint"),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "mcp_items": transport_items,
        "mcp_item_count": len(transport_items),
        "canonical_validation_status": "pending",
        "formal_write_count": 0,
    }
    transport_path, transport_file_sha256 = write_content_addressed_json(
        root / "model-mcp-transport", transport
    )
    return SyntheticSealedTransportFixture(
        root=root,
        session=session_value,
        transport=transport,
        session_path=session_path,
        transport_path=transport_path,
        session_file_sha256=session_file_sha256,
        transport_file_sha256=transport_file_sha256,
        events=tuple(events),
    )
