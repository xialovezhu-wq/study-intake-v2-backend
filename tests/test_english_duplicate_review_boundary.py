#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import preprocessor_core as core  # noqa: E402


def _session() -> dict:
    return {
        "schema_version": "study-read-mcp-read-session.v4",
        "subject": "english",
        "read_session_id": "rs-synthetic-english-0001",
        "manifest_sha256": "1" * 64,
        "candidate_release_id": "4" * 64,
        "plugin_version": "0.0.0-synthetic",
        "skill_id": "background-english-processing",
        "skill_version": "0.0.0-synthetic",
        "mcp_server_release": "0.0.0+sha256." + "5" * 64,
        "generation": "synthetic-generation-1",
        "authority_fingerprint": "2" * 64,
        "capture_id": "EN-SYNTHETIC-DUPLICATE-001",
        "capture_manifest_sha256": "3" * 64,
        "artifact_ids": ["capture-facts"],
        "authority_snapshot_manifest_sha256": "6" * 64,
        "authority_snapshot_receipt_sha256": "7" * 64,
    }


def _mcp_event(*, tool: str, arguments: dict, sequence: int) -> dict:
    session = _session()
    if tool == "get_task_context":
        collection = "task_context"
        stable_id = session["capture_id"]
    elif tool == "read_task_artifact":
        collection = "task_artifact"
        stable_id = arguments["artifact_id"]
    elif tool == "search_records":
        collection = "search"
        stable_id = f"SYNTHETIC-{sequence:03d}"
    else:
        collection = arguments["collection"]
        stable_id = f"SYNTHETIC-{sequence:03d}"
    source_hash = hashlib.sha256(
        f"{tool}:{collection}:{stable_id}".encode("utf-8")
    ).hexdigest()
    item = {
        "stable_id": stable_id,
        "source_hash": source_hash,
        "data_role": "formal_fact",
        "collection": collection,
        "evidence_ref": core.model_mcp_item_ref(
            subject="english",
            generation=session["generation"],
            collection=collection,
            stable_id=stable_id,
            source_hash=source_hash,
        ),
    }
    query_arguments = {
        key: value for key, value in arguments.items() if key != "cursor"
    }
    envelope = {
        "ok": True,
        "schema_version": "study-read-mcp.v3",
        "server_release": session["mcp_server_release"],
        "adapter_release": session["mcp_server_release"],
        "preprocessor_release": session["candidate_release_id"],
        "subject": "english",
        "profile": "luna",
        "consistency": "bound_snapshot",
        "generation": session["generation"],
        "authority_fingerprint": session["authority_fingerprint"],
        "captured_at": "2026-08-20T00:00:00+00:00",
        "read_session": {**session, "formal_write_count": 0},
        "read_route": {
            "caller_skill_id": session["skill_id"],
            "caller_skill_version": session["skill_version"],
            "plugin_version": session["plugin_version"],
            "route_request_id": session["read_session_id"],
            "evidence_scope_hash": session["manifest_sha256"],
            "read_route": "mcp_model_driven",
            "read_session_id": session["read_session_id"],
            "consumed_duplicate_read_count": 0,
        },
        "formal_write_count": 0,
        "model_call_count": 0,
        "mcp_tool_call_count": 1,
        "total_count": 1,
        "returned_count": 1,
        "offset": 0,
        "page_size": 1,
        "query_sha256": hashlib.sha256(
            json.dumps(
                {"tool": tool, "arguments": query_arguments},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "next_cursor": None,
        "truncated": False,
        "complete": True,
        "items": [item],
    }
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": "kaoyan_english_read",
            "tool": tool,
            "arguments": arguments,
            "result": {
                "structured_content": envelope,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            envelope,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                ],
            },
        },
    }


