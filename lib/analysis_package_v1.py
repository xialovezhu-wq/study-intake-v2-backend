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
REPORT_SCHEMA = "study-intake-analysis-stage-report-v2"
EXECUTION_RECEIPT_SCHEMA = "study-intake-analysis-stage-execution-receipt-v1"
NORMALIZATION_RECEIPT_SCHEMA = (
    "study-intake-analysis-stage-normalization-receipt-v1"
)
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
        "normalization_status", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisPackageError("analysis_report_shape_invalid")
    if (
        value.get("schema_version") != REPORT_SCHEMA
        or value.get("stage") != stage
        or value.get("subject") != subject
        or value.get("capture_id") != capture_id
        or value.get("normalization_status") not in {"complete", "incomplete"}
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
        or any(not _valid_evidence_ref(ref, subject=subject) for ref in refs)
    ):
        raise AnalysisPackageError("analysis_report_evidence_invalid")
    return copy.deepcopy(dict(value))


def _valid_evidence_ref(value: Any, *, subject: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith(f"mcp-item:{subject}:"):
        return True
    prefixes = (
        "study-intake-durable-capture://sha256/",
        "study-intake-analysis-stage-execution-receipt://sha256/",
        "study-intake-model-stage-raw-output://sha256/",
        "study-intake-direct-model-stage-raw://sha256/",
    )
    return any(
        value.startswith(prefix)
        and SHA256_RE.fullmatch(value[len(prefix):]) is not None
        for prefix in prefixes
    )


def _valid_proposal(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value)
        == {"kind", "summary", "target_hint", "field_hints", "evidence_refs"}
        and isinstance(value.get("kind"), str)
        and value.get("kind")
        and isinstance(value.get("summary"), str)
        and value.get("summary")
        and (
            value.get("target_hint") is None
            or isinstance(value.get("target_hint"), str)
        )
        and isinstance(value.get("field_hints"), list)
        and all(isinstance(item, str) for item in value["field_hints"])
        and isinstance(value.get("evidence_refs"), list)
        and all(isinstance(item, str) for item in value["evidence_refs"])
    )


def _valid_duplicate(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"candidate_id", "reason", "evidence_refs"}
        and isinstance(value.get("candidate_id"), str)
        and value.get("candidate_id")
        and isinstance(value.get("reason"), str)
        and value.get("reason")
        and isinstance(value.get("evidence_refs"), list)
        and all(isinstance(item, str) for item in value["evidence_refs"])
    )


