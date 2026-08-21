"""Exact-command nightly batching and thin Subject Sol handoff for V2."""

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

from analysis_package_v1 import (
    AnalysisPackageError,
    AnalysisPackageStore,
    PACKAGE_SCHEMA,
    canonical_bytes,
    sha256_value,
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


BATCH_SCHEMA = "study-intake-nightly-sol-batch-v2"
RESULT_SCHEMA = "study-intake-nightly-sol-adapter-result-v1"
AUTHORIZATION_SCHEMA = "study-intake-nightly-command-authorization-v1"
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
    authorization: Mapping[str, Any],
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
            "authorization_id": checked_authorization["authorization_id"],
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
        "authorization": checked_authorization,
        "status": "frozen",
        "formal_write_count": 0,
    }


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


def _subject_tasks(
    *, batch: Mapping[str, Any], package_store: AnalysisPackageStore
) -> list[dict[str, Any]]:
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
            result = adapter.execute(batch, native_executor=native_executor)
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
                batch,
                native_executor=native_executor,
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
    )


__all__ = [
    "AUTHORIZATION_SCHEMA", "BATCH_SCHEMA", "NightlySolCoordinator",
    "NightlySolError", "build_command_authorization", "freeze_from_store",
    "freeze_nightly_batch", "parse_nightly_command",
    "validate_adapter_result", "validate_command_authorization",
]
