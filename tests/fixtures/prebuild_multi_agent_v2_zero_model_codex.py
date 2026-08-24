#!/usr/bin/env python3
"""Deterministic zero-API Codex fixture for the pre-Build V2 route.

This executable deliberately sits at the real ``codex exec`` process boundary.
Terra drafts are generated locally, while every Luna run starts the exact MCP
server described by the Host's sealed command-line configuration and performs
the required reads through MCP.  It never reads subject repositories directly.

The fixture is only a plumbing smoke tool.  It is not a substitute for the
subsequent real-model nine-Capture gate.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


VERSION = "prebuild-multi-agent-v2-zero-model-codex 1.0"
MCP_HELPER = r'''
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def dump_result(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=False)


def envelope(value):
    direct = value.get("structuredContent")
    if isinstance(direct, dict):
        return direct
    direct = value.get("structured_content")
    if isinstance(direct, dict):
        return direct
    for block in value.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("zero_model_mcp_envelope_missing")


async def run(spec):
    params = StdioServerParameters(
        command=spec["command"],
        args=spec["args"],
        cwd=spec["cwd"],
        env=spec["env"],
    )
    calls = []
    refs = []

    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            observed_name = initialized.serverInfo.name
            if observed_name != spec["server"]:
                raise RuntimeError("zero_model_mcp_server_identity_mismatch")

            async def call(tool, arguments):
                result = dump_result(await session.call_tool(tool, arguments))
                body = envelope(result)
                if body.get("ok") is not True:
                    error = body.get("error") or {}
                    raise RuntimeError(
                        "zero_model_mcp_call_failed:"
                        + str(error.get("code") or "unknown")
                    )
                calls.append({
                    "tool": tool,
                    "arguments": arguments,
                    "result": result,
                })
                for item in body.get("items") or []:
                    if isinstance(item, dict):
                        ref = item.get("evidence_ref")
                        if isinstance(ref, str) and ref and ref not in refs:
                            refs.append(ref)
                return body

            await call("get_task_context", {})
            for artifact_id in spec["artifact_ids"]:
                cursor = None
                while True:
                    arguments = {
                        "artifact_id": artifact_id,
                        "max_bytes": 16384,
                    }
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    body = await call("read_task_artifact", arguments)
                    if body.get("complete") is True:
                        break
                    cursor = body.get("next_cursor")
                    if not isinstance(cursor, str) or not cursor:
                        raise RuntimeError("zero_model_mcp_artifact_cursor_missing")

            cursor = None
            while True:
                arguments = {"query": spec["query"], "page_size": 1}
                if cursor is not None:
                    arguments["cursor"] = cursor
                body = await call("search_records", arguments)
                if body.get("complete") is True:
                    break
                cursor = body.get("next_cursor")
                if not isinstance(cursor, str) or not cursor:
                    raise RuntimeError("zero_model_mcp_search_cursor_missing")

    return {"calls": calls, "evidence_refs": refs}


request = json.loads(sys.stdin.read())
print(json.dumps(asyncio.run(run(request)), ensure_ascii=False, separators=(",", ":")))
'''


def _json_after_first_line(prompt: str) -> dict[str, Any]:
    try:
        value = json.loads(prompt.split("\n", 1)[1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("zero_model_prompt_json_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("zero_model_prompt_json_not_object")
    return value


def _marker(text: str) -> str | None:
    for pattern in (
        r"prebuild:v2:math:[1-3]",
        r"prebuild-v2-cs408-[1-3]",
        r"PREBUILD-EN-0[1-3]",
    ):
        found = re.search(pattern, text)
        if found is not None:
            return found.group(0)
    return None


def _terra_initial(prompt: str) -> dict[str, Any]:
    supplied = _json_after_first_line(prompt)
    subject = str(supplied.get("subject") or "")
    capture_id = str(supplied.get("capture_id") or "")
    collections = supplied.get("allowed_collections")
    if (
        subject not in {"math", "cs408", "english"}
        or not capture_id
        or not isinstance(collections, list)
        or "search" not in collections
    ):
        raise RuntimeError("zero_model_terra_initial_binding_invalid")
    marker = _marker(prompt)
    marker_text = marker or capture_id
    purposes = (
        "Check the captured task identity against subject evidence",
        "Check the captured condition or mechanism against subject evidence",
        "Check for bounded related evidence without changing the capture",
    )
    return {
        "schema_version": "terra_initial_draft_v1",
        "subject": subject,
        "capture_id": capture_id,
        "learning_sections": {
            "task_summary": "Synthetic pre-Build capture " + marker_text,
            "known_facts": ["The frozen Capture identity is " + capture_id],
            "open_questions": ["Whether subject evidence contains a bounded match"],
        },
        "proposed_branches": [
            {
                "branch_id": f"branch-{ordinal}",
                "purpose": purpose,
                "rationale": "Independent read-only plumbing check",
                "collection_scope": ["search"],
                "expected_evidence_kinds": ["task", "subject_library"],
            }
            for ordinal, purpose in enumerate(purposes, 1)
        ],
        "warnings": [],
        "formal_write_count": 0,
    }


def _terra_final(prompt: str) -> dict[str, Any]:
    supplied = _json_after_first_line(prompt)
    subject = str(supplied.get("subject") or "")
    capture_id = str(supplied.get("capture_id") or "")
    presented = supplied.get("presented_branches")
    if (
        subject not in {"math", "cs408", "english"}
        or not capture_id
        or not isinstance(presented, list)
        or len(presented) != 3
    ):
        raise RuntimeError("zero_model_terra_final_binding_invalid")
    assessments = []
    for row in presented:
        if not isinstance(row, Mapping):
            raise RuntimeError("zero_model_terra_final_branch_invalid")
        branch_id = str(row.get("branch_id") or "")
        input_kind = str(row.get("input_kind") or "")
        if not branch_id or input_kind not in {"report", "diagnostic"}:
            raise RuntimeError("zero_model_terra_final_branch_invalid")
        assessments.append(
            {
                "branch_id": branch_id,
                "input_kind": input_kind,
                "disposition": (
                    "adopt" if input_kind == "report" else "request_more_evidence"
                ),
                "assessment": "The complete branch material was read in order",
                "evidence_refs": [],
            }
        )
    return {
        "schema_version": "terra_final_draft_v1",
        "subject": subject,
        "capture_id": capture_id,
        "branch_assessments": assessments,
        "subject_sections": {
            "learning_summary": "Synthetic pre-Build transport summary",
            "cross_branch_synthesis": "All three branch materials were considered",
            "recommended_next_step": "Keep the result for technical gate review",
        },
        "proposals": [],
        "conflicts": [],
        "gaps": [],
        "checklist": ["Verify subject and Capture bindings"],
        "warnings": [],
        "formal_write_count": 0,
    }


def _decode_config_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        lowered = raw.casefold()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return raw


def _mcp_server_spec(argv: Sequence[str]) -> dict[str, Any]:
    assignments: dict[str, Any] = {}
    index = 0
    while index < len(argv):
        if argv[index] == "--config" and index + 1 < len(argv):
            raw = argv[index + 1]
            if "=" in raw:
                key, value = raw.split("=", 1)
                assignments[key] = _decode_config_value(value)
            index += 2
        else:
            index += 1
    servers = sorted(
        {
            match.group(1)
            for key in assignments
            if (match := re.fullmatch(r"mcp_servers\.([^.]+)\.command", key))
        }
    )
    if len(servers) != 1:
        raise RuntimeError("zero_model_mcp_server_config_invalid")
    server = servers[0]
    prefix = "mcp_servers." + server + "."
    command = assignments.get(prefix + "command")
    args = assignments.get(prefix + "args")
    cwd = assignments.get(prefix + "cwd")
    if (
        not isinstance(command, str)
        or not Path(command).is_file()
        or not isinstance(args, list)
        or any(not isinstance(value, str) for value in args)
        or not isinstance(cwd, str)
        or not Path(cwd).is_dir()
    ):
        raise RuntimeError("zero_model_mcp_server_config_invalid")
    environment = dict(os.environ)
    for key, value in assignments.items():
        env_prefix = prefix + "env."
        if key.startswith(env_prefix):
            env_name = key[len(env_prefix):]
            if not env_name or not isinstance(value, str):
                raise RuntimeError("zero_model_mcp_environment_invalid")
            environment[env_name] = value
    return {
        "server": server,
        "command": command,
        "args": list(args),
        "cwd": cwd,
        "env": environment,
    }


def _run_subject_mcp(
    *, argv: Sequence[str], prompt_value: Mapping[str, Any], prompt: str
) -> dict[str, Any]:
    spec = _mcp_server_spec(argv)
    session_context = prompt_value.get("read_session")
    session = (
        session_context.get("mcp_read_session")
        if isinstance(session_context, Mapping)
        else None
    )
    artifact_ids = session.get("artifact_ids") if isinstance(session, Mapping) else None
    capture_id = str(prompt_value.get("capture_id") or "")
    if (
        not isinstance(artifact_ids, list)
        or not artifact_ids
        or any(not isinstance(value, str) or not value for value in artifact_ids)
        or not capture_id
    ):
        raise RuntimeError("zero_model_read_session_prompt_invalid")
    request = {
        **spec,
        "artifact_ids": artifact_ids,
        "query": _marker(prompt) or capture_id,
    }
    completed = subprocess.run(
        [spec["command"], "-c", MCP_HELPER],
        input=json.dumps(request, ensure_ascii=False),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=180,
    )
    if completed.returncode != 0:
        detail = completed.stderr[-2000:].replace("\n", " ")
        raise RuntimeError("zero_model_mcp_helper_failed:" + detail)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("zero_model_mcp_helper_output_invalid") from exc
    if not isinstance(result, dict):
        raise RuntimeError("zero_model_mcp_helper_output_invalid")
    return result


def _luna(prompt: str, argv: Sequence[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    supplied = _json_after_first_line(prompt)
    subject = str(supplied.get("subject") or "")
    capture_id = str(supplied.get("capture_id") or "")
    branch = supplied.get("branch")
    branch_id = str(branch.get("branch_id") or "") if isinstance(branch, Mapping) else ""
    if subject not in {"math", "cs408", "english"} or not capture_id or not branch_id:
        raise RuntimeError("zero_model_luna_binding_invalid")
    mcp = _run_subject_mcp(argv=argv, prompt_value=supplied, prompt=prompt)
    calls = mcp.get("calls")
    refs = mcp.get("evidence_refs")
    if (
        not isinstance(calls, list)
        or len(calls) < 3
        or not isinstance(refs, list)
        or not refs
    ):
        raise RuntimeError("zero_model_luna_mcp_coverage_invalid")
    payload = {
        "schema_version": "luna_investigation_draft_v1",
        "subject": subject,
        "capture_id": capture_id,
        "branch_id": branch_id,
        "summary": "Completed task-bound MCP reads for " + branch_id,
        "findings": ["The task identity and all bound artifacts were read"],
        "conflicts": [],
        "missing_evidence": [],
        "confidence": "medium",
        "evidence_refs": refs,
        "formal_write_count": 0,
    }
    return payload, calls


def _write_output(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _emit_mcp_events(server: str, calls: Sequence[Mapping[str, Any]]) -> None:
    for call in calls:
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": server,
                "tool": call["tool"],
                "arguments": call["arguments"],
                "result": call["result"],
                "status": "completed",
            },
        }
        sys.stdout.write(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        sys.stdout.flush()


def _self_test() -> None:
    initial_prompt = (
        "Produce one strict Terra initial draft.\n"
        + json.dumps(
            {
                "subject": "math",
                "capture_id": "CAPTURE-1",
                "frozen_capture": {"marker": "prebuild:v2:math:1"},
                "allowed_collections": ["formal_card_catalog", "search"],
            }
        )
    )
    initial = _terra_initial(initial_prompt)
    assert len(initial["proposed_branches"]) == 3
    assert "prebuild:v2:math:1" in initial["learning_sections"]["task_summary"]
    final_prompt = (
        "Produce one strict Terra final draft.\n"
        + json.dumps(
            {
                "subject": "math",
                "capture_id": "CAPTURE-1",
                "presented_branches": [
                    {"branch_id": f"branch-{index}", "input_kind": "report"}
                    for index in range(1, 4)
                ],
            }
        )
    )
    assert len(_terra_final(final_prompt)["branch_assessments"]) == 3


def main(argv: Sequence[str]) -> int:
    if argv == ["--version"]:
        print(VERSION)
        return 0
    if argv == ["--self-test"]:
        _self_test()
        print(json.dumps({"status": "PASS", "formal_write_count": 0}))
        return 0
    if not argv or argv[0] != "exec":
        raise RuntimeError("zero_model_codex_command_invalid")
    if "--output-last-message" not in argv:
        raise RuntimeError("zero_model_output_path_missing")
    output_index = argv.index("--output-last-message") + 1
    if output_index >= len(argv):
        raise RuntimeError("zero_model_output_path_missing")
    output_path = Path(argv[output_index])
    prompt = sys.stdin.read()
    calls: list[dict[str, Any]] = []
    if prompt.startswith("Produce one strict Terra initial draft."):
        payload = _terra_initial(prompt)
    elif prompt.startswith("Produce one strict Luna investigation draft"):
        payload, calls = _luna(prompt, argv)
    elif prompt.startswith("Produce one strict Terra final draft."):
        payload = _terra_final(prompt)
    else:
        raise RuntimeError("zero_model_prompt_stage_unknown")
    _write_output(output_path, payload)
    if calls:
        server = _mcp_server_spec(argv)["server"]
        _emit_mcp_events(server, calls)
        # Opening nine isolated MCP sessions can stagger the three branches
        # of one Capture by several seconds on a busy host.  Keep the zero-API
        # provider alive long enough for the fixture to prove actual overlap;
        # this delay applies only to the deterministic fixture, never to real
        # Terra or Luna execution.
        hold = float(
            os.environ.get(
                "STUDY_INTAKE_ZERO_MODEL_LUNA_HOLD_SECONDS", "12"
            )
        )
        if hold < 0 or hold > 30:
            raise RuntimeError("zero_model_luna_hold_invalid")
        time.sleep(hold)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        sys.stderr.write(type(exc).__name__ + ":" + str(exc) + "\n")
        raise SystemExit(2)
