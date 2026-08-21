"""Exact-command nightly batching and thin Subject Sol handoff for V2."""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from analysis_package_v1 import (
    AnalysisPackageError,
    AnalysisPackageStore,
    PACKAGE_SCHEMA,
    canonical_bytes,
    sha256_value,
)


BATCH_SCHEMA = "study-intake-nightly-sol-batch-v1"
RESULT_SCHEMA = "study-intake-nightly-sol-adapter-result-v1"
SUBJECT_LABELS = {"数学": "math", "408": "cs408", "英语": "english"}
ABSOLUTE_COMMAND = re.compile(
    r"^开始 (\d{4}-\d{2}-\d{2}) (数学|408|英语)正式入库$"
)
TODAY_COMMAND = re.compile(r"^开始今天的(数学|408|英语)正式入库$")


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


def parse_nightly_command(text: str, *, today: dt.date) -> dict[str, str]:
    if not isinstance(text, str) or text != text.strip():
        raise NightlySolError("nightly_command_not_exact")
    absolute = ABSOLUTE_COMMAND.fullmatch(text)
    if absolute is not None:
        return {
            "subject": SUBJECT_LABELS[absolute.group(2)],
            "capture_intake_date": _date(absolute.group(1)),
            "normalized_command": text,
        }
    relative = TODAY_COMMAND.fullmatch(text)
    if relative is not None:
        date_value = today.isoformat()
        label = relative.group(1)
        return {
            "subject": SUBJECT_LABELS[label],
            "capture_intake_date": date_value,
            "normalized_command": f"开始 {date_value} {label}正式入库",
        }
    raise NightlySolError("nightly_command_not_exact")


def _package_binding(package: Mapping[str, Any]) -> dict[str, str]:
    if package.get("schema_version") != PACKAGE_SCHEMA:
        raise NightlySolError("analysis_package_invalid")
    digest = hashlib.sha256(canonical_bytes(package)).hexdigest()
    return {
        "capture_id": str(package["capture_id"]),
        "package_sha256": digest,
        "package_ref": "study-intake-analysis-package://sha256/" + digest,
    }


def freeze_nightly_batch(
    *,
    subject: str,
    capture_intake_date: str,
    packages: list[Mapping[str, Any]],
    skill_name: str,
    skill_source_path: Path,
    declared_version: str | None = None,
) -> dict[str, Any]:
    if subject not in SUBJECT_LABELS.values():
        raise NightlySolError("nightly_subject_invalid")
    _date(capture_intake_date)
    rows: list[dict[str, str]] = []
    for package in packages:
        if (
            package.get("subject") != subject
            or package.get("capture_intake_date") != capture_intake_date
            or package.get("status") != "ready_for_nightly"
            or package.get("formal_write_count") != 0
        ):
            raise NightlySolError("analysis_package_batch_binding_invalid")
        rows.append(_package_binding(package))
    rows.sort(key=lambda row: row["capture_id"])
    capture_ids = [row["capture_id"] for row in rows]
    if not capture_ids or len(capture_ids) != len(set(capture_ids)):
        raise NightlySolError("nightly_capture_set_invalid")
    skill_path = Path(skill_source_path)
    if skill_path.is_symlink() or not skill_path.is_file():
        raise NightlySolError("nightly_skill_source_missing")
    skill_sha = hashlib.sha256(skill_path.read_bytes()).hexdigest()
    capture_set_sha = sha256_value(capture_ids)
    batch_id = "NIGHTLY-" + sha256_value(
        {
            "subject": subject,
            "capture_intake_date": capture_intake_date,
            "capture_set_sha256": capture_set_sha,
            "skill_source_sha256": skill_sha,
        }
    )[:28].upper()
    return {
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
        "status": "frozen",
        "formal_write_count": 0,
    }


