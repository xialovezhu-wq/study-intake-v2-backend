"""Study Intake V2 daytime Analysis Package contract.

This is deliberately separate from the legacy consumer-stage-chain-v1 Sol
write contract.  It stops after Terra -> Luna -> Terra final and persists a
proposal-only AnalysisPackageV1 for an explicitly authorized nightly Sol run.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


CAPTURE_SCHEMA = "study-intake-durable-capture-v1"
REPORT_SCHEMA = "study-intake-analysis-stage-report-v1"
PACKAGE_SCHEMA = "study-intake-analysis-package-v1"
SUBJECTS = ("math", "cs408", "english")
SOURCE_KINDS = ("canonical", "synthetic")
SHANGHAI = ZoneInfo("Asia/Shanghai")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STAGES = (
    ("terra_analysis", "gpt-5.6-terra"),
    ("luna_analysis", "gpt-5.6-luna"),
    ("terra_final", "gpt-5.6-terra"),
)


class AnalysisPackageError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AnalysisPackageError(code)
    return value


def _safe_id(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 180
        or not value.isascii()
        or any(not (char.isalnum() or char in "._:-") for char in value)
    ):
        raise AnalysisPackageError(code)
    return value


def _date(value: Any, code: str) -> str:
    text = str(value or "")
    try:
        if dt.date.fromisoformat(text).isoformat() != text:
            raise ValueError
    except ValueError as exc:
        raise AnalysisPackageError(code) from exc
    return text


def parse_captured_at(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise AnalysisPackageError("captured_at_invalid")
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise AnalysisPackageError("captured_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnalysisPackageError("captured_at_timezone_missing")
    return parsed


def capture_intake_date(captured_at: Any) -> str:
    return parse_captured_at(captured_at).astimezone(SHANGHAI).date().isoformat()


def build_durable_capture(
    *,
    capture_id: str,
    subject: str,
    study_date: str,
    captured_at: str,
    payload: Any,
    source_kind: str,
) -> dict[str, Any]:
    capture_id = _safe_id(capture_id, "capture_id_invalid")
    if subject not in SUBJECTS:
        raise AnalysisPackageError("capture_subject_invalid")
    if source_kind not in SOURCE_KINDS:
        raise AnalysisPackageError("capture_source_kind_invalid")
    value = {
        "schema_version": CAPTURE_SCHEMA,
        "capture_id": capture_id,
        "subject": subject,
        "study_date": _date(study_date, "study_date_invalid"),
        "captured_at": captured_at,
        "capture_intake_date": capture_intake_date(captured_at),
        "source_kind": source_kind,
        "durable": True,
        "payload": copy.deepcopy(payload),
        "payload_sha256": sha256_value(payload),
        "formal_write_count": 0,
    }
    return validate_durable_capture(value)


def validate_durable_capture(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "capture_id", "subject", "study_date", "captured_at",
        "capture_intake_date", "source_kind", "durable", "payload",
        "payload_sha256", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisPackageError("capture_shape_invalid")
    if value.get("schema_version") != CAPTURE_SCHEMA:
        raise AnalysisPackageError("capture_schema_invalid")
    _safe_id(value.get("capture_id"), "capture_id_invalid")
    if value.get("subject") not in SUBJECTS:
        raise AnalysisPackageError("capture_subject_invalid")
    _date(value.get("study_date"), "study_date_invalid")
    if value.get("capture_intake_date") != capture_intake_date(value.get("captured_at")):
        raise AnalysisPackageError("capture_intake_date_mismatch")
    if value.get("source_kind") not in SOURCE_KINDS or value.get("durable") is not True:
        raise AnalysisPackageError("capture_durable_gate_failed")
    if value.get("formal_write_count") != 0:
        raise AnalysisPackageError("capture_formal_write_nonzero")
    if _sha(value.get("payload_sha256"), "capture_payload_sha256_invalid") != sha256_value(value.get("payload")):
        raise AnalysisPackageError("capture_payload_sha256_invalid")
    return copy.deepcopy(dict(value))


def validate_stage_report(
    value: Mapping[str, Any], *, subject: str, capture_id: str, stage: str
) -> dict[str, Any]:
    required = {
        "schema_version", "stage", "subject", "capture_id", "summary",
        "proposals", "duplicate_candidates", "warnings", "evidence_refs",
        "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisPackageError("analysis_report_shape_invalid")
    if (
        value.get("schema_version") != REPORT_SCHEMA
        or value.get("stage") != stage
        or value.get("subject") != subject
        or value.get("capture_id") != capture_id
        or value.get("formal_write_count") != 0
    ):
        raise AnalysisPackageError("analysis_report_binding_invalid")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 16_000:
        raise AnalysisPackageError("analysis_report_summary_invalid")
    proposals = value.get("proposals")
    duplicates = value.get("duplicate_candidates")
    warnings = value.get("warnings")
    if (
        not isinstance(proposals, list)
        or not isinstance(duplicates, list)
        or not isinstance(warnings, list)
        or any(not isinstance(item, str) or not item for item in warnings)
    ):
        raise AnalysisPackageError("analysis_report_shape_invalid")
    for proposal in proposals:
        if (
            not isinstance(proposal, Mapping)
            or set(proposal) != {
                "kind", "summary", "target_hint", "field_hints", "evidence_refs"
            }
            or not isinstance(proposal.get("kind"), str)
            or not proposal.get("kind")
            or not isinstance(proposal.get("summary"), str)
            or not proposal.get("summary")
            or proposal.get("target_hint") is not None
            and not isinstance(proposal.get("target_hint"), str)
            or not isinstance(proposal.get("field_hints"), list)
            or not isinstance(proposal.get("evidence_refs"), list)
        ):
            raise AnalysisPackageError("analysis_report_proposal_invalid")
    for duplicate in duplicates:
        if (
            not isinstance(duplicate, Mapping)
            or set(duplicate) != {"candidate_id", "reason", "evidence_refs"}
            or not isinstance(duplicate.get("candidate_id"), str)
            or not duplicate.get("candidate_id")
            or not isinstance(duplicate.get("reason"), str)
            or not duplicate.get("reason")
            or not isinstance(duplicate.get("evidence_refs"), list)
        ):
            raise AnalysisPackageError("analysis_report_duplicate_invalid")
    refs = value.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or not refs
        or len(refs) != len(set(refs))
        or any(
            not isinstance(ref, str)
            or not ref.startswith(f"mcp-item:{subject}:")
            for ref in refs
        )
    ):
        raise AnalysisPackageError("analysis_report_evidence_invalid")
    return copy.deepcopy(dict(value))


def _validate_runtime(value: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    required = {
        "requested_model", "requested_reasoning_effort", "runtime_model",
        "runtime_reasoning_effort", "runtime_metadata_provenance", "duration_ms",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisPackageError("analysis_runtime_shape_invalid")
    runtime_model = value.get("runtime_model")
    runtime_effort = value.get("runtime_reasoning_effort")
    if (
        value.get("requested_model") != model
        or value.get("requested_reasoning_effort") != "max"
        or runtime_model not in {None, model}
        or runtime_effort not in {None, "max"}
        or isinstance(value.get("duration_ms"), bool)
        or not isinstance(value.get("duration_ms"), int)
        or int(value["duration_ms"]) < 0
        or not isinstance(value.get("runtime_metadata_provenance"), str)
    ):
        raise AnalysisPackageError("analysis_runtime_identity_invalid")
    return copy.deepcopy(dict(value))


class AnalysisPackageStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.capture_root = self.root / "dispatch/analysis-captures/sha256"
        self.report_root = self.root / "dispatch/analysis-stage-reports/sha256"
        self.package_root = self.root / "dispatch/analysis-packages/sha256"
        self.index_root = self.root / "dispatch/analysis-package-index"

    @staticmethod
    def _write_no_clobber(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != payload:
                    raise AnalysisPackageError("analysis_object_no_clobber_conflict")
            os.chmod(path, 0o600)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _publish(self, root: Path, value: Mapping[str, Any], prefix: str) -> tuple[str, str]:
        payload = canonical_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        path = root / digest[:2] / f"{digest}.json"
        self._write_no_clobber(path, payload)
        return digest, f"{prefix}://sha256/{digest}"

    def publish_capture(self, value: Mapping[str, Any]) -> tuple[str, str]:
        return self._publish(
            self.capture_root,
            validate_durable_capture(value),
            "study-intake-durable-capture",
        )

    def publish_report(self, value: Mapping[str, Any]) -> tuple[str, str]:
        return self._publish(
            self.report_root, value, "study-intake-analysis-stage-report"
        )

    def publish_package(self, value: Mapping[str, Any]) -> tuple[str, str]:
        digest, ref = self._publish(
            self.package_root, value, "study-intake-analysis-package"
        )
        pointer = {
            "schema_version": "study-intake-analysis-package-pointer-v1",
            "subject": value["subject"],
            "capture_intake_date": value["capture_intake_date"],
            "capture_id": value["capture_id"],
            "package_sha256": digest,
            "package_ref": ref,
            "formal_write_count": 0,
        }
        pointer_path = (
            self.index_root / str(value["subject"])
            / str(value["capture_intake_date"])
            / f"{value['capture_id']}.json"
        )
        self._write_no_clobber(pointer_path, canonical_bytes(pointer))
        return digest, ref

    def packages_for(self, *, subject: str, capture_intake_date_value: str) -> list[dict[str, Any]]:
        if subject not in SUBJECTS:
            raise AnalysisPackageError("package_subject_invalid")
        _date(capture_intake_date_value, "capture_intake_date_invalid")
        root = self.index_root / subject / capture_intake_date_value
        rows: list[dict[str, Any]] = []
        for pointer_path in sorted(root.glob("*.json")) if root.is_dir() else []:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            digest = _sha(pointer.get("package_sha256"), "package_pointer_invalid")
            package_path = self.package_root / digest[:2] / f"{digest}.json"
            if package_path.is_symlink() or hashlib.sha256(package_path.read_bytes()).hexdigest() != digest:
                raise AnalysisPackageError("package_pointer_invalid")
            rows.append(json.loads(package_path.read_text(encoding="utf-8")))
        return rows


StageExecutor = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]


class AnalysisPackageDriver:
    def __init__(self, store: AnalysisPackageStore, stage_executor: StageExecutor) -> None:
        self.store = store
        self.stage_executor = stage_executor

    def run(self, capture: Mapping[str, Any]) -> dict[str, Any]:
        checked = validate_durable_capture(capture)
        capture_sha, capture_ref = self.store.publish_capture(checked)
        prior_reports: list[dict[str, str]] = []
        stage_rows: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        for stage, model in STAGES:
            stage_input = {
                "capture_ref": capture_ref,
                "capture_sha256": capture_sha,
                "subject": checked["subject"],
                "capture_id": checked["capture_id"],
                "study_date": checked["study_date"],
                "captured_at": checked["captured_at"],
                "capture_intake_date": checked["capture_intake_date"],
                "prior_reports": copy.deepcopy(prior_reports),
            }
            execution = self.stage_executor(stage, model, stage_input)
            if not isinstance(execution, Mapping) or set(execution) != {
                "report", "runtime", "receipt"
            }:
                raise AnalysisPackageError("analysis_stage_execution_invalid")
            report = validate_stage_report(
                execution["report"],
                subject=checked["subject"],
                capture_id=checked["capture_id"],
                stage=stage,
            )
            runtime = _validate_runtime(execution["runtime"], model=model)
            receipt = execution["receipt"]
            if not isinstance(receipt, Mapping):
                raise AnalysisPackageError("analysis_stage_receipt_invalid")
            report_sha, report_ref = self.store.publish_report(report)
            receipt_copy = copy.deepcopy(dict(receipt))
            stage_rows.append(
                {
                    "stage": stage,
                    "requested_model": model,
                    "requested_reasoning_effort": "max",
                    "read_only": True,
                    "report_sha256": report_sha,
                    "report_ref": report_ref,
                    "runtime": runtime,
                    "receipt": receipt_copy,
                    "receipt_sha256": sha256_value(receipt_copy),
                    "formal_write_count": 0,
                }
            )
            prior_reports.append(
                {"stage": stage, "report_sha256": report_sha, "report_ref": report_ref}
            )
            all_warnings.extend(copy.deepcopy(report["warnings"]))
        core = {
            "schema_version": PACKAGE_SCHEMA,
            "package_id": "ANPKG-" + sha256_value(
                {
                    "capture_sha256": capture_sha,
                    "stage_report_sha256s": [row["report_sha256"] for row in stage_rows],
                }
            )[:24].upper(),
            "capture_id": checked["capture_id"],
            "subject": checked["subject"],
            "study_date": checked["study_date"],
            "captured_at": checked["captured_at"],
            "capture_intake_date": checked["capture_intake_date"],
            "capture_sha256": capture_sha,
            "capture_ref": capture_ref,
            "stage_order": [row["stage"] for row in stage_rows],
            "stages": stage_rows,
            "warnings": all_warnings,
            "status": "ready_for_nightly",
            "formal_write_count": 0,
        }
        package_sha, package_ref = self.store.publish_package(core)
        return {**core, "package_sha256": package_sha, "package_ref": package_ref}


__all__ = [
    "AnalysisPackageDriver", "AnalysisPackageError", "AnalysisPackageStore",
    "CAPTURE_SCHEMA", "PACKAGE_SCHEMA", "REPORT_SCHEMA", "STAGES",
    "build_durable_capture", "capture_intake_date", "sha256_value",
    "validate_durable_capture", "validate_stage_report",
]
