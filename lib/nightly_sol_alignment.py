"""Exact-command nightly batching and thin Subject Sol handoff for V2."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from analysis_package_v1 import (
    AnalysisPackageError,
    AnalysisPackageStore,
    PACKAGE_SCHEMA,
    canonical_bytes,
    sha256_value,
    validate_durable_capture,
)
from analysis_package_v2 import (
    PACKAGE_SCHEMA as PACKAGE_V2_SCHEMA,
    _validate_terra_execution,
    _validate_terra_initial,
    reopen_analysis_package_v2,
)
from isolated_authority_publishers import (
    IndependentUserIntentPublisher,
    IsolatedWriterAdapterPublisher,
)
from subject_sol_contract import (
    SUBJECT_WRITER_ADAPTERS,
    SubjectSolContractError,
    SubjectSolRuntimeStore,
    _document_sha256,
)
from multi_agent_report_contract import (
    _validate_diagnostic_record,
    _validate_read_bundle,
    validate_dual_report_plan,
    validate_luna_investigation_report,
    validate_luna_investigation_report_v2,
    validate_sol_handoff_v3,
    validate_terra_final_report_v2,
)


BATCH_SCHEMA = "study-intake-nightly-sol-batch-v2"
V2_HANDOFF_SCHEMA = "study-intake-nightly-multi-agent-handoff-v1"
RESULT_SCHEMA = "study-intake-nightly-sol-adapter-result-v1"
AUTHORIZATION_SCHEMA = "study-intake-nightly-command-authorization-v1"
SUBJECT_LABELS = {"数学": "math", "408": "cs408", "英语": "english"}
ABSOLUTE_COMMAND = re.compile(
    r"^开始 (\d{4}-\d{2}-\d{2}) (数学|408|英语)正式入库$"
)
TODAY_COMMAND = re.compile(r"^开始今天的(数学|408|英语)正式入库$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class NightlySolError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _date(value: str) -> str:
    try:
        if dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise NightlySolError("nightly_command_date_invalid") from exc
    return value


def _sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise NightlySolError(code)
    return value


def parse_nightly_command(text: str, *, today: dt.date) -> dict[str, Any]:
    if not isinstance(text, str) or text != text.strip():
        raise NightlySolError("nightly_command_not_exact")
    absolute = ABSOLUTE_COMMAND.fullmatch(text)
    if absolute is not None:
        subject = SUBJECT_LABELS[absolute.group(2)]
        date_value = _date(absolute.group(1))
        authorization = build_command_authorization(
            subject=subject,
            capture_intake_date=date_value,
            normalized_command=text,
        )
        return {
            "subject": subject,
            "capture_intake_date": date_value,
            "normalized_command": text,
            "authorization": authorization,
        }
    relative = TODAY_COMMAND.fullmatch(text)
    if relative is not None:
        date_value = today.isoformat()
        label = relative.group(1)
        normalized = f"开始 {date_value} {label}正式入库"
        authorization = build_command_authorization(
            subject=SUBJECT_LABELS[label],
            capture_intake_date=date_value,
            normalized_command=normalized,
        )
        return {
            "subject": SUBJECT_LABELS[label],
            "capture_intake_date": date_value,
            "normalized_command": normalized,
            "authorization": authorization,
        }
    raise NightlySolError("nightly_command_not_exact")


def build_command_authorization(
    *, subject: str, capture_intake_date: str, normalized_command: str
) -> dict[str, str]:
    if subject not in SUBJECT_LABELS.values():
        raise NightlySolError("nightly_subject_invalid")
    date_value = _date(capture_intake_date)
    label = next(
        label for label, candidate in SUBJECT_LABELS.items() if candidate == subject
    )
    expected = f"开始 {date_value} {label}正式入库"
    if normalized_command != expected:
        raise NightlySolError("nightly_command_authorization_invalid")
    command_sha = hashlib.sha256(normalized_command.encode("utf-8")).hexdigest()
    core = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "subject": subject,
        "capture_intake_date": date_value,
        "normalized_command": normalized_command,
        "command_sha256": command_sha,
    }
    return {
        **core,
        "authorization_id": "NAUTH-" + sha256_value(core)[:24].upper(),
    }


def validate_command_authorization(
    value: Mapping[str, Any], *, subject: str, capture_intake_date: str
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "subject", "capture_intake_date",
        "normalized_command", "command_sha256", "authorization_id",
    }:
        raise NightlySolError("nightly_command_authorization_invalid")
    expected = build_command_authorization(
        subject=subject,
        capture_intake_date=capture_intake_date,
        normalized_command=str(value.get("normalized_command") or ""),
    )
    if dict(value) != expected:
        raise NightlySolError("nightly_command_authorization_invalid")
    return expected


def _package_binding(package: Mapping[str, Any]) -> dict[str, str]:
    if package.get("schema_version") not in {PACKAGE_SCHEMA, PACKAGE_V2_SCHEMA}:
        raise NightlySolError("analysis_package_invalid")
    digest = hashlib.sha256(canonical_bytes(package)).hexdigest()
    return {
        "capture_id": str(package["capture_id"]),
        "package_sha256": digest,
        "package_ref": "study-intake-analysis-package://sha256/" + digest,
    }


def _binding_object(
    store: AnalysisPackageStore,
    binding: Mapping[str, Any],
    *,
    capture: bool = False,
    code: str,
) -> dict[str, Any]:
    digest = _sha(binding.get("sha256"), code)
    expected_prefix = (
        "study-intake-durable-capture://sha256/"
        if capture
        else {
            "sealed_plan": "study-intake-orchestration-read-plan://sha256/",
            "read_bundle": "study-intake-read-bundle://sha256/",
            "terra_initial": "study-intake-terra-initial-analysis://sha256/",
            "terra_final": "study-intake-terra-final-report://sha256/",
            "sol_handoff": "study-intake-sol-handoff-envelope://sha256/",
        }.get(str(binding.get("kind")))
    )
    if expected_prefix is None or binding.get("ref") != expected_prefix + digest:
        raise NightlySolError(code)
    try:
        value = (
            store.reopen_capture(digest)
            if capture
            else store.reopen_analysis_object(digest)
        )
    except AnalysisPackageError as exc:
        raise NightlySolError(code) from exc
    if value.get("schema_version") != binding.get("schema_version"):
        raise NightlySolError(code)
    return value


def _content_addressed_json(
    path: Path, digest: str, code: str, *, allow_raw: bool = False
) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise NightlySolError(code) from exc
    if path.is_symlink() or hashlib.sha256(payload).hexdigest() != digest:
        raise NightlySolError(code)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        value = None
    if not isinstance(value, dict):
        if not allow_raw:
            raise NightlySolError(code)
        try:
            raw_text = payload.decode("utf-8")
        except UnicodeError as exc:
            raise NightlySolError(code) from exc
        return {
            "artifact_sha256": digest,
            "payload_format": "utf8_raw_provider_output",
            "raw_text": raw_text,
        }
    return value


def _artifact_candidates(root: Path, digest: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(f"{digest}.json")
        if path.is_file() and not path.is_symlink()
    ) if root.is_dir() else []


def _artifact_from_ref(
    store: AnalysisPackageStore,
    *,
    digest: str,
    ref: str,
    code: str,
) -> dict[str, Any]:
    checked_digest = _sha(digest, code)
    routes = {
        "study-intake-direct-model-stage-raw://sha256/": (
            "private/reports/direct-model-stage-raw", True, True
        ),
        "study-intake-direct-model-stage-execution://sha256/": (
            "private/reports/direct-model-stage-execution", False, False
        ),
        "study-intake-direct-model-stage-normalization://sha256/": (
            "private/reports/direct-model-stage-normalization", False, False
        ),
        "study-intake-model-stage-raw-output://sha256/": (
            "dispatch/model-stage-raw-outputs", True, True
        ),
        "study-intake-model-stage-execution://sha256/": (
            "dispatch/model-stage-execution-receipts", True, False
        ),
        "study-intake-model-stage-normalization://sha256/": (
            "dispatch/model-stage-normalization-receipts", True, False
        ),
        "study-intake-mcp-stage-transcript://sha256/": (
            "private/reports/mcp-stage-transcripts", False, False
        ),
        "study-intake-mcp-read-session-call://sha256/": (
            "dispatch/mcp-read-session-call-receipts", False, False
        ),
        "study-intake-mcp-read-session://sha256/": (
            "dispatch/mcp-read-session-receipts", False, False
        ),
        "study-intake-mcp-investigation-session://sha256/": (
            "dispatch/mcp-read-session-receipts", False, False
        ),
    }
    route = next(
        (
            (prefix, relative, recursive, allow_raw)
            for prefix, (relative, recursive, allow_raw) in routes.items()
            if ref == prefix + checked_digest
        ),
        None,
    )
    if route is None:
        raise NightlySolError(code)
    _prefix, relative, recursive, allow_raw = route
    base = store.root / relative
    direct = base / "sha256" / checked_digest[:2] / f"{checked_digest}.json"
    candidates = (
        _artifact_candidates(base, checked_digest)
        if recursive
        else [direct]
    )
    if not candidates:
        raise NightlySolError(code)
    values = [
        _content_addressed_json(
            path, checked_digest, code, allow_raw=allow_raw
        )
        for path in candidates
    ]
    if any(value != values[0] for value in values[1:]):
        raise NightlySolError(code)
    return values[0]


def _authority_key(store: AnalysisPackageStore) -> bytes:
    path = store.root / "dispatch/state/authority.key"
    try:
        node = path.lstat()
        key = path.read_bytes()
    except OSError as exc:
        raise NightlySolError("multi_agent_authority_key_missing") from exc
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(node.st_mode) != 0o600
        or len(key) != 32
    ):
        raise NightlySolError("multi_agent_authority_key_invalid")
    return key


def _verify_hmac_receipt(
    value: Mapping[str, Any], *, key: bytes, purpose: str, code: str
) -> dict[str, Any]:
    core = {
        name: copy.deepcopy(item)
        for name, item in value.items()
        if name not in {"hmac_key_id", "hmac_sha256"}
    }
    expected = hmac.new(
        key,
        canonical_bytes({"purpose": purpose, "payload": core}),
        hashlib.sha256,
    ).hexdigest()
    if (
        value.get("hmac_key_id") != hashlib.sha256(key).hexdigest()
        or not hmac.compare_digest(str(value.get("hmac_sha256") or ""), expected)
        or value.get("formal_write_count") != 0
    ):
        raise NightlySolError(code)
    return copy.deepcopy(dict(value))


def _read_session_manifest(
    store: AnalysisPackageStore, digest: str
) -> dict[str, Any]:
    checked = _sha(digest, "multi_agent_read_session_manifest_invalid")
    path = (
        store.root / "private/mcp-read-sessions/sha256"
        / checked[:2] / f"{checked}.json"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NightlySolError("multi_agent_read_session_manifest_invalid") from exc
    core = {
        name: copy.deepcopy(item)
        for name, item in value.items()
        if name != "manifest_sha256"
    } if isinstance(value, Mapping) else {}
    if (
        path.is_symlink()
        or not isinstance(value, dict)
        or value.get("manifest_sha256") != checked
        or sha256_value(core) != checked
        or value.get("formal_write_count") != 0
    ):
        raise NightlySolError("multi_agent_read_session_manifest_invalid")
    return value


def _all_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        result: set[str] = set()
        for item in value.values():
            result.update(_all_strings(item))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result = set()
        for item in value:
            result.update(_all_strings(item))
        return result
    return set()


def _digest_from_ref(ref: Any, prefixes: Sequence[str], code: str) -> str:
    if not isinstance(ref, str):
        raise NightlySolError(code)
    for prefix in prefixes:
        if ref.startswith(prefix):
            digest = ref[len(prefix):]
            if SHA256_RE.fullmatch(digest):
                return digest
    raise NightlySolError(code)


def _execution_chain(
    store: AnalysisPackageStore,
    *,
    binding: Mapping[str, Any],
    provider_stage_name: str,
    capture_id: str,
    code: str,
) -> dict[str, Any]:
    raw = _artifact_from_ref(
        store,
        digest=str(binding["raw_output_sha256"]),
        ref=str(binding["raw_output_ref"]),
        code=code,
    )
    execution = _artifact_from_ref(
        store,
        digest=str(binding["stage_execution_receipt_sha256"]),
        ref=str(binding["stage_execution_receipt_ref"]),
        code=code,
    )
    normalization = _artifact_from_ref(
        store,
        digest=str(binding["normalization_receipt_sha256"]),
        ref=str(binding["normalization_receipt_ref"]),
        code=code,
    )
    semantic_stage_name = (
        "critical_review"
        if provider_stage_name.endswith("_critical_review")
        else "analysis"
    )
    provider_stage_names = {
        str(value)
        for value in (
            execution.get("provider_stage_name"),
            normalization.get("provider_stage_name"),
        )
        if value is not None
    }
    semantic_stage_names = {
        str(value)
        for value in (
            execution.get("stage_name"), normalization.get("stage_name")
        )
        if value is not None
    }
    raw_digests = {
        value
        for value in (
            execution.get("raw_output_object_sha256"),
            execution.get("raw_output_sha256"),
            normalization.get("raw_output_object_sha256"),
            normalization.get("raw_output_sha256"),
        )
        if value is not None
    }
    execution_digests = {
        value
        for value in (
            normalization.get("execution_receipt_sha256"),
            normalization.get("stage_execution_receipt_sha256"),
        )
        if value is not None
    }
    execution_bindings = [
        dict(value)
        for value in (
            raw.get("execution_binding"),
            execution.get("execution_binding"),
            normalization.get("execution_binding"),
        )
        if isinstance(value, Mapping)
    ]
    if (
        provider_stage_names and provider_stage_names != {provider_stage_name}
        or semantic_stage_names
        and not semantic_stage_names <= {provider_stage_name, semantic_stage_name}
        or raw_digests and raw_digests != {binding["raw_output_sha256"]}
        or execution_digests
        and execution_digests != {binding["stage_execution_receipt_sha256"]}
        or execution.get("formal_write_count") != 0
        or normalization.get("formal_write_count") != 0
        or raw.get("formal_write_count") not in {None, 0}
        or execution_bindings
        and (
            any(value != execution_bindings[0] for value in execution_bindings[1:])
            or execution_bindings[0].get("subject")
            != provider_stage_name.split("_", 1)[0]
            or execution_bindings[0].get("capture_id") != capture_id
            or execution_bindings[0].get("provider_stage_name")
            != provider_stage_name
        )
    ):
        raise NightlySolError(code)
    return {
        "binding": copy.deepcopy(dict(binding)),
        "raw_output": raw,
        "stage_execution_receipt": execution,
        "normalization_receipt": normalization,
    }


def _luna_success_handoff(
    store: AnalysisPackageStore,
    *,
    key: bytes,
    plan: Mapping[str, Any],
    read_bundle: Mapping[str, Any],
    branch: Mapping[str, Any],
    package_binding: Mapping[str, Any],
    branch_result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    branch_id = str(branch["branch_id"])
    if (
        package_binding.get("kind") != "investigation_report"
        or package_binding.get("branch_id") != branch_id
        or package_binding.get("report_sha256") != report.get("report_sha256")
    ):
        raise NightlySolError("multi_agent_luna_report_binding_invalid")
    checked_report = (
        validate_luna_investigation_report_v2(
            report,
            plan=plan,
            read_bundle=read_bundle,
            branch_result=branch_result,
        )
        if report.get("schema_version") == "luna_investigation_report_v2"
        else validate_luna_investigation_report(
            report,
            plan=plan,
            read_bundle=read_bundle,
            branch_result=branch_result,
        )
    )
    artifacts = checked_report.get("execution_artifacts")
    if not isinstance(artifacts, Mapping):
        raise NightlySolError("multi_agent_luna_execution_artifacts_missing")
    session = _read_session_manifest(
        store, str(artifacts["read_session_manifest_sha256"])
    )
    if (
        session.get("read_session_id") != artifacts.get("read_session_id")
        or session.get("read_session_id") != branch_result.get("read_session_id")
        or session.get("subject") != plan.get("subject")
        or session.get("capture_id") != plan.get("capture_id")
        or session.get("candidate_release_id") != plan.get("release_id")
        or session.get("generation") != plan.get("generation")
        or session.get("artifact_ids") != branch.get("allowed_task_artifact_ids")
        or (
            session.get("authority_snapshot_manifest_sha256") is not None
            and session.get("authority_snapshot_manifest_sha256")
            != plan.get("authority_snapshot_sha256")
        )
    ):
        raise NightlySolError("multi_agent_read_session_binding_invalid")
    opened_sha = _digest_from_ref(
        artifacts.get("opened_session_receipt_ref"),
        ("study-intake-mcp-read-session://sha256/",),
        "multi_agent_opened_session_receipt_invalid",
    )
    opened = _verify_hmac_receipt(
        _artifact_from_ref(
            store,
            digest=opened_sha,
            ref=str(artifacts["opened_session_receipt_ref"]),
            code="multi_agent_opened_session_receipt_invalid",
        ),
        key=key,
        purpose="mcp-read-session-receipt-v1",
        code="multi_agent_opened_session_receipt_invalid",
    )
    final_sha = _digest_from_ref(
        artifacts.get("final_session_receipt_ref"),
        ("study-intake-mcp-investigation-session://sha256/",),
        "multi_agent_final_session_receipt_invalid",
    )
    final = _verify_hmac_receipt(
        _artifact_from_ref(
            store,
            digest=final_sha,
            ref=str(artifacts["final_session_receipt_ref"]),
            code="multi_agent_final_session_receipt_invalid",
        ),
        key=key,
        purpose="mcp-investigation-session-receipt-v1",
        code="multi_agent_final_session_receipt_invalid",
    )
    transcript = _artifact_from_ref(
        store,
        digest=str(artifacts["mcp_transcript_sha256"]),
        ref=str(artifacts["mcp_transcript_ref"]),
        code="multi_agent_mcp_transcript_invalid",
    )
    call_receipt_raw = _artifact_from_ref(
        store,
        digest=str(artifacts["mcp_call_receipt_sha256"]),
        ref=str(artifacts["mcp_call_receipt_ref"]),
        code="multi_agent_mcp_call_receipt_invalid",
    )
    call_purpose = {
        "mcp_stage_call_receipt_v1": "mcp-read-session-model-calls-v1",
        "mcp_stage_call_receipt_v2": "mcp-read-session-model-calls-v2",
    }.get(str(call_receipt_raw.get("schema_version")))
    if call_purpose is None:
        raise NightlySolError("multi_agent_mcp_call_receipt_invalid")
    call_receipt = _verify_hmac_receipt(
        call_receipt_raw,
        key=key,
        purpose=call_purpose,
        code="multi_agent_mcp_call_receipt_invalid",
    )
    execution = _execution_chain(
        store,
        binding={
            "raw_output_sha256": artifacts["raw_output_sha256"],
            "raw_output_ref": artifacts["raw_output_ref"],
            "stage_execution_receipt_sha256": artifacts[
                "stage_execution_receipt_sha256"
            ],
            "stage_execution_receipt_ref": artifacts[
                "stage_execution_receipt_ref"
            ],
            "normalization_receipt_sha256": artifacts[
                "normalization_receipt_sha256"
            ],
            "normalization_receipt_ref": artifacts[
                "normalization_receipt_ref"
            ],
        },
        provider_stage_name=f"{plan['subject']}_luna_analysis",
        capture_id=str(plan["capture_id"]),
        code="multi_agent_luna_execution_chain_invalid",
    )
    evidence_refs = {
        str(row.get("evidence_ref"))
        for row in branch_result.get("evidence", [])
        if isinstance(row, Mapping)
    }
    if (
        opened.get("phase") != "opened"
        or opened.get("read_session_id") != session["read_session_id"]
        or opened.get("read_session_manifest_sha256") != session["manifest_sha256"]
        or final.get("phase") != "investigation_complete"
        or final.get("branch_id") != branch_id
        or final.get("read_session_id") != session["read_session_id"]
        or final.get("opened_receipt_sha256") != opened_sha
        or final.get("mcp_call_receipt_sha256")
        != artifacts["mcp_call_receipt_sha256"]
        or final.get("mcp_transcript_sha256") != artifacts["mcp_transcript_sha256"]
        or transcript.get("subject") != plan["subject"]
        or transcript.get("read_session_id") != session["read_session_id"]
        or transcript.get("read_session_manifest_sha256") != session["manifest_sha256"]
        or transcript.get("calls") != branch_result.get("calls")
        or transcript.get("formal_write_count") != 0
        or call_receipt.get("subject") != plan["subject"]
        or call_receipt.get("read_session_id") != session["read_session_id"]
        or call_receipt.get("transcript_sha256") != artifacts["mcp_transcript_sha256"]
        or call_receipt.get("phase") != "model_stage_calls"
        or call_receipt.get("mcp_tool_call_count") != len(branch_result.get("calls", []))
        or not evidence_refs <= _all_strings(transcript)
    ):
        raise NightlySolError("multi_agent_luna_read_evidence_binding_invalid")
    return {
        "branch_id": branch_id,
        "outcome": "report",
        "package_output_binding": copy.deepcopy(dict(package_binding)),
        "branch_result": copy.deepcopy(dict(branch_result)),
        "report": checked_report,
        "read_session_manifest": session,
        "opened_session_receipt": opened,
        "final_session_receipt": final,
        "mcp_transcript": transcript,
        "mcp_call_receipt": call_receipt,
        "execution": execution,
    }


def _luna_diagnostic_handoff(
    store: AnalysisPackageStore,
    *,
    key: bytes,
    plan: Mapping[str, Any],
    branch: Mapping[str, Any],
    package_binding: Mapping[str, Any],
    branch_result: Mapping[str, Any],
) -> dict[str, Any]:
    branch_id = str(branch["branch_id"])
    checked = _validate_diagnostic_record(branch_result, plan=plan)
    if (
        package_binding.get("kind") != "diagnostic_record"
        or package_binding.get("branch_id") != branch_id
        or package_binding.get("report_sha256") != checked.get("result_sha256")
    ):
        raise NightlySolError("multi_agent_luna_diagnostic_binding_invalid")
    findings = checked.get("findings")
    technical = next(
        (
            row for row in findings
            if isinstance(row, Mapping)
            and row.get("kind") == "technical_diagnostic"
        ),
        None,
    ) if isinstance(findings, list) else None
    result: dict[str, Any] = {
        "branch_id": branch_id,
        "outcome": "diagnostic",
        "package_output_binding": copy.deepcopy(dict(package_binding)),
        "branch_result": checked,
    }
    if not isinstance(technical, Mapping):
        return result
    final_ref = technical.get("failure_session_receipt_ref")
    final_sha = technical.get("failure_session_receipt_sha256")
    if final_ref is None and final_sha is None:
        return result
    if (
        not isinstance(final_sha, str)
        or final_sha != _digest_from_ref(
            final_ref,
            ("study-intake-mcp-investigation-session://sha256/",),
            "multi_agent_failure_session_receipt_invalid",
        )
    ):
        raise NightlySolError("multi_agent_failure_session_receipt_invalid")
    final = _verify_hmac_receipt(
        _artifact_from_ref(
            store,
            digest=final_sha,
            ref=str(final_ref),
            code="multi_agent_failure_session_receipt_invalid",
        ),
        key=key,
        purpose="mcp-investigation-session-receipt-v1",
        code="multi_agent_failure_session_receipt_invalid",
    )
    manifest = _read_session_manifest(
        store, str(final.get("read_session_manifest_sha256"))
    )
    if (
        final.get("phase")
        not in {
            "investigation_failed",
            "investigation_cancelled",
            "investigation_timed_out",
        }
        or final.get("terminal_status") != checked.get("status")
        or final.get("branch_id") != branch_id
        or final.get("subject") != plan.get("subject")
        or final.get("read_session_id") != checked.get("read_session_id")
        or manifest.get("read_session_id") != checked.get("read_session_id")
        or manifest.get("capture_id") != plan.get("capture_id")
        or manifest.get("artifact_ids") != branch.get("allowed_task_artifact_ids")
    ):
        raise NightlySolError("multi_agent_failure_session_binding_invalid")
    result.update({
        "read_session_manifest": manifest,
        "final_session_receipt": final,
    })
    opened_sha = final.get("opened_receipt_sha256")
    if not isinstance(opened_sha, str):
        raise NightlySolError("multi_agent_failure_opened_session_invalid")
    opened_ref = "study-intake-mcp-read-session://sha256/" + opened_sha
    opened = _verify_hmac_receipt(
        _artifact_from_ref(
            store,
            digest=opened_sha,
            ref=opened_ref,
            code="multi_agent_failure_opened_session_invalid",
        ),
        key=key,
        purpose="mcp-read-session-receipt-v1",
        code="multi_agent_failure_opened_session_invalid",
    )
    if (
        opened.get("phase") != "opened"
        or opened.get("read_session_id") != manifest.get("read_session_id")
        or opened.get("read_session_manifest_sha256")
        != manifest.get("manifest_sha256")
    ):
        raise NightlySolError("multi_agent_failure_opened_session_invalid")
    result["opened_session_receipt"] = opened
    pairs = (
        (
            "mcp_transcript_sha256",
            "mcp_transcript_ref",
            "mcp_transcript",
            "multi_agent_failure_transcript_invalid",
        ),
        (
            "mcp_call_receipt_sha256",
            "mcp_call_receipt_ref",
            "mcp_call_receipt",
            "multi_agent_failure_call_receipt_invalid",
        ),
        (
            "raw_output_object_sha256",
            "raw_output_object_ref",
            "raw_output",
            "multi_agent_failure_raw_output_invalid",
        ),
        (
            "stage_execution_receipt_sha256",
            "stage_execution_receipt_ref",
            "stage_execution_receipt",
            "multi_agent_failure_execution_receipt_invalid",
        ),
    )
    for digest_key, ref_key, output_key, code in pairs:
        digest, ref = final.get(digest_key), final.get(ref_key)
        if digest is None and ref is None:
            continue
        if not isinstance(digest, str) or not isinstance(ref, str):
            raise NightlySolError(code)
        artifact = _artifact_from_ref(
            store, digest=digest, ref=ref, code=code
        )
        if output_key == "mcp_call_receipt":
            purpose = {
                "mcp_stage_call_receipt_v1": "mcp-read-session-model-calls-v1",
                "mcp_stage_call_receipt_v2": "mcp-read-session-model-calls-v2",
            }.get(str(artifact.get("schema_version")))
            if purpose is None:
                raise NightlySolError(code)
            artifact = _verify_hmac_receipt(
                artifact, key=key, purpose=purpose, code=code
            )
        result[output_key] = artifact
    return result


def _deep_reopen_analysis_package_v2(
    store: AnalysisPackageStore, package_digest: str
) -> dict[str, Any]:
    try:
        package = reopen_analysis_package_v2(store, package_digest)
        capture = validate_durable_capture(
            _binding_object(
                store,
                package["capture"],
                capture=True,
                code="multi_agent_capture_reopen_invalid",
            )
        )
        plan = _binding_object(
            store,
            package["plan"],
            code="multi_agent_plan_reopen_invalid",
        )
        validate_dual_report_plan(plan)
        initial = _validate_terra_initial(
            _binding_object(
                store,
                package["terra_initial"],
                code="multi_agent_terra_initial_reopen_invalid",
            ),
            capture=capture,
            plan=plan,
        )
    except (AnalysisPackageError, ValueError, TypeError, KeyError) as exc:
        raise NightlySolError("analysis_package_v2_deep_reopen_invalid") from exc
    if (
        capture.get("capture_id") != package.get("capture_id")
        or capture.get("subject") != package.get("subject")
        or capture.get("study_date") != package.get("study_date")
        or capture.get("captured_at") != package.get("captured_at")
        or capture.get("capture_intake_date") != package.get("capture_intake_date")
        or plan.get("capture_id") != capture.get("capture_id")
        or plan.get("subject") != capture.get("subject")
    ):
        raise NightlySolError("analysis_package_v2_capture_binding_invalid")
    output_rows = package.get("luna_outputs")
    branches = plan["branches"]
    if (
        not isinstance(output_rows, list)
        or [row.get("branch_id") for row in output_rows]
        != [row["branch_id"] for row in branches]
    ):
        raise NightlySolError("analysis_package_v2_luna_set_invalid")
    branch_results: list[dict[str, Any]] = []
    reopened_outputs: list[dict[str, Any]] = []
    for row in output_rows:
        if not isinstance(row, Mapping):
            raise NightlySolError("analysis_package_v2_luna_set_invalid")
        result_digest = _sha(
            row.get("branch_result_sha256"),
            "multi_agent_branch_result_reopen_invalid",
        )
        if row.get("branch_result_ref") != (
            "study-intake-read-branch-result://sha256/" + result_digest
        ):
            raise NightlySolError("multi_agent_branch_result_reopen_invalid")
        try:
            branch_result = store.reopen_analysis_object(result_digest)
            output = store.reopen_analysis_object(
                _sha(row.get("sha256"), "multi_agent_luna_output_reopen_invalid")
            )
        except AnalysisPackageError as exc:
            raise NightlySolError("multi_agent_luna_output_reopen_invalid") from exc
        expected_prefix = (
            "study-intake-luna-investigation-report://sha256/"
            if row.get("kind") == "investigation_report"
            else "study-intake-luna-diagnostic-record://sha256/"
        )
        if (
            row.get("ref") != expected_prefix + str(row.get("sha256"))
            or output.get("branch_id") != row.get("branch_id")
            or branch_result.get("branch_id") != row.get("branch_id")
            or output.get("schema_version") != row.get("schema_version")
            or row.get("report_sha256")
            != (
                output.get("report_sha256")
                or output.get("result_sha256")
            )
        ):
            raise NightlySolError("multi_agent_luna_output_binding_invalid")
        branch_results.append(branch_result)
        reopened_outputs.append(output)
    try:
        read_bundle = _validate_read_bundle(
            _binding_object(
                store,
                package["read_bundle"],
                code="multi_agent_read_bundle_reopen_invalid",
            ),
            plan=plan,
            branch_results=branch_results,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise NightlySolError("multi_agent_read_bundle_reopen_invalid") from exc
    key = _authority_key(store)
    luna_handoffs: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for branch, row, branch_result, output in zip(
        branches, output_rows, branch_results, reopened_outputs
    ):
        try:
            if row["kind"] == "investigation_report":
                handoff = _luna_success_handoff(
                    store,
                    key=key,
                    plan=plan,
                    read_bundle=read_bundle,
                    branch=branch,
                    package_binding=row,
                    branch_result=branch_result,
                    report=output,
                )
                reports.append(output)
            else:
                handoff = _luna_diagnostic_handoff(
                    store,
                    key=key,
                    plan=plan,
                    branch=branch,
                    package_binding=row,
                    branch_result=branch_result,
                )
                diagnostics.append(output)
        except (ValueError, TypeError, KeyError) as exc:
            raise NightlySolError("multi_agent_luna_handoff_invalid") from exc
        luna_handoffs.append(handoff)
    terra_input = {
        "subject": plan["subject"],
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "read_bundle_sha256": read_bundle["read_bundle_sha256"],
        "branch_coverage": [
            {
                "branch_id": row["branch_id"],
                "outcome": (
                    "report"
                    if row["kind"] == "investigation_report"
                    else "diagnostic"
                ),
            }
            for row in output_rows
        ],
        "luna_reports": reports,
        "luna_diagnostics": diagnostics,
        "formal_write_count": 0,
    }
    try:
        terra_final = validate_terra_final_report_v2(
            _binding_object(
                store,
                package["terra_final"],
                code="multi_agent_terra_final_reopen_invalid",
            ),
            terra_input=terra_input,
        )
        sol_handoff = _binding_object(
            store,
            package["sol_handoff"],
            code="multi_agent_sol_handoff_reopen_invalid",
        )
        legacy_core = {
            "schema_version": "sol_handoff_envelope_v1",
            "subject": sol_handoff["subject"],
            "capture_id": sol_handoff["capture_id"],
            "read_bundle_sha256": sol_handoff["read_bundle_sha256"],
            "candidate_sha256": sol_handoff.get("candidate_sha256"),
            "review_sha256": sol_handoff.get("review_sha256"),
            "risk_report_sha256": sol_handoff.get("risk_report_sha256"),
            "sol_review_ready": sol_handoff.get("sol_review_ready"),
            "diagnostic_review_ready": sol_handoff.get(
                "diagnostic_review_ready"
            ),
            "quality_clean": sol_handoff.get("quality_clean"),
            "allowed_sol_actions": [
                "adopt", "defer", "modify", "reject", "request_reread"
            ],
            "formal_apply_authorized": False,
            "formal_write_count": 0,
        }
        validate_sol_handoff_v3(
            sol_handoff,
            legacy_handoff={
                **legacy_core,
                "handoff_sha256": sha256_value(legacy_core),
            },
            terra_input=terra_input,
            terra_final_report=terra_final,
        )
        initial_execution_binding = _validate_terra_execution(
            package["terra_initial_execution"],
            subject=str(package["subject"]),
            phase="initial",
        )
        final_execution_binding = _validate_terra_execution(
            package["terra_final_execution"],
            subject=str(package["subject"]),
            phase="final",
        )
    except (AnalysisPackageError, ValueError, TypeError, KeyError) as exc:
        raise NightlySolError("analysis_package_v2_terra_handoff_invalid") from exc
    initial_execution = _execution_chain(
        store,
        binding=initial_execution_binding,
        provider_stage_name=f"{package['subject']}_analysis",
        capture_id=str(package["capture_id"]),
        code="multi_agent_terra_initial_execution_invalid",
    )
    final_execution = _execution_chain(
        store,
        binding=final_execution_binding,
        provider_stage_name=f"{package['subject']}_critical_review",
        capture_id=str(package["capture_id"]),
        code="multi_agent_terra_final_execution_invalid",
    )
    core = {
        "schema_version": V2_HANDOFF_SCHEMA,
        "package_schema_version": PACKAGE_V2_SCHEMA,
        "package_sha256": package_digest,
        "capture_id": capture["capture_id"],
        "subject": capture["subject"],
        "frozen_capture": capture,
        "terra_initial": initial,
        "terra_initial_execution": initial_execution,
        "investigation_plan": plan,
        "luna_investigations": luna_handoffs,
        "investigation_summary": read_bundle,
        "terra_final": terra_final,
        "terra_final_execution": final_execution,
        "sol_handoff": sol_handoff,
        "formal_write_count": 0,
    }
    return {**core, "handoff_sha256": sha256_value(core)}


def freeze_nightly_batch(
    *,
    subject: str,
    capture_intake_date: str,
    packages: list[Mapping[str, Any]],
    skill_name: str,
    skill_source_path: Path,
    declared_version: str | None = None,
    authorization: Mapping[str, Any],
    package_store: AnalysisPackageStore | None = None,
) -> dict[str, Any]:
    if subject not in SUBJECT_LABELS.values():
        raise NightlySolError("nightly_subject_invalid")
    _date(capture_intake_date)
    checked_authorization = validate_command_authorization(
        authorization,
        subject=subject,
        capture_intake_date=capture_intake_date,
    )
    rows: list[dict[str, str]] = []
    package_schemas: set[str] = set()
    for package in packages:
        if (
            package.get("subject") != subject
            or package.get("capture_intake_date") != capture_intake_date
            or package.get("status") != "ready_for_nightly"
            or package.get("formal_write_count") != 0
        ):
            raise NightlySolError("analysis_package_batch_binding_invalid")
        package_schemas.add(str(package.get("schema_version") or ""))
        rows.append(_package_binding(package))
    rows.sort(key=lambda row: row["capture_id"])
    capture_ids = [row["capture_id"] for row in rows]
    if not capture_ids or len(capture_ids) != len(set(capture_ids)):
        raise NightlySolError("nightly_capture_set_invalid")
    if len(package_schemas) != 1 or not package_schemas <= {
        PACKAGE_SCHEMA, PACKAGE_V2_SCHEMA
    }:
        raise NightlySolError("analysis_package_schema_set_invalid")
    skill_path = Path(skill_source_path)
    if skill_path.is_symlink() or not skill_path.is_file():
        raise NightlySolError("nightly_skill_source_missing")
    skill_sha = hashlib.sha256(skill_path.read_bytes()).hexdigest()
    capture_set_sha = sha256_value(capture_ids)
    package_schema = next(iter(package_schemas))
    multi_agent_handoffs: list[dict[str, Any]] = []
    handoff_set_sha: str | None = None
    if package_schema == PACKAGE_V2_SCHEMA:
        if package_store is None:
            raise NightlySolError("analysis_package_v2_store_required")
        multi_agent_handoffs = [
            _deep_reopen_analysis_package_v2(
                package_store, row["package_sha256"]
            )
            for row in rows
        ]
        if [row["capture_id"] for row in multi_agent_handoffs] != capture_ids:
            raise NightlySolError("analysis_package_v2_handoff_set_invalid")
        handoff_set_sha = sha256_value(multi_agent_handoffs)
    batch_id = "NIGHTLY-" + sha256_value(
        {
            "subject": subject,
            "capture_intake_date": capture_intake_date,
            "capture_set_sha256": capture_set_sha,
            "skill_source_sha256": skill_sha,
            "authorization_id": checked_authorization["authorization_id"],
            **(
                {
                    "analysis_package_schema_version": package_schema,
                    "multi_agent_handoff_set_sha256": handoff_set_sha,
                }
                if package_schema == PACKAGE_V2_SCHEMA
                else {}
            ),
        }
    )[:28].upper()
    result = {
        "schema_version": BATCH_SCHEMA,
        "batch_id": batch_id,
        "subject": subject,
        "capture_intake_date": capture_intake_date,
        "capture_ids": capture_ids,
        "capture_set_sha256": capture_set_sha,
        "analysis_packages": rows,
        "skill": {
            "name": skill_name,
            "source_sha256": skill_sha,
            "declared_version": declared_version,
        },
        "authorization": checked_authorization,
        "status": "frozen",
        "formal_write_count": 0,
    }
    if package_schema == PACKAGE_V2_SCHEMA:
        result.update({
            "analysis_package_schema_version": package_schema,
            "multi_agent_handoffs": multi_agent_handoffs,
            "multi_agent_handoff_set_sha256": handoff_set_sha,
        })
    return result


def validate_adapter_result(
    value: Mapping[str, Any],
    *,
    batch: Mapping[str, Any],
    expected_capture_ids: list[str] | None = None,
) -> dict[str, Any]:
    required = {
        "schema_version", "adapter_name", "subject", "batch_id", "status",
        "native_receipt", "native_receipt_sha256", "conflict_id",
        "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise NightlySolError("adapter_result_shape_invalid")
    status = value.get("status")
    if (
        value.get("schema_version") != RESULT_SCHEMA
        or value.get("subject") != batch.get("subject")
        or value.get("batch_id") != batch.get("batch_id")
        or status not in {"complete", "noop", "partial", "awaiting_user", "failed"}
        or not isinstance(value.get("native_receipt"), Mapping)
        or value.get("native_receipt_sha256")
        != sha256_value(value.get("native_receipt"))
        or isinstance(value.get("formal_write_count"), bool)
        or not isinstance(value.get("formal_write_count"), int)
        or int(value["formal_write_count"]) < 0
    ):
        raise NightlySolError("adapter_result_invalid")
    conflict_id = value.get("conflict_id")
    if (status == "awaiting_user") != (
        isinstance(conflict_id, str) and conflict_id.startswith("CONFLICT-")
    ):
        raise NightlySolError("adapter_conflict_binding_invalid")
    native = dict(value["native_receipt"])
    capture_results = native.get("capture_results")
    if not isinstance(capture_results, list) or any(
        not isinstance(row, Mapping)
        or not isinstance(row.get("capture_id"), str)
        or not row.get("capture_id")
        for row in capture_results
    ):
        raise NightlySolError("adapter_capture_results_invalid")
    observed = [str(row["capture_id"]) for row in capture_results]
    if observed != sorted(set(observed)):
        raise NightlySolError("adapter_capture_results_invalid")
    if expected_capture_ids is not None:
        expected = sorted(expected_capture_ids)
        if status == "awaiting_user":
            conflict = native.get("conflict")
            conflicted = (
                conflict.get("capture_id")
                if isinstance(conflict, Mapping)
                else None
            )
            conflict_row = next(
                (
                    row for row in capture_results
                    if isinstance(row, Mapping)
                    and row.get("capture_id") == conflicted
                ),
                None,
            )
            separated = (
                isinstance(conflicted, str)
                and conflicted not in observed
                and sorted([*observed, conflicted]) == expected
            )
            explicit_pending = (
                isinstance(conflicted, str)
                and observed == expected
                and isinstance(conflict_row, Mapping)
                and conflict_row.get("status") in {"needs_user", "awaiting_user"}
            )
            if not (separated or explicit_pending):
                raise NightlySolError("adapter_conflict_capture_set_invalid")
        elif observed != expected:
            raise NightlySolError("adapter_capture_set_invalid")
    execution = _execution_evidence(native)
    if status in {"complete", "noop", "awaiting_user"} and execution[
        "exit_code"
    ] != 0:
        raise NightlySolError("adapter_success_exit_code_invalid")
    if status == "failed" and (
        execution["exit_code"] == 0 or value.get("formal_write_count") != 0
    ):
        raise NightlySolError("adapter_failed_terminal_invalid")
    return copy.deepcopy(dict(value))


def _warning_code(value: Any) -> str:
    text = str(value or "")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", text):
        return text
    return "analysis-warning:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _stage_handoff(row: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "raw_output_sha256", "execution_receipt_sha256",
        "normalization_receipt_sha256", "report_sha256",
    }
    if any(not isinstance(row.get(key), str) for key in required):
        raise NightlySolError("analysis_package_stage_binding_invalid")
    warning_codes = (
        ["normalization_incomplete"]
        if row.get("normalization_status") == "incomplete"
        else []
    )
    return {
        "raw_output_sha256": _sha(
            row.get("raw_output_sha256"), "analysis_stage_raw_output_invalid"
        ),
        "execution_receipt_sha256": _sha(
            row.get("execution_receipt_sha256"),
            "analysis_stage_execution_receipt_invalid",
        ),
        "normalization_receipt_sha256": _sha(
            row.get("normalization_receipt_sha256"),
            "analysis_stage_normalization_receipt_invalid",
        ),
        "report_sha256": _sha(
            row.get("report_sha256"), "analysis_stage_report_invalid"
        ),
        "warning_codes": warning_codes,
    }


def _subject_tasks_v2(
    *, batch: Mapping[str, Any], package_store: AnalysisPackageStore
) -> list[dict[str, Any]]:
    handoffs = batch.get("multi_agent_handoffs")
    if (
        batch.get("analysis_package_schema_version") != PACKAGE_V2_SCHEMA
        or not isinstance(handoffs, list)
        or batch.get("multi_agent_handoff_set_sha256")
        != sha256_value(handoffs)
        or [row.get("capture_id") for row in handoffs]
        != list(batch.get("capture_ids") or [])
    ):
        raise NightlySolError("analysis_package_v2_batch_handoff_invalid")
    packages = package_store.packages_for(
        subject=str(batch["subject"]),
        capture_intake_date_value=str(batch["capture_intake_date"]),
    )
    by_capture = {str(row["capture_id"]): row for row in packages}
    handoff_by_capture = {str(row["capture_id"]): row for row in handoffs}
    tasks: list[dict[str, Any]] = []
    for binding in batch["analysis_packages"]:
        capture_id = str(binding["capture_id"])
        package = by_capture.get(capture_id)
        if (
            package is None
            or package.get("schema_version") != PACKAGE_V2_SCHEMA
            or _package_binding(package) != dict(binding)
        ):
            raise NightlySolError("analysis_package_reopen_mismatch")
        recomputed = _deep_reopen_analysis_package_v2(
            package_store, str(binding["package_sha256"])
        )
        if handoff_by_capture.get(capture_id) != recomputed:
            raise NightlySolError("analysis_package_v2_batch_handoff_invalid")
        initial_execution = package["terra_initial_execution"]
        final_execution = package["terra_final_execution"]
        terra_final = recomputed["terra_final"]
        warning_codes = sorted({
            _warning_code(value)
            for value in [
                *list(terra_final.get("warnings") or []),
                *list(terra_final.get("evidence_gaps") or []),
            ]
        })
        authority_snapshot = sha256_value({
            "authorization": batch["authorization"],
            "skill": batch["skill"],
            "capture_sha256": package["capture"]["sha256"],
            "package_sha256": binding["package_sha256"],
            "multi_agent_handoff_sha256": recomputed["handoff_sha256"],
        })
        tasks.append({
            "capture_id": capture_id,
            "unit_sha256": binding["package_sha256"],
            "input_fingerprint": package["capture"]["sha256"],
            "study_date": package["study_date"],
            "frozen_payload_sha256": package["capture"]["sha256"],
            "authority_snapshot_sha256": authority_snapshot,
            "analysis": {
                "raw_output_sha256": initial_execution["raw_output_sha256"],
                "execution_receipt_sha256": initial_execution[
                    "stage_execution_receipt_sha256"
                ],
                "normalization_receipt_sha256": initial_execution[
                    "normalization_receipt_sha256"
                ],
                "report_sha256": package["terra_initial"]["sha256"],
                "warning_codes": [],
            },
            "critical_review": {
                "raw_output_sha256": final_execution["raw_output_sha256"],
                "execution_receipt_sha256": final_execution[
                    "stage_execution_receipt_sha256"
                ],
                "normalization_receipt_sha256": final_execution[
                    "normalization_receipt_sha256"
                ],
                "report_sha256": package["terra_final"]["sha256"],
                "warning_codes": warning_codes,
            },
            "package_sha256": binding["package_sha256"],
            "warning_codes": warning_codes,
        })
    if [row["capture_id"] for row in tasks] != list(batch["capture_ids"]):
        raise NightlySolError("analysis_package_batch_binding_invalid")
    return tasks


def _subject_tasks(
    *, batch: Mapping[str, Any], package_store: AnalysisPackageStore
) -> list[dict[str, Any]]:
    if batch.get("analysis_package_schema_version") == PACKAGE_V2_SCHEMA:
        return _subject_tasks_v2(batch=batch, package_store=package_store)
    packages = package_store.packages_for(
        subject=str(batch["subject"]),
        capture_intake_date_value=str(batch["capture_intake_date"]),
    )
    by_capture = {str(row["capture_id"]): row for row in packages}
    tasks: list[dict[str, Any]] = []
    for binding in batch["analysis_packages"]:
        capture_id = str(binding["capture_id"])
        package = by_capture.get(capture_id)
        if package is None or _package_binding(package) != dict(binding):
            raise NightlySolError("analysis_package_reopen_mismatch")
        stages = {
            str(row.get("stage")): row
            for row in package.get("stages", [])
            if isinstance(row, Mapping)
        }
        if set(stages) != {"terra_analysis", "luna_analysis", "terra_final"}:
            raise NightlySolError("analysis_package_stage_set_invalid")
        warning_codes = sorted(
            {_warning_code(value) for value in package.get("warnings", [])}
        )
        authority_snapshot = sha256_value(
            {
                "authorization": batch["authorization"],
                "skill": batch["skill"],
                "capture_sha256": package["capture_sha256"],
                "package_sha256": binding["package_sha256"],
            }
        )
        tasks.append(
            {
                "capture_id": capture_id,
                "unit_sha256": binding["package_sha256"],
                "input_fingerprint": package["capture_sha256"],
                "study_date": package["study_date"],
                "frozen_payload_sha256": package["capture_sha256"],
                "authority_snapshot_sha256": authority_snapshot,
                "analysis": _stage_handoff(stages["terra_analysis"]),
                "critical_review": _stage_handoff(stages["terra_final"]),
                "package_sha256": binding["package_sha256"],
                "warning_codes": warning_codes,
            }
        )
    if [row["capture_id"] for row in tasks] != list(batch["capture_ids"]):
        raise NightlySolError("analysis_package_batch_binding_invalid")
    return tasks


def _execution_evidence(native_receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = native_receipt.get("execution_evidence")
    required = {
        "pre_state_sha256", "post_state_sha256", "operations",
        "adapter_run_id", "pid", "transaction_id", "ended_at",
        "stopped_at", "exit_code",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise NightlySolError("adapter_execution_evidence_invalid")
    checked = copy.deepcopy(dict(value))
    _sha(checked.get("pre_state_sha256"), "adapter_pre_state_invalid")
    _sha(checked.get("post_state_sha256"), "adapter_post_state_invalid")
    if (
        not isinstance(checked.get("operations"), list)
        or not isinstance(checked.get("adapter_run_id"), str)
        or not checked["adapter_run_id"]
        or isinstance(checked.get("pid"), bool)
        or not isinstance(checked.get("pid"), int)
        or checked["pid"] < 1
        or not isinstance(checked.get("transaction_id"), str)
        or not checked["transaction_id"]
        or isinstance(checked.get("exit_code"), bool)
        or not isinstance(checked.get("exit_code"), int)
    ):
        raise NightlySolError("adapter_execution_evidence_invalid")
    parsed_times: list[dt.datetime] = []
    for key in ("ended_at", "stopped_at"):
        raw = checked.get(key)
        if not isinstance(raw, str):
            raise NightlySolError("adapter_execution_evidence_invalid")
        try:
            parsed = dt.datetime.fromisoformat(
                raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            )
        except ValueError as exc:
            raise NightlySolError("adapter_execution_evidence_invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise NightlySolError("adapter_execution_evidence_invalid")
        parsed_times.append(parsed)
    if parsed_times[0] > parsed_times[1]:
        raise NightlySolError("adapter_execution_evidence_invalid")
    return checked


def _write_result_file(root: Path, name: str, value: Mapping[str, Any]) -> None:
    path = root / name
    payload = canonical_bytes(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _dispatcher_effect(subject: str) -> dict[str, Any]:
    return {
        "scope": "subject",
        "subject": subject,
        "paused_subjects": [],
        "drained_subjects": [],
        "other_subjects_unchanged": True,
    }


def _adapter_compatible_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the deployed subject adapters' strict V1-compatible input shape."""

    required = {
        "schema_version", "batch_id", "subject", "capture_intake_date",
        "capture_ids", "capture_set_sha256", "analysis_packages", "skill",
        "authorization", "status", "formal_write_count",
    }
    if batch.get("analysis_package_schema_version") != PACKAGE_V2_SCHEMA:
        return copy.deepcopy(dict(batch))
    if not required <= set(batch):
        raise NightlySolError("analysis_package_v2_adapter_projection_invalid")
    return {key: copy.deepcopy(batch[key]) for key in required}


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_value(item) for item in value]
    return copy.deepcopy(value)