def validate_adapter_result(
    value: Mapping[str, Any], *, batch: Mapping[str, Any]
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
    return copy.deepcopy(dict(value))


class NightlySolStore:
    def __init__(self, runtime_root: Path) -> None:
        self.root = Path(runtime_root).resolve()
        self.batch_root = self.root / "dispatch/nightly-sol-batches/sha256"
        self.result_root = self.root / "dispatch/nightly-sol-results/sha256"
        self.conflict_root = self.root / "dispatch/state/nightly-sol-conflicts"
        self.resolution_root = self.root / "dispatch/nightly-sol-resolutions/sha256"
        self.global_lock_path = self.root / "dispatch/state/global-sol-writer.lock"

    @staticmethod
    def _write_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
        payload = canonical_bytes(value)
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
                    raise NightlySolError("nightly_pointer_no_clobber_conflict")
            os.chmod(path, 0o600)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _publish(root: Path, value: Mapping[str, Any]) -> tuple[str, Path]:
        payload = canonical_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        path = root / digest[:2] / f"{digest}.json"
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
                    raise NightlySolError("nightly_object_no_clobber_conflict")
            os.chmod(path, 0o600)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
        return digest, path

    def publish_batch(self, batch: Mapping[str, Any]) -> tuple[str, Path]:
        return self._publish(self.batch_root, batch)

    def publish_result(self, result: Mapping[str, Any]) -> tuple[str, Path]:
        digest, path = self._publish(self.result_root, result)
        if result.get("status") == "awaiting_user":
            conflict_id = str(result["conflict_id"])
            conflict_path = self.conflict_root / f"{conflict_id}.json"
            self._publish(self.conflict_root / conflict_id[:10], {
                "schema_version": "study-intake-nightly-sol-conflict-v1",
                "conflict_id": conflict_id,
                "subject": result["subject"],
                "batch_id": result["batch_id"],
                "adapter_result_sha256": digest,
                "status": "awaiting_user",
                "formal_write_count": 0,
            })
            # A stable lookup pointer contains no protected content.
            pointer = {
                "conflict_id": conflict_id,
                "adapter_result_sha256": digest,
            }
            self._write_no_clobber(conflict_path, pointer)
        return digest, path

    def publish_resolution(
        self, *, conflict_id: str, resolution: Mapping[str, Any]
    ) -> tuple[str, Path]:
        pointer_path = self.conflict_root / f"{conflict_id}.json"
        if not pointer_path.is_file():
            raise NightlySolError("nightly_conflict_not_found")
        value = {
            "schema_version": "study-intake-nightly-sol-resolution-v1",
            "conflict_id": conflict_id,
            "resolution": copy.deepcopy(dict(resolution)),
            "resume_scope": "conflicted_capture_only",
            "rerun_analysis_package": False,
            "formal_write_count": 0,
        }
        return self._publish(self.resolution_root, value)


class GlobalWriterLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "GlobalWriterLease":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = self.path.open("a+b")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args: object) -> None:
        assert self.handle is not None
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


class NightlySolCoordinator:
    def __init__(self, store: NightlySolStore) -> None:
        self.store = store

    def record_adapter_result(
        self, *, batch: Mapping[str, Any], result: Mapping[str, Any]
    ) -> dict[str, Any]:
        checked = validate_adapter_result(result, batch=batch)
        with GlobalWriterLease(self.store.global_lock_path):
            digest, path = self.store.publish_result(checked)
        return {
            "status": checked["status"],
            "result_sha256": digest,
            "result_path": str(path),
            "conflict_id": checked["conflict_id"],
            "writer_lease_released": True,
        }

    def record_resolution(
        self, *, conflict_id: str, resolution: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(resolution, Mapping) or not resolution:
            raise NightlySolError("nightly_resolution_invalid")
        with GlobalWriterLease(self.store.global_lock_path):
            digest, path = self.store.publish_resolution(
                conflict_id=conflict_id, resolution=resolution
            )
        return {
            "status": "resume_ready",
            "conflict_id": conflict_id,
            "resolution_sha256": digest,
            "resolution_path": str(path),
            "resume_scope": "conflicted_capture_only",
            "rerun_analysis_package": False,
            "writer_lease_released": True,
        }


def freeze_from_store(
    *,
    package_store: AnalysisPackageStore,
    subject: str,
    capture_intake_date: str,
    skill_name: str,
    skill_source_path: Path,
    declared_version: str | None = None,
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
    )


__all__ = [
    "BATCH_SCHEMA", "GlobalWriterLease", "NightlySolCoordinator",
    "NightlySolError", "NightlySolStore", "freeze_from_store",
    "freeze_nightly_batch", "parse_nightly_command", "validate_adapter_result",
]
