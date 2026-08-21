#!/usr/bin/env python3
"""Run three isolated hosted-synthetic AnalysisPackageV1 trials.

The harness builds the candidate Shared MCP release and synthetic subject
repositories under one temporary root.  It never opens production subject
roots, Capture ledgers, formal data, runtime state, current, or LaunchAgents.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
import struct
import zlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "lib", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from preprocessor_core import Candidate, CodexRunner  # noqa: E402
from processing_plugin import ProcessingPluginHost  # noqa: E402
from tests.portable_plugin_fixture import build_portable_plugin_fixture  # noqa: E402


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _authority_response(
    *, subject: str, arguments: dict[str, Any], server_release: str
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "study-read-mcp.v3",
        "profile": "background",
        "read_route": arguments["route"],
        "generation": "synthetic-generation-1",
        "authority_fingerprint": "b" * 64,
        "server_release": server_release,
        "preprocessor_release": "a" * 64,
        "formal_write_count": 0,
        "model_call_count": 0,
        "items": [{
            "subject": subject,
            "available": True,
            "generation": "synthetic-generation-1",
            "authority_fingerprint": "b" * 64,
            "adapter_release": server_release,
        }],
    }


def _preflight_response(
    *, subject: str, arguments: dict[str, Any], server_release: str
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "study-read-mcp.v3",
        "profile": "background",
        "subject": subject,
        "read_route": arguments["route"],
        "generation": "synthetic-generation-1",
        "authority_fingerprint": "b" * 64,
        "adapter_release": server_release,
        "server_release": server_release,
        "formal_write_count": 0,
        "model_call_count": 0,
        "items": [{
            "operation": "curation_inventory",
            "data_role": "projection",
            "projection_event_binding": {"event_count": 1},
            "items": [],
        }],
    }


def _math_artifacts(root: Path) -> tuple[dict[str, Any], ...]:
    root.mkdir(parents=True, exist_ok=True)
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )
    question = root / "synthetic-question.png"
    solution = root / "synthetic-solution.md"
    question.write_bytes(png)
    solution.write_text("# Synthetic solution\n\nOnly synthetic evidence.\n", encoding="utf-8")
    dialogue = {
        "schema_version": "synthetic-math-dialogue-v1",
        "turns": [
            {"speaker": "user", "text": "Synthetic first step."},
            {"speaker": "assistant", "text": "Synthetic check only."},
        ],
    }
    learning = {
        "schema_version": "synthetic-math-learning-record-v1",
        "capture_id": "SYN-MATH-001",
        "result": "synthetic",
    }
    return (
        {
            "artifact_id": "dialogue",
            "artifact_kind": "dialogue",
            "content": dialogue,
            "sha256": hashlib.sha256(_canonical(dialogue)).hexdigest(),
        },
        {
            "artifact_id": "learning-record",
            "artifact_kind": "learning_record",
            "content": learning,
            "sha256": hashlib.sha256(_canonical(learning)).hexdigest(),
        },
        {
            "artifact_id": "question-image",
            "artifact_kind": "question_image",
            "path": str(question),
            "sha256": hashlib.sha256(png).hexdigest(),
        },
        {
            "artifact_id": "solution-text",
            "artifact_kind": "solution_text",
            "path": str(solution),
            "sha256": hashlib.sha256(solution.read_bytes()).hexdigest(),
        },
    )


def _open_context(
    *, host: ProcessingPluginHost, subject: str, server_release: str, root: Path
) -> tuple[dict[str, Any], str]:
    capture_ids = {
        "math": "SYN-MATH-001",
        "cs408": "SYN-408-001",
        "english": "SYN-EN-001",
    }
    scenes = {
        "math": "formal_problem",
        "cs408": "formal_problem",
        "english": "intensive_reading",
    }
    del server_release
    facts = {
        "synthetic": True,
        "subject": subject,
        "prompt": "Synthetic evidence only.",
    }
    context = host.open_read_session(
        subject=subject,
        capture_id=capture_ids[subject],
        study_date="2026-08-21",
        input_fingerprint="1" * 64,
        input_binding={"source_kind": "synthetic"},
        capture_facts_sha256=hashlib.sha256(_canonical(facts)).hexdigest(),
        capture_facts=facts,
        capture_scene=scenes[subject],
        capture_identity={"content_fingerprint": "1" * 64},
        capture_artifacts=(
            _math_artifacts(root) if subject == "math" else ()
        ),
        captured_at="2026-08-21T01:00:00+00:00",
        provider_schema_sha256="2" * 64,
        canonical_schema_sha256="3" * 64,
        validator_sha256="4" * 64,
    )
    return context, capture_ids[subject]


def _roles() -> dict[str, dict[str, Any]]:
    def role(model: str) -> dict[str, Any]:
        return {
            "model": model,
            "reasoning_effort": "max",
            "sandbox_mode": "read-only",
            "agents_enabled": False,
        }

    return {
        "terra_analysis": role("gpt-5.6-terra"),
        "luna_analysis": role("gpt-5.6-luna"),
        "terra_critical_review": role("gpt-5.6-terra"),
    }


def run_trial(*, codex_path: Path, mcp_python: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="study-intake-hosted-synthetic-") as folder:
        trial_root = Path(folder).resolve()
        portable = build_portable_plugin_fixture(
            trial_root,
            ROOT,
            mcp_python=mcp_python,
        )
        runtime = trial_root / "runtime"
        runtime.mkdir(parents=True)
        synthetic_release_id = "a" * 64
        synthetic_release = runtime / "releases" / synthetic_release_id
        synthetic_release.mkdir(parents=True)
        (runtime / "packages/objects").mkdir(parents=True)
        (synthetic_release / "release.json").write_bytes(
            _canonical(
                {
                    "schema_version": "synthetic-preprocessor-release-v1",
                    "release_id": synthetic_release_id,
                    "component_inventory": {},
                    "formal_write_count": 0,
                }
            )
        )
        (runtime / "current").symlink_to(synthetic_release)
        key_path = runtime / "dispatch/state/authority.key"
        key_path.parent.mkdir(parents=True)
        key_path.write_bytes(b"k" * 32)
        key_path.chmod(0o600)
        plugin_config = {
            "enabled": True,
            "root": str(portable.plugin_root),
            "component_lock_path": str(
                portable.plugin_root / "component-lock.json"
            ),
            "mcp_client_python": str(portable.mcp_python),
            "mcp_project_root": str(portable.mcp_root),
            "authority_key_path": str(key_path),
            "profile": "background",
            "timeout_seconds": 30,
        }
        host = ProcessingPluginHost(
            plugin_config,
            runtime_root=runtime,
            candidate_release_id="a" * 64,
            subject_roots=portable.subject_roots,
            require_authority_snapshot=True,
        )
        server_release = "0.4.1+sha256." + portable.release_id
        summaries: list[dict[str, Any]] = []
        for subject in ("math", "cs408", "english"):
            context, capture_id = _open_context(
                host=host,
                subject=subject,
                server_release=server_release,
                root=trial_root / subject,
            )
            config = {
                "codex_path": str(codex_path),
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "max_images": 8,
                "execution_mode": "hosted_synthetic",
                "models": _roles(),
                "analysis_package_v1": {
                    "enabled": True,
                    "stage_output_schema": str(
                        ROOT / "schemas/analysis-stage-report-v1.json"
                    ),
                    "max_prompt_bytes": 262144,
                    "max_output_bytes": 262144,
                },
                "hosted_synthetic_trial": {
                    "enabled": True,
                    "synthetic_only": True,
                    "capture_source_kind": "synthetic",
                    "runtime_root": str(trial_root),
                    "subject_roots": {
                        name: str(path)
                        for name, path in portable.subject_roots.items()
                    },
                    "formal_write_count": 0,
                },
                "task_model_temp_root": str(runtime / "state/model-output"),
                "task_context_root": str(runtime),
            }
            runner = CodexRunner(config, runtime / subject)
            runner._processing_host = host
            candidate = Candidate(
                subject=subject,
                capture_id=capture_id,
                study_date="2026-08-21",
                recorded_at="2026-08-21T09:00:00+08:00",
                input_fingerprint="1" * 64,
                input_binding={"source_kind": "synthetic"},
                model_input={"synthetic": True, "subject": subject},
                allowed_evidence_refs=(),
                image_paths=(),
                target_label=f"synthetic-{subject}",
                canonical_state="synthetic",
                sol_state="not_started",
            )
            original_background = runner._background_context
            runner._background_context = lambda _candidate, value=context: value  # type: ignore[method-assign]
            try:
                result = runner.run_analysis_package_v1(candidate)
            finally:
                runner._background_context = original_background  # type: ignore[method-assign]
            package = result.analysis
            summaries.append(
                {
                    "subject": subject,
                    "capture_id": capture_id,
                    "package_id": package["package_id"],
                    "package_sha256": package["package_sha256"],
                    "stage_order": package["stage_order"],
                    "requested_models": [
                        row["requested_model"] for row in package["stages"]
                    ],
                    "semantic_stage_count": result.semantic_stage_count,
                    "provider_request_count": result.provider_request_count,
                    "mcp_tool_call_count": result.mcp_tool_call_count,
                    "formal_write_count": package["formal_write_count"],
                    "status": package["status"],
                }
            )
        return {
            "schema_version": "study-intake-hosted-synthetic-trial-v1",
            "subjects": summaries,
            "hosted_model_call_count": sum(
                row["semantic_stage_count"] for row in summaries
            ),
            "real_capture_count": 0,
            "production_formal_write_count": 0,
            "production_surface_touched": False,
            "status": "passed",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-path", type=Path, required=True)
    parser.add_argument("--mcp-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.codex_path.is_file() or not os.access(args.codex_path, os.X_OK):
        raise SystemExit("codex executable is required")
    if not args.mcp_python.is_file() or not os.access(args.mcp_python, os.X_OK):
        raise SystemExit("MCP Python executable is required")
    previous_fixture = os.environ.pop("STUDY_INTAKE_FIXTURE_EXECUTION", None)
    try:
        try:
            result = run_trial(
                codex_path=args.codex_path.resolve(),
                mcp_python=args.mcp_python.expanduser().absolute(),
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "error_type": type(exc).__name__,
                        "error_code": getattr(exc, "code", str(exc)),
                        "diagnostic": getattr(exc, "diagnostic", {}),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise
    finally:
        if previous_fixture is not None:
            os.environ["STUDY_INTAKE_FIXTURE_EXECUTION"] = previous_fixture
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