def _immutable_fixture() -> tuple[bytes, dict, bytes]:
    events = [
        _mcp_event(tool="get_task_context", arguments={}, sequence=1),
        _mcp_event(
            tool="read_task_artifact",
            arguments={
                "artifact_id": "capture-facts",
                "cursor": None,
                "max_bytes": 16384,
            },
            sequence=2,
        ),
    ]
    previous_arguments: dict | None = None
    for sequence in range(3, 41):
        tool = "search_records" if sequence == 40 else "get_records"
        arguments = (
            {
                "query": "synthetic final lookup",
                "collections": ["synthetic_collection_40"],
                "cursor": None,
                "page_size": 1,
            }
            if tool == "search_records"
            else {
                "collection": f"synthetic_collection_{sequence:02d}",
                "ids": [f"SYNTHETIC-{sequence:03d}"],
                "cursor": None,
                "page_size": 1,
            }
        )
        if sequence == 20:
            assert previous_arguments is not None
            arguments = copy.deepcopy(previous_arguments)
        event = _mcp_event(
            tool=tool, arguments=arguments, sequence=sequence
        )
        if sequence == 20:
            event["item"]["result"] = copy.deepcopy(
                events[-1]["item"]["result"]
            )
        events.append(event)
        previous_arguments = arguments
    stdout = (
        "\n".join(
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for event in events
        )
        + "\n"
    ).encode("utf-8")
    return stdout, _session(), b'{"synthetic":true}'