def normalize_stage_report(
    value: Any,
    *,
    subject: str,
    capture_id: str,
    stage: str,
    fallback_evidence_refs: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize fallible model JSON into one strict canonical report.

    Unusable advisory fields become safe empty structures.  No learning fact
    is synthesized; nightly Sol can reopen the durable raw output and the
    execution receipt from the package stage row.
    """

    raw = dict(value) if isinstance(value, Mapping) else {}
    normalization_warnings: list[str] = []
    expected_bindings = {
        "schema_version": REPORT_SCHEMA,
        "stage": stage,
        "subject": subject,
        "capture_id": capture_id,
        "formal_write_count": 0,
    }
    if any(raw.get(key) != expected for key, expected in expected_bindings.items()):
        normalization_warnings.append("analysis_normalization_binding_repaired")

    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 16_000:
        summary = (
            "Normalization incomplete; inspect the durable raw output and "
            "execution receipt references."
        )
        normalization_warnings.append("analysis_normalization_summary_unusable")

    raw_proposals = raw.get("proposals")
    proposals = (
        [copy.deepcopy(dict(item)) for item in raw_proposals if _valid_proposal(item)]
        if isinstance(raw_proposals, list)
        else []
    )
    if not isinstance(raw_proposals, list) or len(proposals) != len(raw_proposals):
        normalization_warnings.append("analysis_normalization_proposals_defaulted")

    raw_duplicates = raw.get("duplicate_candidates")
    duplicates = (
        [copy.deepcopy(dict(item)) for item in raw_duplicates if _valid_duplicate(item)]
        if isinstance(raw_duplicates, list)
        else []
    )
    if not isinstance(raw_duplicates, list) or len(duplicates) != len(raw_duplicates):
        normalization_warnings.append(
            "analysis_normalization_duplicate_candidates_defaulted"
        )

    raw_warnings = raw.get("warnings")
    warnings = (
        [item for item in raw_warnings if isinstance(item, str) and item]
        if isinstance(raw_warnings, list)
        else []
    )
    if not isinstance(raw_warnings, list) or len(warnings) != len(raw_warnings):
        normalization_warnings.append("analysis_normalization_warnings_defaulted")

    raw_refs = raw.get("evidence_refs")
    refs = (
        list(dict.fromkeys(
            item
            for item in raw_refs
            if _valid_evidence_ref(item, subject=subject)
        ))
        if isinstance(raw_refs, list)
        else []
    )
    if not refs:
        refs = list(dict.fromkeys(
            item
            for item in fallback_evidence_refs
            if _valid_evidence_ref(item, subject=subject)
        ))
        normalization_warnings.append("analysis_normalization_evidence_fallback")
    elif not isinstance(raw_refs, list) or len(refs) != len(raw_refs):
        normalization_warnings.append("analysis_normalization_evidence_filtered")
    if not refs:
        raise AnalysisPackageError("analysis_report_evidence_unavailable")

    allowed_keys = {
        *expected_bindings,
        "summary", "proposals", "duplicate_candidates", "warnings",
        "evidence_refs", "normalization_status",
    }
    if set(raw) - allowed_keys:
        normalization_warnings.append("analysis_normalization_unknown_fields_dropped")

    normalization_warnings = list(dict.fromkeys(normalization_warnings))
    warnings = list(dict.fromkeys([*warnings, *normalization_warnings]))
    report = {
        **expected_bindings,
        "summary": summary,
        "proposals": proposals,
        "duplicate_candidates": duplicates,
        "warnings": warnings,
        "evidence_refs": refs,
        "normalization_status": (
            "incomplete" if normalization_warnings else "complete"
        ),
    }
    return (
        validate_stage_report(
            report, subject=subject, capture_id=capture_id, stage=stage
        ),
        normalization_warnings,
    )


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
        self.execution_receipt_root = (
            self.root / "dispatch/analysis-stage-execution-receipts/sha256"
        )
        self.normalization_receipt_root = (
            self.root / "dispatch/analysis-stage-normalization-receipts/sha256"
        )
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

    def publish_execution_receipt(
        self, value: Mapping[str, Any]
    ) -> tuple[str, str]:
        return self._publish(
            self.execution_receipt_root,
            value,
            "study-intake-analysis-stage-execution-receipt",
        )

    def publish_normalization_receipt(
        self, value: Mapping[str, Any]
    ) -> tuple[str, str]:
        return self._publish(
            self.normalization_receipt_root,
            value,
            "study-intake-analysis-stage-normalization-receipt",
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
            runtime = _validate_runtime(execution["runtime"], model=model)
            receipt = execution["receipt"]
            if not isinstance(receipt, Mapping):
                raise AnalysisPackageError("analysis_stage_receipt_invalid")
            receipt_copy = copy.deepcopy(dict(receipt))
            raw_sha = _sha(
                receipt_copy.get("raw_output_object_sha256"),
                "analysis_stage_raw_output_sha256_invalid",
            )
            raw_ref = receipt_copy.get("raw_output_object_ref")
            raw_prefixes = (
                "study-intake-model-stage-raw-output://sha256/",
                "study-intake-direct-model-stage-raw://sha256/",
            )
            if (
                not isinstance(raw_ref, str)
                or not raw_ref
                or not any(raw_ref.startswith(prefix) for prefix in raw_prefixes)
                or not raw_ref.endswith("/" + raw_sha)
                or receipt_copy.get("formal_write_count") != 0
            ):
                raise AnalysisPackageError("analysis_stage_raw_output_binding_invalid")
            execution_receipt = {
                "schema_version": EXECUTION_RECEIPT_SCHEMA,
                "stage": stage,
                "subject": checked["subject"],
                "capture_id": checked["capture_id"],
                "raw_output_sha256": raw_sha,
                "raw_output_ref": raw_ref,
                "executor_receipt": receipt_copy,
                "executor_receipt_sha256": sha256_value(receipt_copy),
                "formal_write_count": 0,
            }
            execution_sha, execution_ref = self.store.publish_execution_receipt(
                execution_receipt
            )
            report, normalization_warnings = normalize_stage_report(
                execution["report"],
                subject=checked["subject"],
                capture_id=checked["capture_id"],
                stage=stage,
                fallback_evidence_refs=[capture_ref, execution_ref, raw_ref],
            )
            report_sha, report_ref = self.store.publish_report(report)
            normalization_receipt = {
                "schema_version": NORMALIZATION_RECEIPT_SCHEMA,
                "stage": stage,
                "subject": checked["subject"],
                "capture_id": checked["capture_id"],
                "execution_receipt_sha256": execution_sha,
                "execution_receipt_ref": execution_ref,
                "raw_output_sha256": raw_sha,
                "raw_output_ref": raw_ref,
                "report_sha256": report_sha,
                "report_ref": report_ref,
                "normalization_status": report["normalization_status"],
                "warning_codes": normalization_warnings,
                "formal_write_count": 0,
            }
            normalization_sha, normalization_ref = (
                self.store.publish_normalization_receipt(normalization_receipt)
            )
            stage_rows.append(
                {
                    "stage": stage,
                    "requested_model": model,
                    "requested_reasoning_effort": "max",
                    "read_only": True,
                    "report_sha256": report_sha,
                    "report_ref": report_ref,
                    "raw_output_sha256": raw_sha,
                    "raw_output_ref": raw_ref,
                    "execution_receipt_sha256": execution_sha,
                    "execution_receipt_ref": execution_ref,
                    "normalization_receipt_sha256": normalization_sha,
                    "normalization_receipt_ref": normalization_ref,
                    "normalization_status": report["normalization_status"],
                    "runtime": runtime,
                    "receipt": receipt_copy,
                    "receipt_sha256": sha256_value(receipt_copy),
                    "formal_write_count": 0,
                }
            )
            prior_reports.append(
                {
                    "stage": stage,
                    "report_sha256": report_sha,
                    "report_ref": report_ref,
                    "report": copy.deepcopy(report),
                }
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
    "build_durable_capture", "capture_intake_date", "normalize_stage_report",
    "sha256_value",
    "validate_durable_capture", "validate_stage_report",
]
