"""Fail-closed execution gate for Study Intake external processes.

Live launches require the current task's Dispatcher-issued execution proof.
That proof is revalidated against the active release, HMAC canary queue and
current lease fence before every child launch.  Tests use explicit fixture
roots; missing mode is accepted only for legacy in-process unit tests that do
not opt into this gate.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


EXECUTION_MODES = frozenset(
    {"fixture", "offline", "hosted_synthetic", "live_authorized"}
)
EXTERNAL_PURPOSES = frozenset(
    {
        "terra_orchestrator",
        "luna_reader",
        "terra_critical_reviewer",
        "provider_model_request",
        "production_mcp_launcher",
        "live_capture_consumer",
        "formal_writer",
        "fake_branch_worker",
        "fake_agent_worker",
        "fake_mcp_launcher",
    }
)
LIVE_PURPOSES = EXTERNAL_PURPOSES - {
    "fake_branch_worker",
    "fake_agent_worker",
    "fake_mcp_launcher",
}
TASK_PROOF_PURPOSES = LIVE_PURPOSES - {"formal_writer"}
FAKE_PURPOSES = EXTERNAL_PURPOSES - LIVE_PURPOSES
MODEL_IDS = frozenset({"gpt-5.6-terra", "gpt-5.6-luna"})


class LiveExecutionDenied(RuntimeError):
    def __init__(self, code: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = copy.deepcopy(dict(evidence or {}))


def execution_mode(config: Mapping[str, Any]) -> str | None:
    raw = config.get("execution_mode")
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in EXECUTION_MODES:
        raise LiveExecutionDenied("execution_mode_invalid")
    return raw


def _command_strings(command: Sequence[object]) -> tuple[str, ...]:
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
        raise LiveExecutionDenied("external_command_invalid")
    values = tuple(str(part) for part in command)
    if not values or any(not value or "\x00" in value for value in values):
        raise LiveExecutionDenied("external_command_invalid")
    return values


def _candidate_payload_path(command: tuple[str, ...]) -> Path | None:
    executable = Path(command[0]).expanduser()
    if executable.name.startswith("python") and len(command) > 1:
        for value in command[1:]:
            if value.startswith("-"):
                continue
            return Path(value).expanduser()
    return executable


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=True))
        except (OSError, ValueError):
            continue
        return True
    return False


def _fixture_roots(config: Mapping[str, Any]) -> tuple[Path, ...]:
    fixture = config.get("fixture_execution")
    values = fixture.get("allowed_executable_roots") if isinstance(fixture, Mapping) else None
    if not isinstance(values, list) or not values:
        return ()
    roots: list[Path] = []
    for raw in values:
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise LiveExecutionDenied("fixture_executable_root_invalid")
        roots.append(Path(raw))
    return tuple(roots)


def _looks_like_real_codex(
    command: tuple[str, ...], *, fixture_roots: Sequence[Path] = ()
) -> bool:
    joined = "\n".join(command)
    executable = Path(command[0])
    if "/Applications/ChatGPT.app/Contents/Resources/codex" in joined:
        return True
    executable_is_fixture = _inside(executable, fixture_roots)
    if any(model in command for model in MODEL_IDS) and not executable_is_fixture:
        return True
    # Test fixtures historically use an executable named ``codex``.  That is
    # safe only when the exact file resolves inside an explicit fixture root;
    # a normal PATH/global Codex binary remains fail-closed.
    return executable.name == "codex" and not executable_is_fixture


def assert_external_launch_allowed(
    config: Mapping[str, Any],
    *,
    purpose: str,
    command: Sequence[object],
    task_identity: Mapping[str, Any] | None = None,
    authorization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an external launch before any child process is created."""

    if purpose not in EXTERNAL_PURPOSES:
        raise LiveExecutionDenied("external_launch_purpose_invalid")
    argv = _command_strings(command)
    mode = execution_mode(config)
    evidence = {
        "schema_version": "study-intake-live-execution-gate-decision-v1",
        "execution_mode": mode or "legacy_unset",
        "purpose": purpose,
        "allowed": False,
        "child_process_created": False,
        "formal_write_count": 0,
    }

    # Old unit tests construct CodexRunner with only a model dictionary.  They
    # do not opt into the V2 gate.  Production release validation requires the
    # explicit mode, so this compatibility path cannot appear in a sealed V2
    # config.
    if mode is None:
        return {**evidence, "allowed": True, "reason": "legacy_test_compatibility"}

    if mode == "offline":
        raise LiveExecutionDenied("offline_external_launch_forbidden", evidence=evidence)

    if mode == "fixture":
        if purpose not in FAKE_PURPOSES:
            raise LiveExecutionDenied("fixture_live_launch_forbidden", evidence=evidence)
        roots = _fixture_roots(config)
        if _looks_like_real_codex(argv, fixture_roots=roots):
            raise LiveExecutionDenied("fixture_real_codex_tripwire", evidence=evidence)
        fixture = config.get("fixture_execution")
        if (
            len(argv) >= 3
            and Path(argv[0]).name.startswith("python")
            and argv[1] == "-c"
            and isinstance(fixture, Mapping)
            and fixture.get("allow_inline_python") is True
        ):
            inline_source = argv[2]
            forbidden_inline = (
                "/Applications/ChatGPT.app",
                "gpt-5.6-",
                "socket",
                "urllib",
                "requests",
                "http.client",
                "subprocess",
                "formal_writer",
                "study-read-mcp",
            )
            if any(token in inline_source for token in forbidden_inline):
                raise LiveExecutionDenied(
                    "fixture_inline_python_tripwire", evidence=evidence
                )
            return {
                **evidence,
                "allowed": True,
                "reason": "fixture_inline_python_allowlist",
            }
        payload_path = _candidate_payload_path(argv)
        if payload_path is None or not roots or not _inside(payload_path, roots):
            raise LiveExecutionDenied("fixture_executable_not_allowlisted", evidence=evidence)
        return {**evidence, "allowed": True, "reason": "fixture_allowlist"}

    if mode == "hosted_synthetic":
        trial = config.get("hosted_synthetic_trial")
        runtime_root = (
            Path(str(trial.get("runtime_root"))).resolve()
            if isinstance(trial, Mapping)
            and isinstance(trial.get("runtime_root"), str)
            else None
        )
        subject_roots = (
            trial.get("subject_roots") if isinstance(trial, Mapping) else None
        )
        roots_valid = bool(
            runtime_root is not None
            and runtime_root.is_dir()
            and isinstance(subject_roots, Mapping)
            and set(subject_roots) == {"math", "cs408", "english"}
        )
        if roots_valid:
            for raw_root in subject_roots.values():
                if not isinstance(raw_root, str):
                    roots_valid = False
                    break
                try:
                    Path(raw_root).resolve(strict=True).relative_to(
                        runtime_root.resolve(strict=True)
                    )
                except (OSError, ValueError):
                    roots_valid = False
                    break
        if (
            purpose != "provider_model_request"
            or not isinstance(trial, Mapping)
            or set(trial) != {
                "enabled", "synthetic_only", "capture_source_kind",
                "runtime_root", "subject_roots", "formal_write_count",
            }
            or trial.get("enabled") is not True
            or trial.get("synthetic_only") is not True
            or trial.get("capture_source_kind") != "synthetic"
            or trial.get("formal_write_count") != 0
            or not roots_valid
            or not _looks_like_real_codex(argv)
        ):
            raise LiveExecutionDenied(
                "hosted_synthetic_launch_invalid", evidence=evidence
            )
        return {
            **evidence,
            "allowed": True,
            "reason": "hosted_synthetic_isolated_allowlist",
        }

    if purpose not in LIVE_PURPOSES:
        raise LiveExecutionDenied("live_mode_fake_launch_forbidden", evidence=evidence)
    if purpose not in TASK_PROOF_PURPOSES:
        raise LiveExecutionDenied(
            "task_execution_proof_purpose_forbidden", evidence=evidence
        )
    if not isinstance(task_identity, Mapping) or not isinstance(
        authorization, Mapping
    ):
        raise LiveExecutionDenied(
            "task_execution_proof_missing", evidence=evidence
        )
    from concurrent_dispatch import (
        DispatchError,
        validate_task_execution_proof_for_launch,
    )

    try:
        proof = validate_task_execution_proof_for_launch(
            config, authorization, task_identity=task_identity
        )
    except DispatchError as exc:
        raise LiveExecutionDenied(exc.code, evidence=evidence) from exc
    return {
        **evidence,
        "allowed": True,
        "reason": "task_execution_proof_valid",
        "task_execution_proof_sha256": authorization.get(
            "task_execution_proof_sha256"
        ),
        "release_id": proof.get("release_id"),
        "activation_id": proof.get("activation_id"),
        "unit_sha256": proof.get("unit_sha256"),
        "lease_fence": proof.get("lease_fence"),
    }


def zero_real_call_evidence() -> dict[str, Any]:
    return {
        "schema_version": "study-intake-zero-real-call-evidence-v1",
        "real_terra_call_count": 0,
        "real_luna_call_count": 0,
        "real_provider_model_request_count": 0,
        "production_mcp_task_count": 0,
        "live_capture_created_count": 0,
        "live_capture_consumed_count": 0,
        "formal_write_count": 0,
        "production_accepted": False,
    }


def explicit_offline_config() -> dict[str, Any]:
    return {
        "execution_mode": "offline",
        "live_execution_gate": {
            "enabled": True,
            "default_locked": True,
            "authorization_required": True,
            "authorization_kind": "task_execution_proof_v1",
        },
    }
