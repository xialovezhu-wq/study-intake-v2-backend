"""Fail-closed execution gate for Study Intake external processes.

The gate is intentionally independent from Dispatcher state.  A process may
be started only when both the static execution mode and an exact task-scoped
authorization permit it.  Tests use explicit fixture roots; missing mode is
accepted only for legacy in-process unit tests that do not opt into this gate.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
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
FAKE_PURPOSES = EXTERNAL_PURPOSES - LIVE_PURPOSES
MODEL_IDS = frozenset({"gpt-5.6-terra", "gpt-5.6-luna"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_TRUSTED_ENV = (
    "STUDY_PREPROCESS_RUNTIME_ROOT",
    "STUDY_PREPROCESS_UNIT_SHA256",
    "STUDY_PREPROCESS_LEASE_FENCE",
    "STUDY_PREPROCESS_LEASE_OWNER_ID",
    "STUDY_PREPROCESS_CONTEXT_ROOT",
)


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


def _local_trusted_claim(
    config: Mapping[str, Any],
    *,
    purpose: str,
    task_identity: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Reopen the exact already-claimed local task without issuing authority.

    The dispatcher owns the only claim lifecycle.  This policy merely proves
    that the Provider launch occurs inside the task context created after that
    claim.  Absence of the task environment keeps historical/manual callers on
    the legacy authorization path.
    """

    if purpose != "provider_model_request":
        return None
    raw = {name: os.environ.get(name) for name in LOCAL_TRUSTED_ENV}
    configured = [value is not None for value in raw.values()]
    configured_runtime = config.get("runtime_root")
    if any(configured):
        if not all(configured):
            raise LiveExecutionDenied("local_trusted_context_incomplete")
        runtime_text = str(raw["STUDY_PREPROCESS_RUNTIME_ROOT"])
        unit_sha256 = str(raw["STUDY_PREPROCESS_UNIT_SHA256"])
        owner_id = str(raw["STUDY_PREPROCESS_LEASE_OWNER_ID"])
        fence_text = str(raw["STUDY_PREPROCESS_LEASE_FENCE"])
        context_text = str(raw["STUDY_PREPROCESS_CONTEXT_ROOT"])
    elif (
        isinstance(task_identity, Mapping)
        and task_identity.get("local_trusted") is True
    ):
        runtime_text = str(task_identity.get("runtime_root") or "")
        unit_sha256 = str(task_identity.get("unit_sha256") or "")
        owner_id = str(task_identity.get("lease_owner_id") or "")
        fence_text = str(task_identity.get("lease_fence") or "")
        context_text = str(task_identity.get("context_root") or "")
    else:
        return None
    try:
        fence = int(fence_text)
        runtime_root = Path(runtime_text).resolve(strict=True)
        context_root = Path(context_text).resolve(strict=True)
        configured_root = Path(str(configured_runtime)).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise LiveExecutionDenied("local_trusted_context_invalid") from exc
    expected_context = (
        runtime_root
        / "dispatch"
        / "contexts"
        / unit_sha256
        / f"fence-{fence}"
    )
    if (
        not isinstance(configured_runtime, str)
        or runtime_root != configured_root
        or SHA256_RE.fullmatch(unit_sha256) is None
        or not owner_id
        or len(owner_id) > 256
        or fence < 1
        or str(fence) != fence_text
        or context_root != expected_context
        or not context_root.is_dir()
    ):
        raise LiveExecutionDenied("local_trusted_context_invalid")

    state_root = runtime_root / "dispatch" / "state"
    lock_path = state_root / "dispatch.lock"
    lease_path = state_root / "leases" / f"{unit_sha256}.json"
    if (
        lock_path.is_symlink()
        or not lock_path.is_file()
        or lease_path.is_symlink()
        or not lease_path.is_file()
        or lease_path.stat().st_size > 64 * 1024
    ):
        raise LiveExecutionDenied("local_trusted_claim_missing")
    try:
        with lock_path.open("rb") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
            try:
                lease = json.loads(lease_path.read_text(encoding="utf-8"))
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LiveExecutionDenied("local_trusted_claim_invalid") from exc
    subject = lease.get("subject") if isinstance(lease, Mapping) else None
    if (
        not isinstance(lease, Mapping)
        or lease.get("schema_version") != "study-intake-dispatch-lease-v1"
        or lease.get("unit_sha256") != unit_sha256
        or lease.get("owner_id") != owner_id
        or lease.get("fence") != fence
        or lease.get("status") != "claimed"
        or subject not in {"math", "cs408", "english"}
    ):
        raise LiveExecutionDenied("local_trusted_claim_invalid")

    adapters = config.get("adapters")
    route = adapters.get(subject) if isinstance(adapters, Mapping) else None
    if not isinstance(route, Mapping) or route.get("enabled") is not True:
        raise LiveExecutionDenied("local_trusted_subject_route_missing")
    if task_identity is not None:
        identity_unit = task_identity.get("unit_sha256")
        identity_subject = task_identity.get("subject")
        if (
            identity_unit not in {None, unit_sha256}
            or identity_subject not in {None, subject}
            or task_identity.get("local_trusted") is True
            and (
                not isinstance(
                    task_identity.get("frozen_payload_sha256"), str
                )
                or SHA256_RE.fullmatch(
                    str(task_identity.get("frozen_payload_sha256"))
                )
                is None
                or not isinstance(task_identity.get("capture_id"), str)
                or not task_identity.get("capture_id")
            )
        ):
            raise LiveExecutionDenied("local_trusted_task_binding_mismatch")
    return {
        "subject": subject,
        "unit_sha256": unit_sha256,
        "lease_fence": fence,
    }


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
    local_claim = _local_trusted_claim(
        config,
        purpose=purpose,
        task_identity=task_identity,
    )
    if local_claim is not None:
        return {
            **evidence,
            **local_claim,
            "allowed": True,
            "reason": "local_trusted_claimed_capture",
        }
    if not isinstance(task_identity, Mapping) or not isinstance(authorization, Mapping):
        raise LiveExecutionDenied("manual_live_authorization_missing", evidence=evidence)
    from manual_capture_admission import validate_authorization_for_task

    validate_authorization_for_task(authorization, task_identity=task_identity)
    return {**evidence, "allowed": True, "reason": "manual_live_authorization_valid"}


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
        },
    }
