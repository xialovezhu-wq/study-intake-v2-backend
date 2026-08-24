"""Neutral content-addressed storage for analysis captures and packages.

The store owns immutable JSON objects and package indexes. It contains no
model-stage sequencing and cannot execute any analysis pipeline.
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
from typing import Any, Mapping
from zoneinfo import ZoneInfo


CAPTURE_SCHEMA = "study-intake-durable-capture-v1"
CURRENT_PACKAGE_SCHEMA = "study-intake-analysis-package-v2"
SUBJECTS = ("math", "cs408", "english")
SOURCE_KINDS = ("canonical", "synthetic")
SHANGHAI = ZoneInfo("Asia/Shanghai")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def validate_sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AnalysisPackageError(code)
    return value


def _safe_id(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 180
        or not value.isascii()
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise AnalysisPackageError(code)
    return value


def validate_date(value: Any, code: str) -> str:
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
        "study_date": validate_date(study_date, "study_date_invalid"),
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
        "schema_version", "capture_id", "subject", "study_date",
        "captured_at", "capture_intake_date", "source_kind", "durable",
        "payload", "payload_sha256", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisPackageError("capture_shape_invalid")
    if value.get("schema_version") != CAPTURE_SCHEMA:
        raise AnalysisPackageError("capture_schema_invalid")
    _safe_id(value.get("capture_id"), "capture_id_invalid")
    if value.get("subject") not in SUBJECTS:
        raise AnalysisPackageError("capture_subject_invalid")
    validate_date(value.get("study_date"), "study_date_invalid")
    if value.get("capture_intake_date") != capture_intake_date(value.get("captured_at")):
        raise AnalysisPackageError("capture_intake_date_mismatch")
    if value.get("source_kind") not in SOURCE_KINDS or value.get("durable") is not True:
        raise AnalysisPackageError("capture_durable_gate_failed")
    if value.get("formal_write_count") != 0:
        raise AnalysisPackageError("capture_formal_write_nonzero")
    if validate_sha256(
        value.get("payload_sha256"), "capture_payload_sha256_invalid"
    ) != sha256_value(value.get("payload")):
        raise AnalysisPackageError("capture_payload_sha256_invalid")
    return copy.deepcopy(dict(value))


class AnalysisPackageStore:
    """Immutable CAS and index access with no execution behavior."""

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
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != payload:
                    raise AnalysisPackageError("analysis_object_no_clobber_conflict")
            os.chmod(path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _publish(
        self, root: Path, value: Mapping[str, Any], prefix: str
    ) -> tuple[str, str]:
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

    def publish_analysis_object(
        self, value: Mapping[str, Any], *, ref_prefix: str
    ) -> tuple[str, str]:
        if not isinstance(ref_prefix, str) or not ref_prefix.startswith("study-intake-"):
            raise AnalysisPackageError("analysis_object_ref_prefix_invalid")
        return self._publish(self.report_root, value, ref_prefix)

    def publish_package(self, value: Mapping[str, Any]) -> tuple[str, str]:
        """Publish only the current schema; historical V1 is read-only."""

        if value.get("schema_version") != CURRENT_PACKAGE_SCHEMA:
            raise AnalysisPackageError("analysis_package_publish_schema_retired")
        digest, ref = self._publish(
            self.package_root, value, "study-intake-analysis-package"
        )
        pointer = {
            "schema_version": "study-intake-analysis-package-pointer-v2",
            "subject": value["subject"],
            "capture_intake_date": value["capture_intake_date"],
            "capture_id": value["capture_id"],
            "package_schema_version": value["schema_version"],
            "package_sha256": digest,
            "package_ref": ref,
            "formal_write_count": 0,
        }
        pointer_path = (
            self.index_root / str(value["subject"])
            / str(value["capture_intake_date"]) / f"{value['capture_id']}.json"
        )
        self._write_no_clobber(pointer_path, canonical_bytes(pointer))
        return digest, ref

    @staticmethod
    def _reopen(
        root: Path, digest: str, *, sha_code: str, reopen_code: str
    ) -> dict[str, Any]:
        digest = validate_sha256(digest, sha_code)
        path = root / digest[:2] / f"{digest}.json"
        try:
            payload = path.read_bytes()
            value = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AnalysisPackageError(reopen_code) from exc
        if (
            path.is_symlink()
            or hashlib.sha256(payload).hexdigest() != digest
            or not isinstance(value, dict)
        ):
            raise AnalysisPackageError(reopen_code)
        return value

    def reopen_analysis_object(self, digest: str) -> dict[str, Any]:
        return self._reopen(
            self.report_root,
            digest,
            sha_code="analysis_object_sha256_invalid",
            reopen_code="analysis_object_reopen_invalid",
        )

    def reopen_capture(self, digest: str) -> dict[str, Any]:
        return self._reopen(
            self.capture_root,
            digest,
            sha_code="capture_sha256_invalid",
            reopen_code="capture_reopen_invalid",
        )

    def reopen_execution_receipt(self, digest: str) -> dict[str, Any]:
        return self._reopen(
            self.execution_receipt_root,
            digest,
            sha_code="analysis_execution_receipt_sha256_invalid",
            reopen_code="analysis_execution_receipt_reopen_invalid",
        )

    def reopen_normalization_receipt(self, digest: str) -> dict[str, Any]:
        return self._reopen(
            self.normalization_receipt_root,
            digest,
            sha_code="analysis_normalization_receipt_sha256_invalid",
            reopen_code="analysis_normalization_receipt_reopen_invalid",
        )

    def reopen_package(self, digest: str) -> dict[str, Any]:
        return self._reopen(
            self.package_root,
            digest,
            sha_code="package_sha256_invalid",
            reopen_code="package_reopen_invalid",
        )

    def packages_for(
        self, *, subject: str, capture_intake_date_value: str
    ) -> list[dict[str, Any]]:
        if subject not in SUBJECTS:
            raise AnalysisPackageError("package_subject_invalid")
        validate_date(capture_intake_date_value, "capture_intake_date_invalid")
        root = self.index_root / subject / capture_intake_date_value
        rows: list[dict[str, Any]] = []
        for pointer_path in sorted(root.glob("*.json")) if root.is_dir() else []:
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                digest = validate_sha256(
                    pointer.get("package_sha256"), "package_pointer_invalid"
                )
                package = self.reopen_package(digest)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise AnalysisPackageError("package_pointer_invalid") from exc
            if (
                pointer_path.is_symlink()
                or pointer.get("subject") != subject
                or pointer.get("capture_intake_date") != capture_intake_date_value
                or pointer.get("capture_id") != package.get("capture_id")
            ):
                raise AnalysisPackageError("package_pointer_invalid")
            rows.append(package)
        return rows


__all__ = [
    "AnalysisPackageError", "AnalysisPackageStore", "CAPTURE_SCHEMA",
    "build_durable_capture", "canonical_bytes", "capture_intake_date",
    "sha256_value", "validate_durable_capture", "validate_sha256",
]