def _v2_native_executor(
    *,
    batch: Mapping[str, Any],
    native_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    if batch.get("analysis_package_schema_version") != PACKAGE_V2_SCHEMA:
        return native_executor
    adapter_batch = _adapter_compatible_batch(batch)
    handoffs = copy.deepcopy(list(batch["multi_agent_handoffs"]))
    by_capture = {str(row["capture_id"]): row for row in handoffs}
    batch_sha = sha256_value(batch)

    def execute(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(invocation, Mapping):
            raise NightlySolError("multi_agent_native_invocation_invalid")
        plain_invocation = _plain_value(invocation)
        nested_batch = plain_invocation.get("batch")
        if nested_batch is not None and (
            not isinstance(nested_batch, Mapping)
            or dict(nested_batch) != adapter_batch
        ):
            raise NightlySolError("multi_agent_native_batch_binding_invalid")
        if (
            plain_invocation.get("batch_id") != adapter_batch["batch_id"]
            or plain_invocation.get("subject") != adapter_batch["subject"]
        ):
            raise NightlySolError("multi_agent_native_batch_binding_invalid")
        selected = plain_invocation.get("capture_ids")
        if not isinstance(selected, list) or any(
            not isinstance(item, str) or item not in by_capture
            for item in selected
        ) or selected != sorted(set(selected)):
            raise NightlySolError("multi_agent_native_capture_set_invalid")
        expected_packages = [
            row for row in adapter_batch["analysis_packages"]
            if row["capture_id"] in selected
        ]
        invocation_packages = plain_invocation.get("analysis_packages")
        if (
            invocation_packages is not None
            and invocation_packages != expected_packages
        ):
            raise NightlySolError("multi_agent_native_capture_set_invalid")
        selected_handoffs = [copy.deepcopy(by_capture[item]) for item in selected]
        enriched = plain_invocation
        enriched.update({
            "analysis_package_schema_version": PACKAGE_V2_SCHEMA,
            "verified_multi_agent_handoffs": selected_handoffs,
            "verified_multi_agent_handoff_set_sha256": sha256_value(
                selected_handoffs
            ),
            "verified_nightly_batch_sha256": batch_sha,
        })
        return native_executor(enriched)

    return execute


class NightlySolCoordinator:
    """Subject-thread dispatcher backed only by SubjectSolRuntimeStore."""

    def __init__(
        self,
        *,
        runtime_store: SubjectSolRuntimeStore,
        package_store: AnalysisPackageStore,
    ) -> None:
        self.runtime_store = runtime_store
        self.package_store = package_store
        self.user_intent = IndependentUserIntentPublisher(
            runtime_store.runtime_root
        )
        self.writer_publisher = IsolatedWriterAdapterPublisher(
            runtime_store.runtime_root
        )

    def _existing_result(self, batch: Mapping[str, Any]) -> dict[str, Any] | None:
        global_state = self.runtime_store.read_global()
        row = next(
            (
                item for item in global_state["queue"]
                if item["batch_id"] == batch["batch_id"]
            ),
            None,
        )
        if row is None:
            return None
        status = row["status"]
        continuation: dict[str, Any] | None = None
        continuation_sha: str | None = None
        if status == "safe_paused":
            root = (
                self.runtime_store.receipt_root
                / "sol-conflict-continuations"
                / "sha256"
            )
            for path in sorted(root.glob("*/*.json")) if root.is_dir() else []:
                try:
                    payload = path.read_bytes()
                    value = json.loads(payload.decode("utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                digest = hashlib.sha256(payload).hexdigest()
                if (
                    isinstance(value, Mapping)
                    and digest == path.stem
                    and value.get("batch_id") == batch["batch_id"]
                    and value.get("subject") == batch["subject"]
                ):
                    self.runtime_store._verify_seal(
                        value, purpose="subject-sol-conflict-continuation"
                    )
                    continuation = dict(value)
                    continuation_sha = digest
                    break
        result = {
            "status": (
                "ALREADY_COMMITTED"
                if status == "committed"
                else "PARTIAL_AWAITING_USER"
                if continuation is not None
                else "SAFE_PAUSED_RETRYABLE"
                if status == "safe_paused"
                else "IN_PROGRESS"
            ),
            "batch_id": batch["batch_id"],
            "subject": batch["subject"],
            "queue_status": status,
            "idempotent": True,
            "formal_write_count": 0,
        }
        if continuation is not None:
            result.update(
                {
                    "conflict_id": continuation["conflict_id"],
                    "conflicted_capture_id": continuation[
                        "conflicted_capture_id"
                    ],
                    "continuation_receipt_sha256": continuation_sha,
                    "writer_lease_released": True,
                }
            )
        return result

    def _admit_and_authorize(
        self, batch: Mapping[str, Any], *, authorized_at: str
    ) -> dict[str, Any]:
        tasks = _subject_tasks(batch=batch, package_store=self.package_store)
        authority_fingerprint = sha256_value(
            {
                "authorization": batch["authorization"],
                "skill": batch["skill"],
                "capture_set_sha256": batch["capture_set_sha256"],
            }
        )
        luna_batch = self.runtime_store.admit_nightly_analysis_batch(
            batch_id=str(batch["batch_id"]),
            subject=str(batch["subject"]),
            capture_intake_date=str(batch["capture_intake_date"]),
            capture_high_watermark=str(batch["capture_ids"][-1]),
            scan_snapshot_sha256=sha256_value(batch["analysis_packages"]),
            authority_generation=(
                "nightly-" + str(batch["authorization"]["authorization_id"]).lower()
            ),
            authority_fingerprint=authority_fingerprint,
            tasks=tasks,
            issued_at=authorized_at,
        )
        authorization_sha, _ = self.user_intent.publish_sol_authorization(
            luna_batch=luna_batch,
            sol_batch_id=str(batch["batch_id"]),
            event_id=str(batch["authorization"]["authorization_id"]),
            authorized_at=authorized_at,
            idempotency_key=sha256_value(batch),
        )
        daily_batch = self.runtime_store.build_authorized_batch(
            str(batch["subject"]),
            sol_batch_id=str(batch["batch_id"]),
            authorization_receipt_sha256=authorization_sha,
        )
        self.runtime_store.authorize_batch(str(batch["subject"]), daily_batch)
        return daily_batch

    def _publish_subject_receipts(
        self,
        *,
        batch: Mapping[str, Any],
        daily_batch: Mapping[str, Any],
        claim: Mapping[str, Any],
        adapter_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        subject = str(batch["subject"])
        native = dict(adapter_result["native_receipt"])
        evidence = _execution_evidence(native)
        candidate_ids = list(daily_batch["sol_candidate_task_ids"])
        task_by_capture = {
            row["capture_id"]: row
            for row in daily_batch["subject_luna_batch"]["tasks"]
        }
        decisions = [
            {
                "capture_id": capture_id,
                "unit_sha256": task_by_capture[capture_id]["unit_sha256"],
                "sol_handoff_envelope_sha256": task_by_capture[capture_id][
                    "sol_handoff_envelope_sha256"
                ],
                "decision": "adopted",
                "replacement_proposal_sha256": None,
                "replacement_package_sha256": None,
                "reason": (
                    "hard conflict deferred without a formal operation"
                    if adapter_result["status"] == "awaiting_user"
                    and isinstance(native.get("conflict"), Mapping)
                    and capture_id == native["conflict"].get("capture_id")
                    else
                    "validated current adapter execution"
                    if capture_id
                    in {
                        row["capture_id"] for row in native["capture_results"]
                    }
                    else "previously committed receipt carried forward"
                ),
            }
            for capture_id in candidate_ids
        ]
        effect = _dispatcher_effect(subject)
        with tempfile.TemporaryDirectory(prefix="nightly-sol-receipts-") as folder:
            root = Path(folder)
            review_core = {
                "batch_id": batch["batch_id"],
                "daily_sol_batch_sha256": _document_sha256(daily_batch),
                "subject": subject,
                "fencing_token": claim["fencing_token"],
                "writer": "sol",
                "writer_adapter": SUBJECT_WRITER_ADAPTERS[subject],
                "canonical_evidence_sha256": sha256_value(
                    {
                        "nightly_batch": batch,
                        "native_receipt_sha256": adapter_result[
                            "native_receipt_sha256"
                        ],
                    }
                ),
                "decisions": decisions,
                "status": "approved",
                "dispatcher_effect": effect,
                "formal_write_count": 0,
                "completed_at": evidence["ended_at"],
            }
            _write_result_file(root, "review.json", review_core)
            review_sha, _ = self.writer_publisher.publish_review_from_result_directory(
                root, batch=daily_batch
            )
            self.runtime_store.record_sol_review(subject, review_sha)

            transaction_status = (
                "already_current"
                if adapter_result["status"] == "noop"
                else "committed"
            )
            _write_result_file(
                root,
                "writer-process.json",
                {
                    "batch_id": batch["batch_id"],
                    "subject": subject,
                    "fencing_token": claim["fencing_token"],
                    "adapter_run_id": evidence["adapter_run_id"],
                    "pid": evidence["pid"],
                    "terminal_state": "stopped",
                    "exit_code": evidence["exit_code"],
                    "stopped_at": evidence["stopped_at"],
                    "formal_write_count": 0,
                },
            )
            _write_result_file(
                root,
                "transaction-end.json",
                {
                    "batch_id": batch["batch_id"],
                    "subject": subject,
                    "fencing_token": claim["fencing_token"],
                    "transaction_id": evidence["transaction_id"],
                    "state": transaction_status,
                    "ended_at": evidence["ended_at"],
                    "formal_write_count": 0,
                },
            )
            _write_result_file(
                root,
                "operations.json",
                {
                    "batch_id": batch["batch_id"],
                    "subject": subject,
                    "fencing_token": claim["fencing_token"],
                    "pre_state_sha256": evidence["pre_state_sha256"],
                    "post_state_sha256": evidence["post_state_sha256"],
                    "operations": evidence["operations"],
                    "formal_write_count": adapter_result["formal_write_count"],
                },
            )
            _write_result_file(
                root,
                "result.json",
                {
                    "batch_id": batch["batch_id"],
                    "subject": subject,
                    "fencing_token": claim["fencing_token"],
                    "writer": "sol",
                    "writer_adapter": SUBJECT_WRITER_ADAPTERS[subject],
                    "sol_review_receipt_sha256": review_sha,
                    "status": transaction_status,
                    "dispatcher_effect": effect,
                    "completed_at": evidence["stopped_at"],
                },
            )
            apply_sha, _ = (
                self.writer_publisher.publish_apply_from_execution_result_directory(
                    root, batch=daily_batch
                )
            )

        if adapter_result["status"] == "awaiting_user":
            conflict = native["conflict"]
            completed_ids = sorted(
                row["capture_id"]
                for row in native["capture_results"]
                if row["capture_id"] != conflict["capture_id"]
            )
            paused = self.runtime_store.pause_subject_commit_for_conflict(
                subject,
                apply_sha,
                conflict={
                    "conflict_id": adapter_result["conflict_id"],
                    "batch_id": batch["batch_id"],
                    "subject": subject,
                    "conflicted_capture_id": conflict["capture_id"],
                    "completed_capture_ids": completed_ids,
                    "native_receipt_sha256": adapter_result[
                        "native_receipt_sha256"
                    ],
                    "skill": batch["skill"],
                    "issued_at": evidence["stopped_at"],
                },
            )
            return {
                **paused,
                "review_receipt_sha256": review_sha,
                "apply_receipt_sha256": apply_sha,
                "adapter_result": copy.deepcopy(dict(adapter_result)),
            }
        committed = self.runtime_store.finish_subject_commit(subject, apply_sha)
        return {
            "status": "complete",
            "batch_id": batch["batch_id"],
            "subject": subject,
            "review_receipt_sha256": review_sha,
            "apply_receipt_sha256": apply_sha,
            "commit_receipt_sha256": committed["commit_receipt_sha256"],
            "formal_write_count": adapter_result["formal_write_count"],
            "adapter_result": copy.deepcopy(dict(adapter_result)),
            "writer_lease_released": True,
        }

    def _record_failed_review(
        self,
        *,
        batch: Mapping[str, Any],
        daily_batch: Mapping[str, Any],
        claim: Mapping[str, Any],
        error_code: str,
    ) -> dict[str, Any]:
        """Release an acquired claim through the existing review receipt path."""

        subject = str(batch["subject"])
        task_by_capture = {
            row["capture_id"]: row
            for row in daily_batch["subject_luna_batch"]["tasks"]
        }
        decisions = [
            {
                "capture_id": capture_id,
                "unit_sha256": task_by_capture[capture_id]["unit_sha256"],
                "sol_handoff_envelope_sha256": task_by_capture[capture_id][
                    "sol_handoff_envelope_sha256"
                ],
                "decision": "rejected",
                "replacement_proposal_sha256": None,
                "replacement_package_sha256": None,
                "reason": error_code,
            }
            for capture_id in daily_batch["sol_candidate_task_ids"]
        ]
        completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        with tempfile.TemporaryDirectory(prefix="nightly-sol-failed-review-") as folder:
            root = Path(folder)
            _write_result_file(
                root,
                "review.json",
                {
                    "batch_id": batch["batch_id"],
                    "daily_sol_batch_sha256": _document_sha256(daily_batch),
                    "subject": subject,
                    "fencing_token": claim["fencing_token"],
                    "writer": "sol",
                    "writer_adapter": SUBJECT_WRITER_ADAPTERS[subject],
                    "canonical_evidence_sha256": sha256_value(
                        {"batch_id": batch["batch_id"], "error_code": error_code}
                    ),
                    "decisions": decisions,
                    "status": "failed",
                    "dispatcher_effect": _dispatcher_effect(subject),
                    "formal_write_count": 0,
                    "completed_at": completed_at,
                },
            )
            review_sha, _ = self.writer_publisher.publish_review_from_result_directory(
                root, batch=daily_batch
            )
        recorded = self.runtime_store.record_sol_review(subject, review_sha)
        return {
            "status": "retryable_failed",
            "batch_id": batch["batch_id"],
            "subject": subject,
            "error_code": error_code,
            "review_receipt_sha256": review_sha,
            "writer_lease_released": True,
            "formal_write_count": 0,
            "global": recorded["global"],
            "subject_state": recorded["subject"],
        }

    def _compensate_publication_failure(
        self,
        *,
        batch: Mapping[str, Any],
        daily_batch: Mapping[str, Any],
        claim: Mapping[str, Any],
        error_code: str,
    ) -> None:
        """Release a claim if a post-review publication step fails."""

        global_state = self.runtime_store.read_global()
        active = global_state.get("active_writer")
        if (
            isinstance(active, Mapping)
            and active.get("batch_id") == batch["batch_id"]
            and active.get("subject") == batch["subject"]
            and active.get("fencing_token") == claim["fencing_token"]
        ):
            self._record_failed_review(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=error_code,
            )
            return
        queue_entry = next(
            (
                row for row in global_state["queue"]
                if row["batch_id"] == batch["batch_id"]
            ),
            None,
        )
        if isinstance(queue_entry, Mapping) and queue_entry.get("status") in {
            "safe_paused", "committed"
        }:
            return
        raise NightlySolError("subjectsol_publication_compensation_failed")

    def execute_batch(
        self,
        *,
        batch: Mapping[str, Any],
        adapter: Any,
        native_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        authorized_at: str,
        owner_id: str,
    ) -> dict[str, Any]:
        existing = self._existing_result(batch)
        if existing is not None:
            return existing
        daily_batch = self._admit_and_authorize(
            batch, authorized_at=authorized_at
        )
        claim = self.runtime_store.begin_sol_review(
            str(batch["subject"]), str(batch["batch_id"]), owner_id=owner_id
        )
        try:
            result = adapter.execute(
                _adapter_compatible_batch(batch),
                native_executor=_v2_native_executor(
                    batch=batch, native_executor=native_executor
                ),
            )
            checked = validate_adapter_result(
                result,
                batch=batch,
                expected_capture_ids=list(batch["capture_ids"]),
            )
        except Exception as exc:
            code = str(getattr(exc, "code", None) or type(exc).__name__)
            self._record_failed_review(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=code,
            )
            raise NightlySolError("subject_adapter_execution_failed") from exc
        if checked["status"] in {"failed", "partial"}:
            return self._record_failed_review(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=f"adapter_terminal_{checked['status']}",
            )
        try:
            return self._publish_subject_receipts(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                adapter_result=checked,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", None) or type(exc).__name__)
            self._compensate_publication_failure(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=code,
            )
            raise NightlySolError("subjectsol_publication_failed") from exc

    def resume_batch(
        self,
        *,
        batch: Mapping[str, Any],
        adapter: Any,
        native_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        continuation_receipt_sha256: str,
        conflict_id: str,
        conflicted_capture_id: str,
        user_option: str,
        resolved_at: str,
        owner_id: str,
    ) -> dict[str, Any]:
        # Reopen the complete V2 package again before mutating the resume state.
        # The resumed formal operation must not rely on a handoff verified only
        # during the earlier partial run.
        _subject_tasks(batch=batch, package_store=self.package_store)
        adapter_resolution = {
            "conflict_id": conflict_id,
            "batch_id": batch["batch_id"],
            "subject": batch["subject"],
            "conflicted_capture_id": conflicted_capture_id,
            "user_option": user_option,
            "skill_name": batch["skill"]["name"],
            "skill_source_sha256": batch["skill"]["source_sha256"],
        }
        resumed = self.runtime_store.resume_subject_commit_from_resolution(
            str(batch["subject"]),
            continuation_receipt_sha256=continuation_receipt_sha256,
            resolution={
                "conflict_id": conflict_id,
                "batch_id": batch["batch_id"],
                "subject": batch["subject"],
                "conflicted_capture_id": conflicted_capture_id,
                "user_option": user_option,
                "skill": batch["skill"],
                "resolved_at": resolved_at,
            },
        )
        claim = self.runtime_store.begin_sol_review(
            str(batch["subject"]), str(batch["batch_id"]), owner_id=owner_id
        )
        daily_batch = self.runtime_store._sol_batch_by_digest(
            str(claim["global"]["active_writer"]["daily_sol_batch_sha256"])
        )
        try:
            result = adapter.execute(
                _adapter_compatible_batch(batch),
                native_executor=_v2_native_executor(
                    batch=batch, native_executor=native_executor
                ),
                capture_ids=[conflicted_capture_id],
                resolution=adapter_resolution,
            )
            checked = validate_adapter_result(
                result,
                batch=batch,
                expected_capture_ids=[conflicted_capture_id],
            )
        except Exception as exc:
            code = str(getattr(exc, "code", None) or type(exc).__name__)
            self._record_failed_review(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=code,
            )
            self.runtime_store.restore_conflict_resume_after_failure(
                str(batch["subject"]),
                continuation_receipt_sha256=continuation_receipt_sha256,
            )
            raise NightlySolError("subject_adapter_execution_failed") from exc
        if checked["status"] == "awaiting_user":
            self._record_failed_review(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code="nightly_resolution_did_not_resolve_conflict",
            )
            self.runtime_store.restore_conflict_resume_after_failure(
                str(batch["subject"]),
                continuation_receipt_sha256=continuation_receipt_sha256,
            )
            raise NightlySolError("nightly_resolution_did_not_resolve_conflict")
        if checked["status"] in {"failed", "partial"}:
            failed = self._record_failed_review(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=f"adapter_terminal_{checked['status']}",
            )
            self.runtime_store.restore_conflict_resume_after_failure(
                str(batch["subject"]),
                continuation_receipt_sha256=continuation_receipt_sha256,
            )
            return failed
        try:
            completed = self._publish_subject_receipts(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                adapter_result=checked,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", None) or type(exc).__name__)
            self._compensate_publication_failure(
                batch=batch,
                daily_batch=daily_batch,
                claim=claim,
                error_code=code,
            )
            self.runtime_store.restore_conflict_resume_after_failure(
                str(batch["subject"]),
                continuation_receipt_sha256=continuation_receipt_sha256,
            )
            raise NightlySolError("subjectsol_publication_failed") from exc
        return {
            **completed,
            "resumed_capture_id": conflicted_capture_id,
            "resolution_receipt_sha256": resumed[
                "resolution_receipt_sha256"
            ],
            "resume_scope": "conflicted_capture_only",
            "rerun_analysis_package": False,
        }

    def dispatch_subject_thread_command(
        self,
        *,
        text: str,
        today: dt.date,
        skill_name: str,
        skill_source_path: Path,
        adapter: Any,
        native_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        authorized_at: str,
        owner_id: str,
        declared_version: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_nightly_command(text, today=today)
        batch = freeze_from_store(
            package_store=self.package_store,
            subject=str(parsed["subject"]),
            capture_intake_date=str(parsed["capture_intake_date"]),
            skill_name=skill_name,
            skill_source_path=skill_source_path,
            declared_version=declared_version,
            authorization=parsed["authorization"],
        )
        return self.execute_batch(
            batch=batch,
            adapter=adapter,
            native_executor=native_executor,
            authorized_at=authorized_at,
            owner_id=owner_id,
        )


def freeze_from_store(
    *,
    package_store: AnalysisPackageStore,
    subject: str,
    capture_intake_date: str,
    skill_name: str,
    skill_source_path: Path,
    declared_version: str | None = None,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        packages = package_store.packages_for(
            subject=subject,
            capture_intake_date_value=capture_intake_date,
        )
    except AnalysisPackageError as exc:
        raise NightlySolError(exc.code) from exc
    return freeze_nightly_batch(
        subject=subject,
        capture_intake_date=capture_intake_date,
        packages=packages,
        skill_name=skill_name,
        skill_source_path=skill_source_path,
        declared_version=declared_version,
        authorization=authorization,
        package_store=package_store,
    )


__all__ = [
    "AUTHORIZATION_SCHEMA", "BATCH_SCHEMA", "NightlySolCoordinator",
    "NightlySolError", "build_command_authorization", "freeze_from_store",
    "freeze_nightly_batch", "parse_nightly_command",
    "validate_adapter_result", "validate_command_authorization",
]