class EnglishDuplicateReviewBoundaryTests(unittest.TestCase):
    maxDiff = None

    def _parse(self, stdout: bytes, session: dict):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime = Path(temporary.name)
        runner = core.CodexRunner({}, runtime)
        try:
            runner._mcp_stage_calls(
                stdout=stdout,
                stage_name="english_analysis",
                subject="english",
                processing_context={"mcp_read_session": session},
            )
        except core._McpReviewPolicyViolation as exc:
            transcript_path = (
                runtime
                / "private/reports/mcp-stage-transcripts/sha256"
                / exc.transcript_sha256[:2]
                / f"{exc.transcript_sha256}.json"
            )
            return exc, json.loads(transcript_path.read_text(encoding="utf-8"))
        self.fail("expected one review-only duplicate policy violation")

    def _execute_actual_shape(self, raw_output: bytes):
        stdout, session, _fixture_raw = _immutable_fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        codex = root / "codex"
        codex.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        codex.chmod(0o700)
        schema = root / "schema.json"
        schema.write_text('{"type":"object"}\n', encoding="utf-8")
        runner = core.CodexRunner(
            {
                "codex_path": str(codex),
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
            root / "runtime",
        )
        execution_statuses: list[str] = []
        failure_digest = "f" * 64

        def invoke(command, **_kwargs):
            output_path = Path(
                command[command.index("--output-last-message") + 1]
            )
            output_path.write_bytes(raw_output)
            runner._provider_raw_refs["english_analysis"] = {
                "raw_output_object_sha256": "1" * 64,
                "raw_output_object_ref": (
                    "study-intake-model-stage-raw-output://sha256/" + "1" * 64
                ),
            }
            return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

        def publish_execution(**kwargs):
            execution_statuses.append(kwargs["execution_status"])
            return {
                "stage_execution_receipt_sha256": "2" * 64,
                "stage_execution_receipt_ref": (
                    "study-intake-model-stage-execution://sha256/" + "2" * 64
                ),
            }

        def publish_normalization(**kwargs):
            warnings = tuple(kwargs["warnings"])
            return {
                "stage_normalization_receipt_sha256": "3" * 64,
                "stage_normalization_receipt_ref": (
                    "study-intake-model-stage-normalization://sha256/" + "3" * 64
                ),
                "normalization_status": "normalized_with_warnings",
                "normalization_warning_count": len(warnings),
                "normalization_warnings": warnings,
            }

        with (
            mock.patch.object(runner, "_invoke_subprocess", side_effect=invoke),
            mock.patch.object(runner, "_model_mcp_config_args", return_value=[]),
            mock.patch.object(
                runner,
                "_publish_model_stage_execution",
                side_effect=publish_execution,
            ),
            mock.patch.object(
                runner,
                "_publish_model_stage_normalization",
                side_effect=publish_normalization,
            ),
            mock.patch.object(
                runner,
                "_sign_mcp_stage_failure",
                return_value={
                    "mcp_failure_receipt_sha256": failure_digest,
                    "mcp_failure_receipt_ref": (
                        "study-intake-model-mcp-failure://sha256/"
                        + failure_digest
                    ),
                },
            ) as sign_failure,
        ):
            try:
                runner._execute_prompt(
                    prompt="bounded English review fixture",
                    output_schema=schema,
                    image_paths=(),
                    stage_name="english_analysis",
                    max_prompt_bytes=4096,
                    max_output_bytes=65536,
                    allowed_evidence_refs=(),
                    bind_evidence_schema=False,
                    subject="english",
                    processing_context={"mcp_read_session": session},
                )
            except core.PreprocessorError as exc:
                return exc, execution_statuses, sign_failure
        self.fail("actual duplicate shape must not be automatically adoptable")

    def test_actual_40_call_shape_is_reviewable_and_preserves_later_reads(self) -> None:
        stdout, session, raw_output = _immutable_fixture()
        self.assertIsInstance(json.loads(raw_output), dict)
        self.assertGreater(len(raw_output), 0)
        error, transcript = self._parse(stdout, session)
        self.assertEqual(error.code, "english_analysis_mcp_duplicate_read")
        self.assertRegex(
            error.duplicate_result_projection_sha256, r"^[0-9a-f]{64}$"
        )
        self.assertEqual(len(error.calls), 40)
        self.assertEqual(transcript["coverage"]["call_count"], 40)
        self.assertEqual(transcript["coverage"]["duplicate_argument_count"], 1)
        self.assertEqual(transcript["formal_write_count"], 0)
        self.assertEqual(transcript["calls"][18]["arguments"], transcript["calls"][19]["arguments"])
        self.assertEqual(transcript["calls"][19]["sequence"], 20)
        self.assertEqual(transcript["calls"][-1]["sequence"], 40)
        self.assertEqual(transcript["calls"][-1]["tool"], "search_records")

    def test_actual_valid_raw_preserves_technical_error_without_mcp_failure(self) -> None:
        _stdout, _session, raw_output = _immutable_fixture()
        error, statuses, sign_failure = self._execute_actual_shape(raw_output)
        self.assertEqual(error.code, "english_analysis_mcp_duplicate_read")
        self.assertEqual(statuses, ["completed"])
        sign_failure.assert_not_called()
        self.assertNotIn("mcp_failure_receipt_sha256", error.diagnostic)
        self.assertTrue(error.diagnostic["post_stage_validation_failed"])
        self.assertNotIn("report_disposition", error.diagnostic)
        self.assertNotIn("review_candidate_stage", error.diagnostic)
        self.assertRegex(
            error.diagnostic["mcp_transcript_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_actual_transcript_with_invalid_raw_remains_execution_failure(self) -> None:
        error, statuses, sign_failure = self._execute_actual_shape(b"not-json")
        self.assertEqual(error.code, "english_analysis_output_invalid_json")
        self.assertEqual(statuses, ["completed"])
        sign_failure.assert_called_once()
        self.assertIn("mcp_failure_receipt_sha256", error.diagnostic)
        self.assertNotIn("review_candidate_stage", error.diagnostic)

    def test_changed_duplicate_result_remains_execution_failure(self) -> None:
        stdout, session, _raw_output = _immutable_fixture()
        events = [json.loads(line) for line in stdout.splitlines()]
        changed = events[19]["item"]["result"]
        for representation in (
            changed["structured_content"],
            json.loads(changed["content"][0]["text"]),
        ):
            representation["warnings"] = ["changed duplicate result"]
            if representation is not changed["structured_content"]:
                changed["content"][0]["text"] = json.dumps(
                    representation,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
        mutated = ("\n".join(json.dumps(row) for row in events) + "\n").encode()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(core.PreprocessorError) as raised:
                core.CodexRunner({}, Path(temporary))._mcp_stage_calls(
                    stdout=mutated,
                    stage_name="english_analysis",
                    subject="english",
                    processing_context={"mcp_read_session": session},
                )
        self.assertEqual(raised.exception.code, "english_analysis_mcp_duplicate_read")
        self.assertNotIsInstance(raised.exception, core._McpReviewPolicyViolation)

    def test_third_identical_read_remains_execution_failure(self) -> None:
        stdout, session, _raw_output = _immutable_fixture()
        events = [json.loads(line) for line in stdout.splitlines()]
        third = copy.deepcopy(events[19])
        events.insert(20, third)
        mutated = ("\n".join(json.dumps(row) for row in events) + "\n").encode()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(core.PreprocessorError) as raised:
                core.CodexRunner({}, Path(temporary))._mcp_stage_calls(
                    stdout=mutated,
                    stage_name="english_analysis",
                    subject="english",
                    processing_context={"mcp_read_session": session},
                )
        self.assertEqual(raised.exception.code, "english_analysis_mcp_duplicate_read")
        self.assertNotIsInstance(raised.exception, core._McpReviewPolicyViolation)


if __name__ == "__main__":
    unittest.main()
