from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from analysis_package_store import (  # noqa: E402
    AnalysisPackageStore,
    build_durable_capture,
    canonical_bytes,
    sha256_value,
)
from analysis_package_v2 import (  # noqa: E402
    build_terra_initial_analysis,
    publish_analysis_package_v2,
)
from multi_agent_report_contract import (  # noqa: E402
    build_luna_investigation_report_v2,
    build_sol_handoff_v3,
    build_terra_final_input_v2,
    build_terra_final_report_v2,
)
from nightly_sol_alignment import (  # noqa: E402
    NightlySolCoordinator,
    NightlySolError,
    freeze_from_store,
    parse_nightly_command,
    validate_adapter_result,
)
from subject_sol_contract import SubjectSolRuntimeStore  # noqa: E402
from preprocessor_core import CodexRunner, StructuredStageResult  # noqa: E402
from read_bundle import build_read_bundle  # noqa: E402
from orchestration_plan import seal_read_plan  # noqa: E402
from tests.test_multi_agent_report_contract import (  # noqa: E402
    branch_result,
    legacy_handoff,
    plan,
)


LABELS = {"math": "数学", "cs408": "408", "english": "英语"}
SKILLS = {
    "math": "kaoyan-math-nightly-qa",
    "cs408": "kaoyan-408-daily-intake-curation",
    "english": "kaoyan-english-daily-intake-curation",
}


def state_sha(root: Path) -> str:
    rows = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.glob("*.json"))
    }
    return hashlib.sha256(canonical_bytes(rows)).hexdigest()


class FakeSubjectAdapter:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        batch: Mapping[str, Any],
        *,
        native_executor: Any,
        capture_ids: list[str] | None = None,
        resolution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if batch["subject"] != self.subject:
            raise AssertionError("cross-subject adapter call")
        selected = list(capture_ids or batch["capture_ids"])
        invocation = {
            "batch": dict(batch),
            "batch_id": batch["batch_id"],
            "subject": batch["subject"],
            "capture_ids": selected,
            "analysis_packages": [
                row for row in batch["analysis_packages"]
                if row["capture_id"] in selected
            ],
            "resolution": dict(resolution) if resolution is not None else None,
        }
        self.calls.append(invocation)
        return dict(native_executor(invocation))


def native_executor(
    *,
    subject: str,
    state_root: Path,
    conflict_capture_id: str | None = None,
):
    call_index = 0

    def execute(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal call_index
        call_index += 1
        batch = invocation["batch"]
        selected = list(invocation["capture_ids"])
        resolution = invocation.get("resolution")
        conflict = (
            conflict_capture_id
            if resolution is None and conflict_capture_id in selected
            else None
        )
        completed = [capture_id for capture_id in selected if capture_id != conflict]
        before = state_sha(state_root)
        operations: list[dict[str, Any]] = []
        for capture_id in completed:
            path = state_root / f"{capture_id}.json"
            payload = canonical_bytes(
                {"capture_id": capture_id, "subject": subject, "committed": True}
            )
            if not path.exists():
                path.write_bytes(payload)
                operations.append(
                    {"capture_id": capture_id, "operation": "synthetic_commit"}
                )
        after = state_sha(state_root)
        ended = f"2026-08-21T00:00:{call_index * 2:02d}Z"
        stopped = f"2026-08-21T00:00:{call_index * 2 + 1:02d}Z"
        native = {
            "schema_version": "synthetic-subject-native-terminal-v1",
            "subject": subject,
            "batch_id": batch["batch_id"],
            "status": "awaiting_user" if conflict else "committed",
            "capture_results": [
                {"capture_id": capture_id, "status": "committed"}
                for capture_id in completed
            ],
            "changed_files": [f"{capture_id}.json" for capture_id in completed],
            "formal_write_count": len(operations),
            "conflict": (
                {
                    "capture_id": conflict,
                    "kind": "ambiguous_formal_target",
                }
                if conflict
                else None
            ),
            "execution_evidence": {
                "pre_state_sha256": before,
                "post_state_sha256": after,
                "operations": operations,
                "adapter_run_id": f"synthetic-run-{call_index}",
                "pid": 101,
                "transaction_id": f"synthetic-tx-{call_index}",
                "ended_at": ended,
                "stopped_at": stopped,
                "exit_code": 0,
            },
        }
        conflict_id = (
            "CONFLICT-"
            + sha256_value(
                {
                    "subject": subject,
                    "batch_id": batch["batch_id"],
                    "capture_id": conflict,
                    "kind": "ambiguous_formal_target",
                    "skill_source_sha256": batch["skill"]["source_sha256"],
                }
            )[:24].upper()
            if conflict
            else None
        )
        return {
            "schema_version": "study-intake-nightly-sol-adapter-result-v1",
            "adapter_name": f"Synthetic{subject.title()}Adapter",
            "subject": subject,
            "batch_id": batch["batch_id"],
            "status": "awaiting_user" if conflict else "complete",
            "native_receipt": native,
            "native_receipt_sha256": sha256_value(native),
            "conflict_id": conflict_id,
            "formal_write_count": len(operations),
        }

    return execute


def publish_historical_package_fixture(
    store: AnalysisPackageStore, capture: Mapping[str, Any]
) -> str:
    """Materialize immutable V1 history without invoking a retired driver."""

    capture_sha, capture_ref = store.publish_capture(capture)
    stages: list[dict[str, Any]] = []
    for stage in ("terra_analysis", "luna_analysis", "terra_final"):
        raw_sha = sha256_value({
            "capture_id": capture["capture_id"], "stage": stage, "kind": "raw"
        })
        raw_ref = "study-intake-direct-model-stage-raw://sha256/" + raw_sha
        report = {
            "schema_version": "study-intake-analysis-stage-report-v2",
            "stage": stage,
            "subject": capture["subject"],
            "capture_id": capture["capture_id"],
            "summary": "historical fixture",
            "proposals": [],
            "duplicate_candidates": [],
            "warnings": [],
            "evidence_refs": [capture_ref],
            "normalization_status": "complete",
            "formal_write_count": 0,
        }
        report_sha, report_ref = store._publish(
            store.report_root, report, "study-intake-analysis-stage-report"
        )
        executor_receipt = {
            "schema_version": "historical-fixture-executor-receipt-v1",
            "stage": stage,
            "raw_output_object_sha256": raw_sha,
            "raw_output_object_ref": raw_ref,
            "formal_write_count": 0,
        }
        execution = {
            "schema_version": "study-intake-analysis-stage-execution-receipt-v1",
            "stage": stage,
            "subject": capture["subject"],
            "capture_id": capture["capture_id"],
            "raw_output_sha256": raw_sha,
            "raw_output_ref": raw_ref,
            "executor_receipt": executor_receipt,
            "executor_receipt_sha256": sha256_value(executor_receipt),
            "formal_write_count": 0,
        }
        execution_sha, execution_ref = store._publish(
            store.execution_receipt_root,
            execution,
            "study-intake-analysis-stage-execution-receipt",
        )
        normalization = {
            "schema_version": (
                "study-intake-analysis-stage-normalization-receipt-v1"
            ),
            "stage": stage,
            "subject": capture["subject"],
            "capture_id": capture["capture_id"],
            "execution_receipt_sha256": execution_sha,
            "execution_receipt_ref": execution_ref,
            "raw_output_sha256": raw_sha,
            "raw_output_ref": raw_ref,
            "report_sha256": report_sha,
            "report_ref": report_ref,
            "normalization_status": "complete",
            "warning_codes": [],
            "formal_write_count": 0,
        }
        normalization_sha, normalization_ref = store._publish(
            store.normalization_receipt_root,
            normalization,
            "study-intake-analysis-stage-normalization-receipt",
        )
        stages.append({
            "stage": stage,
            "report_sha256": report_sha,
            "report_ref": report_ref,
            "raw_output_sha256": raw_sha,
            "raw_output_ref": raw_ref,
            "execution_receipt_sha256": execution_sha,
            "execution_receipt_ref": execution_ref,
            "normalization_receipt_sha256": normalization_sha,
            "normalization_receipt_ref": normalization_ref,
            "normalization_status": "complete",
            "formal_write_count": 0,
        })
    package = {
        "schema_version": "study-intake-analysis-package-v1",
        "package_id": "ANPKG-" + sha256_value({
            "capture_sha256": capture_sha,
            "stage_report_sha256s": [row["report_sha256"] for row in stages],
        })[:24].upper(),
        "capture_id": capture["capture_id"],
        "subject": capture["subject"],
        "study_date": capture["study_date"],
        "captured_at": capture["captured_at"],
        "capture_intake_date": capture["capture_intake_date"],
        "capture_sha256": capture_sha,
        "capture_ref": capture_ref,
        "stage_order": [row["stage"] for row in stages],
        "stages": stages,
        "warnings": [],
        "status": "ready_for_nightly",
        "formal_write_count": 0,
    }
    payload = canonical_bytes(package)
    package_sha = hashlib.sha256(payload).hexdigest()
    store._write_no_clobber(
        store.package_root / package_sha[:2] / f"{package_sha}.json", payload
    )
    pointer = {
        "schema_version": "study-intake-analysis-package-pointer-v1",
        "subject": capture["subject"],
        "capture_intake_date": capture["capture_intake_date"],
        "capture_id": capture["capture_id"],
        "package_sha256": package_sha,
        "package_ref": "study-intake-analysis-package://sha256/" + package_sha,
        "formal_write_count": 0,
    }
    store._write_no_clobber(
        store.index_root / str(capture["subject"])
        / str(capture["capture_intake_date"])
        / f"{capture['capture_id']}.json",
        canonical_bytes(pointer),
    )
    return package_sha


def prepare_batch(
    root: Path,
    *,
    subject: str,
    capture_ids: list[str],
    study_dates: list[str] | None = None,
    skill_path: Path | None = None,
) -> tuple[AnalysisPackageStore, dict[str, Any], Path]:
    package_store = AnalysisPackageStore(root)
    for index, capture_id in enumerate(capture_ids, start=1):
        publish_historical_package_fixture(
            package_store,
            build_durable_capture(
                capture_id=capture_id,
                subject=subject,
                study_date=(study_dates or ["2026-08-21"] * len(capture_ids))[
                    index - 1
                ],
                captured_at=f"2026-08-21T0{index}:00:00+08:00",
                payload={"synthetic": index},
                source_kind="synthetic",
            ),
        )
    skill = skill_path or (root / f"{subject}-SKILL.md")
    if skill_path is None:
        skill.write_text(f"synthetic {subject} skill\n", encoding="utf-8")
    parsed = parse_nightly_command(
        f"开始 2026-08-21 {LABELS[subject]}正式入库",
        today=dt.date(2026, 8, 21),
    )
    batch = freeze_from_store(
        package_store=package_store,
        subject=subject,
        capture_intake_date="2026-08-21",
        skill_name=SKILLS[subject],
        skill_source_path=skill,
        authorization=parsed["authorization"],
    )
    return package_store, batch, skill


def _publish_cas(base: Path, value: Mapping[str, Any]) -> tuple[str, str]:
    payload = canonical_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    path = base / digest[:2] / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest, str(path)


def _signed_receipt(
    core: Mapping[str, Any], *, key: bytes, purpose: str
) -> dict[str, Any]:
    return {
        **copy.deepcopy(dict(core)),
        "hmac_key_id": hashlib.sha256(key).hexdigest(),
        "hmac_sha256": hmac.new(
            key,
            canonical_bytes({"purpose": purpose, "payload": core}),
            hashlib.sha256,
        ).hexdigest(),
    }


def _direct_execution_chain(
    root: Path, *, stage_name: str, model: str, seed: str,
    raw_jsonl: bool = False,
) -> dict[str, Any]:
    raw_value = {
        "schema_version": "synthetic-model-output-v1",
        "stage_name": stage_name,
        "seed": seed,
        "formal_write_count": 0,
    }
    raw_payload = (
        (
            json.dumps({"type": "item.completed", "seed": seed})
            + "\n"
            + json.dumps({"type": "turn.completed", "seed": seed})
            + "\n"
        ).encode("utf-8")
        if raw_jsonl
        else canonical_bytes(raw_value)
    )
    runner = CodexRunner(
        {"model": model, "reasoning_effort": "max"}, root
    )
    raw_refs = runner._persist_direct_model_stage_raw(
        stage_name=stage_name, raw_output=raw_payload
    )
    execution_refs = runner._publish_model_stage_execution(
        stage_name=stage_name,
        execution_status="completed",
        raw_refs=raw_refs,
        transport_sha256=None,
        transcript_sha256=None,
        authority_snapshot_manifest_sha256=None,
        mcp_grounding_manifest_sha256=None,
        attempt_counts={
            "attempted_mcp_tool_call_count": 0,
            "successful_mcp_tool_call_count": 0,
            "grounding_mcp_tool_call_count": 0,
            "failed_mcp_tool_call_count": 0,
            "last_mcp_error_code": None,
        },
        provider_returncode=0,
        duration_ms=1,
        requested_model=model,
        requested_reasoning_effort="max",
    )
    result = StructuredStageResult(
        payload=raw_value,
        duration_ms=1,
        runtime_model=model,
        runtime_reasoning_effort="max",
        runtime_metadata_provenance="synthetic_persisted_fixture",
        runtime_identity_status="confirmed",
        output_sha256=sha256_value(raw_value),
        raw_output_object_sha256=raw_refs["raw_output_object_sha256"],
        raw_output_object_ref=raw_refs["raw_output_object_ref"],
        stage_execution_receipt_sha256=execution_refs[
            "stage_execution_receipt_sha256"
        ],
        stage_execution_receipt_ref=execution_refs[
            "stage_execution_receipt_ref"
        ],
        stage_name=stage_name,
    )
    normalization_refs = runner._publish_model_stage_normalization(
        stage_name=stage_name,
        result=result,
        normalized_payload=raw_value,
        warnings=(),
        error_code=None,
    )
    return {
        "requested_model": model,
        "requested_reasoning_effort": "max",
        "provider_stage_name": stage_name,
        "raw_output_sha256": raw_refs["raw_output_object_sha256"],
        "raw_output_ref": raw_refs["raw_output_object_ref"],
        "stage_execution_receipt_sha256": execution_refs[
            "stage_execution_receipt_sha256"
        ],
        "stage_execution_receipt_ref": execution_refs[
            "stage_execution_receipt_ref"
        ],
        "normalization_receipt_sha256": normalization_refs[
            "stage_normalization_receipt_sha256"
        ],
        "normalization_receipt_ref": normalization_refs[
            "stage_normalization_receipt_ref"
        ],
        "formal_write_count": 0,
    }


def _luna_session_artifacts(
    root: Path,
    *,
    key: bytes,
    read_plan: Mapping[str, Any],
    branch: Mapping[str, Any],
    result: Mapping[str, Any],
    failed: bool,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    branch_id = str(branch["branch_id"])
    session_id = str(result["read_session_id"])
    fingerprint = "7" * 64
    manifest_core = {
        "schema_version": "study-read-mcp-read-session.v2",
        "read_session_id": session_id,
        "subject": read_plan["subject"],
        "candidate_release_id": read_plan["release_id"],
        "plugin_version": "synthetic-v2",
        "skill_id": f"background-{read_plan['subject']}-processing",
        "skill_version": "4.0.1",
        "mcp_server_release": "synthetic-mcp-v1",
        "generation": read_plan["generation"],
        "authority_fingerprint": fingerprint,
        "created_at": "2026-08-24T00:00:00+00:00",
        "formal_write_count": 0,
        "capture_id": read_plan["capture_id"],
        "capture_manifest_path": "/synthetic/capture.json",
        "capture_manifest_sha256": "8" * 64,
        "artifact_ids": list(branch["allowed_task_artifact_ids"]),
    }
    manifest_sha = sha256_value(manifest_core)
    manifest = {**manifest_core, "manifest_sha256": manifest_sha}
    manifest_path = (
        root / "private/mcp-read-sessions/sha256" / manifest_sha[:2]
        / f"{manifest_sha}.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(canonical_bytes(manifest))

    opened_core = {
        "schema_version": "mcp_read_session_receipt_v1",
        "phase": "opened",
        "subject": read_plan["subject"],
        "processing_binding_sha256": "9" * 64,
        "read_session_id": session_id,
        "read_session_manifest_sha256": manifest_sha,
        "generation": read_plan["generation"],
        "authority_fingerprint": fingerprint,
        "formal_write_count": 0,
    }
    opened = _signed_receipt(
        opened_core, key=key, purpose="mcp-read-session-receipt-v1"
    )
    opened_sha, _ = _publish_cas(
        root / "dispatch/mcp-read-session-receipts/sha256", opened
    )
    opened_ref = "study-intake-mcp-read-session://sha256/" + opened_sha

    if failed:
        final_core = {
            "schema_version": "mcp_investigation_session_receipt_v1",
            "phase": "investigation_failed",
            "terminal_status": "failed",
            "error_code": "synthetic_branch_failure",
            "subject": read_plan["subject"],
            "branch_id": branch_id,
            "provider_stage_name": f"{read_plan['subject']}_luna_analysis",
            "read_session_id": session_id,
            "read_session_manifest_sha256": manifest_sha,
            "opened_receipt_sha256": opened_sha,
            "mcp_call_receipt_sha256": None,
            "mcp_call_receipt_ref": None,
            "mcp_transcript_sha256": None,
            "mcp_transcript_ref": None,
            "raw_output_object_sha256": None,
            "raw_output_object_ref": None,
            "stage_execution_receipt_sha256": None,
            "stage_execution_receipt_ref": None,
            "provider_request_count": 0,
            "mcp_tool_call_count": 0,
            "model_call_count": 0,
            "proposal_only": True,
            "formal_write_count": 0,
        }
        final = _signed_receipt(
            final_core,
            key=key,
            purpose="mcp-investigation-session-receipt-v1",
        )
        final_sha, _ = _publish_cas(
            root / "dispatch/mcp-read-session-receipts/sha256", final
        )
        return None, {
            "final_sha256": final_sha,
            "final_ref": (
                "study-intake-mcp-investigation-session://sha256/" + final_sha
            ),
        }

    transcript = {
        "schema_version": "model-driven-mcp-stage-transcript-v1",
        "stage_name": f"{read_plan['subject']}_luna_analysis",
        "subject": read_plan["subject"],
        "read_session_id": session_id,
        "read_session_manifest_sha256": manifest_sha,
        "generation": read_plan["generation"],
        "authority_fingerprint": fingerprint,
        "calls": copy.deepcopy(list(result["calls"])),
        "formal_write_count": 0,
    }
    transcript_sha, _ = _publish_cas(
        root / "private/reports/mcp-stage-transcripts/sha256", transcript
    )
    transcript_ref = (
        "study-intake-mcp-stage-transcript://sha256/" + transcript_sha
    )
    call_core = {
        "schema_version": "mcp_stage_call_receipt_v2",
        "phase": "model_stage_calls",
        "stage_name": f"{read_plan['subject']}_luna_analysis",
        "subject": read_plan["subject"],
        "read_session_id": session_id,
        "read_session_manifest_sha256": manifest_sha,
        "transcript_sha256": transcript_sha,
        "calls": [{"sequence": index + 1} for index, _ in enumerate(result["calls"])],
        "mcp_tool_call_count": len(result["calls"]),
        "provider_request_count": len(result["calls"]) + 1,
        "formal_write_count": 0,
    }
    call_receipt = _signed_receipt(
        call_core, key=key, purpose="mcp-read-session-model-calls-v2"
    )
    call_sha, _ = _publish_cas(
        root / "dispatch/mcp-read-session-call-receipts/sha256", call_receipt
    )
    call_ref = "study-intake-mcp-read-session-call://sha256/" + call_sha
    execution = _direct_execution_chain(
        root,
        stage_name=f"{read_plan['subject']}_luna_analysis",
        model="gpt-5.6-luna",
        seed=branch_id,
    )
    final_core = {
        "schema_version": "mcp_investigation_session_receipt_v1",
        "phase": "investigation_complete",
        "subject": read_plan["subject"],
        "branch_id": branch_id,
        "provider_stage_name": f"{read_plan['subject']}_luna_analysis",
        "read_session_id": session_id,
        "read_session_manifest_sha256": manifest_sha,
        "opened_receipt_sha256": opened_sha,
        "mcp_call_receipt_sha256": call_sha,
        "mcp_transcript_sha256": transcript_sha,
        "proposal_only": True,
        "formal_write_count": 0,
    }
    final = _signed_receipt(
        final_core, key=key, purpose="mcp-investigation-session-receipt-v1"
    )
    final_sha, _ = _publish_cas(
        root / "dispatch/mcp-read-session-receipts/sha256", final
    )
    artifacts = {
        "read_session_id": session_id,
        "read_session_manifest_sha256": manifest_sha,
        "opened_session_receipt_ref": opened_ref,
        "final_session_receipt_ref": (
            "study-intake-mcp-investigation-session://sha256/" + final_sha
        ),
        "mcp_transcript_sha256": transcript_sha,
        "mcp_transcript_ref": transcript_ref,
        "mcp_call_receipt_sha256": call_sha,
        "mcp_call_receipt_ref": call_ref,
        "raw_output_sha256": execution["raw_output_sha256"],
        "raw_output_ref": execution["raw_output_ref"],
        "stage_execution_receipt_sha256": execution[
            "stage_execution_receipt_sha256"
        ],
        "stage_execution_receipt_ref": execution[
            "stage_execution_receipt_ref"
        ],
        "normalization_receipt_sha256": execution[
            "normalization_receipt_sha256"
        ],
        "normalization_receipt_ref": execution["normalization_receipt_ref"],
    }
    return artifacts, {}


def prepare_v2_batch(
    root: Path,
    *,
    subject: str = "math",
    failed_last: bool = False,
    skill_path: Path | None = None,
) -> tuple[AnalysisPackageStore, dict[str, Any], dict[str, Any], Path]:
    key_path = root / "dispatch/state/authority.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = b"v" * 32
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    base_plan = plan(4 if failed_last else 3)
    plan_core = {
        key: copy.deepcopy(value)
        for key, value in base_plan.items()
        if key != "plan_sha256"
    }
    plan_core.update(
        {
            "plan_id": f"PLAN-DUAL-{subject.upper()}",
            "subject": subject,
            "capture_id": f"CAP-DUAL-{subject.upper()}",
        }
    )
    read_plan = seal_read_plan(plan_core)
    results = [
        branch_result(read_plan, index)
        for index in range(1, len(read_plan["branches"]) + 1)
    ]
    for index, result in enumerate(results, start=1):
        evidence_ref = result["evidence"][0]["evidence_ref"]
        replacement_ref = evidence_ref.replace(
            "mcp-item:math:", f"mcp-item:{subject}:"
        )
        result["evidence"][0]["evidence_ref"] = replacement_ref
        evidence_ref = replacement_ref
        result["calls"][0]["result"] = {
            "items": [{"evidence_ref": evidence_ref}]
        }
        result_core = {
            key: copy.deepcopy(value)
            for key, value in result.items()
            if key != "result_sha256"
        }
        result["result_sha256"] = sha256_value(result_core)
    failed_index = len(results) - 1 if failed_last else None
    artifact_rows: list[dict[str, Any] | None] = []
    for index, (branch, result) in enumerate(
        zip(read_plan["branches"], results)
    ):
        is_failed = failed_index == index
        if is_failed:
            result.update({
                "status": "failed",
                "calls": [],
                "evidence": [],
                "findings": [],
                "pagination_closed": False,
            })
        artifacts, failure = _luna_session_artifacts(
            root,
            key=key,
            read_plan=read_plan,
            branch=branch,
            result=result,
            failed=is_failed,
        )
        artifact_rows.append(artifacts)
        if is_failed:
            result["findings"] = [{
                "kind": "technical_diagnostic",
                "error_code": "synthetic_branch_failure",
                "failure_session_receipt_sha256": failure["final_sha256"],
                "failure_session_receipt_ref": failure["final_ref"],
                "execution_artifacts": {},
            }]
        result_core = {
            key: copy.deepcopy(value)
            for key, value in result.items()
            if key != "result_sha256"
        }
        result["result_sha256"] = sha256_value(result_core)
    read_bundle = build_read_bundle(
        read_plan,
        {
            "results": results,
            "logical_branch_count": len(results),
            "physical_slot_count": len(results),
            "maximum_active_branch_count": len(results),
            "wave_count": 1,
            "duration_ms": 1,
        },
    )
    reports = [
        build_luna_investigation_report_v2(
            plan=read_plan,
            read_bundle=read_bundle,
            branch_result=result,
            summary=f"Luna report {index + 1}",
            confidence="high",
            execution_artifacts=artifact_rows[index],
        )
        for index, result in enumerate(results)
        if result["status"] == "succeeded"
    ]
    diagnostics = [row for row in results if row["status"] != "succeeded"]
    terra_input = build_terra_final_input_v2(
        plan=read_plan,
        read_bundle=read_bundle,
        branch_results=results,
        luna_reports=reports,
        diagnostic_records=diagnostics,
    )
    terra_final = build_terra_final_report_v2(
        terra_input=terra_input,
        summary="Terra final V2",
        branch_assessments=[{
            "branch_id": row["branch_id"],
            "outcome": row["outcome"],
            "disposition": (
                "adopt" if row["outcome"] == "report" else "diagnostic_only"
            ),
            "rationale": "synthetic complete evidence",
        } for row in terra_input["branch_coverage"]],
        subject_analysis={"summary": "synthetic subject analysis"},
    )
    legacy = legacy_handoff(read_plan)
    legacy["read_bundle_sha256"] = read_bundle["read_bundle_sha256"]
    legacy_core = {
        key: copy.deepcopy(value)
        for key, value in legacy.items()
        if key != "handoff_sha256"
    }
    legacy["handoff_sha256"] = sha256_value(legacy_core)
    sol_handoff = build_sol_handoff_v3(
        legacy_handoff=legacy,
        terra_input=terra_input,
        terra_final_report=terra_final,
    )
    capture = build_durable_capture(
        capture_id=read_plan["capture_id"],
        subject=read_plan["subject"],
        study_date="2026-08-24",
        captured_at="2026-08-24T09:00:00+08:00",
        payload={"synthetic": "multi-agent-v2"},
        source_kind="synthetic",
    )
    terra_initial = build_terra_initial_analysis(
        capture=capture,
        summary="Terra initial V2",
        analysis={"sections": [{
            "kind": "learning_facts",
            "summary": "frozen learning facts",
            "evidence_refs": [],
        }]},
        proposed_branches=[{
            "branch_id": row["branch_id"],
            "purpose": row["purpose"],
            "rationale": row["rationale"],
        } for row in read_plan["branches"]],
    )
    initial_execution = _direct_execution_chain(
        root,
        stage_name=f"{subject}_analysis",
        model="gpt-5.6-terra",
        seed="terra-initial",
    )
    final_execution = _direct_execution_chain(
        root,
        stage_name=f"{subject}_critical_review",
        model="gpt-5.6-terra",
        seed="terra-final",
        raw_jsonl=True,
    )
    store = AnalysisPackageStore(root)
    package = publish_analysis_package_v2(
        store,
        capture=capture,
        plan=read_plan,
        read_bundle=read_bundle,
        branch_results=results,
        terra_initial=terra_initial,
        terra_initial_execution=initial_execution,
        luna_outputs=[
            (
                next(row for row in reports if row["branch_id"] == result["branch_id"])
                if result["status"] == "succeeded"
                else result
            )
            for result in results
        ],
        terra_final=terra_final,
        terra_final_execution=final_execution,
        sol_handoff=sol_handoff,
    )
    skill = skill_path or (root / f"{subject}-v2-SKILL.md")
    if skill_path is None:
        skill.write_text(
            f"synthetic {subject} v2 skill\n", encoding="utf-8"
        )
    parsed = parse_nightly_command(
        f"开始 2026-08-24 {LABELS[subject]}正式入库",
        today=dt.date(2026, 8, 24),
    )
    batch = freeze_from_store(
        package_store=store,
        subject=subject,
        capture_intake_date="2026-08-24",
        skill_name=SKILLS[subject],
        skill_source_path=skill,
        authorization=parsed["authorization"],
    )
    return store, batch, package, skill


class NightlySolAlignmentTests(unittest.TestCase):
    def test_exact_command_routes_absolute_or_resolved_today_only(self) -> None:
        today = dt.date(2026, 8, 21)
        absolute = parse_nightly_command(
            "开始 2026-08-20 数学正式入库", today=today
        )
        self.assertEqual(absolute["subject"], "math")
        self.assertEqual(absolute["capture_intake_date"], "2026-08-20")
        self.assertEqual(
            absolute["authorization"]["normalized_command"],
            "开始 2026-08-20 数学正式入库",
        )
        relative = parse_nightly_command("开始今天的408正式入库", today=today)
        self.assertEqual(
            relative["authorization"]["normalized_command"],
            "开始 2026-08-21 408正式入库",
        )
        for invalid in (
            "开始今天数学正式入库",
            "快速入库",
            "记录一下",
            "今天做过",
            " 开始今天的英语正式入库",
            "开始 2026-08-21 英语正式入库，请执行",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                NightlySolError, "nightly_command_not_exact"
            ):
                parse_nightly_command(invalid, today=today)

    def test_batch_uses_capture_intake_date_not_study_date_and_binds_authority(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            _store, batch, _skill = prepare_batch(
                Path(folder),
                subject="math",
                capture_ids=["CAP-MATH-001", "CAP-MATH-002"],
                study_dates=["2026-08-14", "2026-08-19"],
            )
            self.assertEqual(batch["capture_intake_date"], "2026-08-21")
            self.assertEqual(
                batch["capture_set_sha256"], sha256_value(batch["capture_ids"])
            )
            self.assertTrue(
                batch["authorization"]["authorization_id"].startswith("NAUTH-")
            )

    def test_v2_native_sol_receives_every_luna_report_and_both_terra_layers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, package, _skill = prepare_v2_batch(root)
            handoff = batch["multi_agent_handoffs"][0]
            self.assertEqual(
                batch["analysis_package_schema_version"],
                "study-intake-analysis-package-v2",
            )
            self.assertEqual(len(handoff["luna_investigations"]), 3)
            self.assertTrue(all(
                row["outcome"] == "report" and "report" in row
                for row in handoff["luna_investigations"]
            ))
            self.assertIn("raw_output", handoff["terra_initial_execution"])
            self.assertIn("raw_output", handoff["terra_final_execution"])
            first_binding = package["luna_outputs"][0]
            self.assertNotEqual(
                first_binding["sha256"], first_binding["report_sha256"]
            )
            self.assertEqual(
                handoff["sol_handoff"]["ordered_luna_reports"][0][
                    "report_sha256"
                ],
                first_binding["report_sha256"],
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal-v2"
            formal.mkdir()
            observed: list[dict[str, Any]] = []
            base_native = native_executor(subject="math", state_root=formal)

            def inspect(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                observed.append(copy.deepcopy(dict(invocation)))
                verified = invocation["verified_multi_agent_handoffs"]
                self.assertEqual(len(verified), 1)
                self.assertEqual(len(verified[0]["luna_investigations"]), 3)
                self.assertIn("terra_initial", verified[0])
                self.assertIn("terra_final", verified[0])
                self.assertIn("investigation_summary", verified[0])
                self.assertIn("sol_handoff", verified[0])
                self.assertEqual(
                    invocation["verified_multi_agent_handoff_set_sha256"],
                    sha256_value(verified),
                )
                return base_native(invocation)

            result = NightlySolCoordinator(
                runtime_store=SubjectSolRuntimeStore(root),
                package_store=package_store,
            ).execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=inspect,
                authorized_at="2026-08-24T12:00:00Z",
                owner_id="v2-complete",
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(observed), 1)
            self.assertNotIn(
                "multi_agent_handoffs", adapter.calls[0]["batch"]
            )
            self.assertEqual(
                set(adapter.calls[0]["batch"]),
                {
                    "schema_version", "batch_id", "subject",
                    "capture_intake_date", "capture_ids",
                    "capture_set_sha256", "analysis_packages", "skill",
                    "authorization", "status", "formal_write_count",
                },
            )

    def test_v2_bridge_accepts_the_deployed_strict_math_adapter(self) -> None:
        helper_path = ROOT.parent / "kaoyan-math/tests/test_nightly_sol_adapter.py"
        spec = importlib.util.spec_from_file_location(
            "nightly_v2_real_math_adapter", helper_path
        )
        assert spec and spec.loader
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        adapter_module = helper.adapter
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _package, _skill = prepare_v2_batch(
                root, skill_path=Path(adapter_module.SKILL_PATH)
            )
            strict_batch = {
                key: copy.deepcopy(value)
                for key, value in batch.items()
                if key not in {
                    "analysis_package_schema_version",
                    "multi_agent_handoffs",
                    "multi_agent_handoff_set_sha256",
                }
            }
            observed: list[dict[str, Any]] = []

            def subject_native(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                observed.append(copy.deepcopy(dict(invocation)))
                self.assertEqual(
                    len(invocation["verified_multi_agent_handoffs"][0][
                        "luna_investigations"
                    ]),
                    3,
                )
                original = helper.close_receipt

                def dated_close(*args: Any, **kwargs: Any) -> dict[str, Any]:
                    receipt = original(*args, **kwargs)
                    receipt["study_date"] = "2026-08-24"
                    receipt["artifact_date"] = "2026-08-24"
                    return receipt

                helper.close_receipt = dated_close
                try:
                    return helper.terminal(strict_batch)
                finally:
                    helper.close_receipt = original

            result = NightlySolCoordinator(
                runtime_store=SubjectSolRuntimeStore(root),
                package_store=package_store,
            ).execute_batch(
                batch=batch,
                adapter=adapter_module.MathNightlySolAdapter(),
                native_executor=subject_native,
                authorized_at="2026-08-24T12:00:00Z",
                owner_id="v2-real-math-adapter",
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(observed), 1)

    def test_v2_bridge_accepts_deployed_cs408_and_english_adapters(
        self,
    ) -> None:
        helper_paths = {
            "cs408": ROOT.parent / "kaoyan-408/tests/test_nightly_sol_adapter.py",
            "english": ROOT.parent
            / "kaoyan-english/tests/english_pipeline/test_nightly_sol_adapter.py",
        }
        for subject, helper_path in helper_paths.items():
            with self.subTest(subject=subject), tempfile.TemporaryDirectory() as folder:
                spec = importlib.util.spec_from_file_location(
                    f"nightly_v2_real_adapter_{subject}", helper_path
                )
                assert spec and spec.loader
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                adapter_module = helper.adapter
                adapter_class = getattr(
                    adapter_module,
                    {
                        "cs408": "CS408NightlySolAdapter",
                        "english": "EnglishNightlySolAdapter",
                    }[subject],
                )
                root = Path(folder)
                package_store, batch, _package, _skill = prepare_v2_batch(
                    root,
                    subject=subject,
                    skill_path=Path(adapter_module.SKILL_PATH),
                )
                strict_batch = {
                    key: copy.deepcopy(value)
                    for key, value in batch.items()
                    if key
                    not in {
                        "analysis_package_schema_version",
                        "multi_agent_handoffs",
                        "multi_agent_handoff_set_sha256",
                    }
                }
                observed: list[dict[str, Any]] = []

                def subject_native(
                    invocation: Mapping[str, Any],
                ) -> Mapping[str, Any]:
                    observed.append(copy.deepcopy(dict(invocation)))
                    self.assertEqual(
                        len(
                            invocation["verified_multi_agent_handoffs"][0][
                                "luna_investigations"
                            ]
                        ),
                        3,
                    )
                    capture_id = strict_batch["capture_ids"][0]
                    if subject == "cs408":
                        original = helper.batch
                        helper.batch = lambda: strict_batch
                        try:
                            return helper.terminal(
                                [helper.curated(capture_id)]
                            )
                        finally:
                            helper.batch = original
                    original_date = helper.DATE
                    helper.DATE = strict_batch["capture_intake_date"]
                    try:
                        return helper.terminal(strict_batch)
                    finally:
                        helper.DATE = original_date

                result = NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=batch,
                    adapter=adapter_class(),
                    native_executor=subject_native,
                    authorized_at="2026-08-24T12:00:00Z",
                    owner_id=f"v2-real-{subject}-adapter",
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(observed), 1)

    def test_v2_failed_luna_diagnostic_is_preserved_for_native_sol(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _package, _skill = prepare_v2_batch(
                root, failed_last=True
            )
            handoff = batch["multi_agent_handoffs"][0]
            outcomes = [
                row["outcome"] for row in handoff["luna_investigations"]
            ]
            self.assertEqual(outcomes, ["report", "report", "report", "diagnostic"])
            failed = handoff["luna_investigations"][-1]
            self.assertIn("final_session_receipt", failed)
            self.assertIn("opened_session_receipt", failed)
            self.assertEqual(
                failed["final_session_receipt"]["terminal_status"], "failed"
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal-v2-partial"
            formal.mkdir()
            observed: list[dict[str, Any]] = []
            base_native = native_executor(subject="math", state_root=formal)

            def inspect(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                observed.append(copy.deepcopy(dict(invocation)))
                rows = invocation["verified_multi_agent_handoffs"][0][
                    "luna_investigations"
                ]
                self.assertEqual(rows[-1]["outcome"], "diagnostic")
                self.assertEqual(
                    rows[-1]["branch_result"]["status"], "failed"
                )
                return base_native(invocation)

            result = NightlySolCoordinator(
                runtime_store=SubjectSolRuntimeStore(root),
                package_store=package_store,
            ).execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=inspect,
                authorized_at="2026-08-24T12:00:00Z",
                owner_id="v2-partial",
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(observed), 1)

    def test_v2_tampered_luna_object_stops_before_adapter_or_formal_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, package, _skill = prepare_v2_batch(root)
            binding = package["luna_outputs"][0]
            path = (
                package_store.report_root / binding["sha256"][:2]
                / f"{binding['sha256']}.json"
            )
            path.write_text("{}\n", encoding="utf-8")
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal-v2-tampered"
            formal.mkdir()
            with self.assertRaises(NightlySolError):
                NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject="math", state_root=formal
                    ),
                    authorized_at="2026-08-24T12:00:00Z",
                    owner_id="v2-tampered",
                )
            self.assertEqual(adapter.calls, [])
            self.assertEqual(list(formal.iterdir()), [])

    def test_v2_semantic_report_digest_cannot_be_replaced_by_cas_digest(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _package, _skill = prepare_v2_batch(root)
            tampered = copy.deepcopy(batch)
            handoff = tampered["multi_agent_handoffs"][0]
            luna = handoff["luna_investigations"][0]
            luna["package_output_binding"]["report_sha256"] = luna[
                "package_output_binding"
            ]["sha256"]
            handoff_core = {
                key: copy.deepcopy(value)
                for key, value in handoff.items()
                if key != "handoff_sha256"
            }
            handoff["handoff_sha256"] = sha256_value(handoff_core)
            tampered["multi_agent_handoff_set_sha256"] = sha256_value(
                tampered["multi_agent_handoffs"]
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal-v2-digest-confusion"
            formal.mkdir()
            with self.assertRaisesRegex(
                NightlySolError, "analysis_package_v2_batch_handoff_invalid"
            ):
                NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=tampered,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject="math", state_root=formal
                    ),
                    authorized_at="2026-08-24T12:00:00Z",
                    owner_id="v2-digest-confusion",
                )
            self.assertEqual(adapter.calls, [])
            self.assertEqual(list(formal.iterdir()), [])

    def test_v2_tampered_terra_execution_stops_before_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, package, _skill = prepare_v2_batch(root)
            digest = package["terra_final_execution"][
                "stage_execution_receipt_sha256"
            ]
            path = (
                root / "private/reports/direct-model-stage-execution/sha256"
                / digest[:2] / f"{digest}.json"
            )
            path.write_text("{}\n", encoding="utf-8")
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal-v2-terra-tampered"
            formal.mkdir()
            with self.assertRaisesRegex(
                NightlySolError, "multi_agent_terra_final_execution_invalid"
            ):
                NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject="math", state_root=formal
                    ),
                    authorized_at="2026-08-24T12:00:00Z",
                    owner_id="v2-terra-tampered",
                )
            self.assertEqual(adapter.calls, [])
            self.assertEqual(list(formal.iterdir()), [])

    def test_v2_native_invocation_identity_drift_stops_before_formal_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _package, _skill = prepare_v2_batch(root)
            formal = root / "formal-v2-invocation-drift"
            formal.mkdir()

            class DriftAdapter:
                def execute(
                    self,
                    adapter_batch: Mapping[str, Any],
                    *,
                    native_executor: Any,
                    capture_ids: list[str] | None = None,
                    resolution: Mapping[str, Any] | None = None,
                ) -> dict[str, Any]:
                    del capture_ids, resolution
                    return dict(native_executor({
                        "batch": dict(adapter_batch),
                        "batch_id": adapter_batch["batch_id"],
                        "subject": "english",
                        "capture_ids": list(adapter_batch["capture_ids"]),
                        "analysis_packages": list(
                            adapter_batch["analysis_packages"]
                        ),
                    }))

            with self.assertRaisesRegex(
                NightlySolError, "subject_adapter_execution_failed"
            ):
                NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=batch,
                    adapter=DriftAdapter(),
                    native_executor=native_executor(
                        subject="math", state_root=formal
                    ),
                    authorized_at="2026-08-24T12:00:00Z",
                    owner_id="v2-invocation-drift",
                )
            self.assertEqual(list(formal.iterdir()), [])

    def test_six_exact_commands_enter_the_matching_adapter_and_subjectsol_commit(self) -> None:
        cases = [
            (subject, command)
            for subject in ("math", "cs408", "english")
            for command in (
                f"开始 2026-08-21 {LABELS[subject]}正式入库",
                f"开始今天的{LABELS[subject]}正式入库",
            )
        ]
        for subject, command in cases:
            with self.subTest(subject=subject, command=command), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                package_store, _batch, skill = prepare_batch(
                    root, subject=subject, capture_ids=[f"CAP-{subject.upper()}-001"]
                )
                adapter = FakeSubjectAdapter(subject)
                formal = root / "synthetic-formal"
                formal.mkdir()
                result = NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).dispatch_subject_thread_command(
                    text=command,
                    today=dt.date(2026, 8, 21),
                    skill_name=SKILLS[subject],
                    skill_source_path=skill,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject=subject, state_root=formal
                    ),
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id=f"synthetic-{subject}",
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(adapter.calls[0]["batch"]["subject"], subject)

    def test_real_subject_adapter_classes_feed_the_existing_subjectsol_control_plane(self) -> None:
        canonical_root = ROOT.parent
        helper_paths = {
            "math": canonical_root / "kaoyan-math/tests/test_nightly_sol_adapter.py",
            "cs408": canonical_root / "kaoyan-408/tests/test_nightly_sol_adapter.py",
            "english": canonical_root
            / "kaoyan-english/tests/english_pipeline/test_nightly_sol_adapter.py",
        }
        for subject, helper_path in helper_paths.items():
            with self.subTest(subject=subject), tempfile.TemporaryDirectory() as folder:
                spec = importlib.util.spec_from_file_location(
                    f"nightly_subject_helper_{subject}", helper_path
                )
                assert spec and spec.loader
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                adapter_module = helper.adapter
                adapter_class = getattr(
                    adapter_module,
                    {
                        "math": "MathNightlySolAdapter",
                        "cs408": "CS408NightlySolAdapter",
                        "english": "EnglishNightlySolAdapter",
                    }[subject],
                )
                root = Path(folder)
                capture_id = f"CAP-REAL-{subject.upper()}-001"
                package_store, batch, _skill = prepare_batch(
                    root,
                    subject=subject,
                    capture_ids=[capture_id],
                    skill_path=Path(adapter_module.SKILL_PATH),
                )

                def subject_native(_invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                    if subject == "math":
                        return helper.terminal(batch)
                    if subject == "cs408":
                        original = helper.batch
                        helper.batch = lambda: batch
                        try:
                            return helper.terminal([helper.curated(capture_id)])
                        finally:
                            helper.batch = original
                    return helper.terminal(batch)

                result = NightlySolCoordinator(
                    runtime_store=SubjectSolRuntimeStore(root),
                    package_store=package_store,
                ).execute_batch(
                    batch=batch,
                    adapter=adapter_class(),
                    native_executor=subject_native,
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id=f"real-adapter-{subject}",
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["subject"], subject)

    def test_duplicate_command_does_not_repeat_native_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _skill = prepare_batch(
                root, subject="math", capture_ids=["CAP-MATH-IDEMPOTENT"]
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal"
            formal.mkdir()
            coordinator = NightlySolCoordinator(
                runtime_store=SubjectSolRuntimeStore(root),
                package_store=package_store,
            )
            first = coordinator.execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native_executor(subject="math", state_root=formal),
                authorized_at="2026-08-21T00:00:00Z",
                owner_id="first",
            )
            before = state_sha(formal)
            second = coordinator.execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native_executor(subject="math", state_root=formal),
                authorized_at="2026-08-21T00:05:00Z",
                owner_id="duplicate",
            )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "ALREADY_COMMITTED")
            self.assertEqual(len(adapter.calls), 1)
            self.assertEqual(state_sha(formal), before)

    def test_adapter_exception_releases_existing_subjectsol_claim(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _skill = prepare_batch(
                root, subject="cs408", capture_ids=["CAP-408-FAIL-001"]
            )
            runtime_store = SubjectSolRuntimeStore(root)
            coordinator = NightlySolCoordinator(
                runtime_store=runtime_store,
                package_store=package_store,
            )
            adapter = FakeSubjectAdapter("cs408")

            def broken(_invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError("synthetic native failure")

            with self.assertRaisesRegex(
                NightlySolError, "subject_adapter_execution_failed"
            ):
                coordinator.execute_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=broken,
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id="failure-test",
                )
            global_state = runtime_store.read_global()
            self.assertIsNone(global_state["active_writer"])
            self.assertEqual(global_state["queue"][0]["status"], "safe_paused")

    def test_apply_publication_failure_after_review_releases_claim(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package_store, batch, _skill = prepare_batch(
                root, subject="math", capture_ids=["CAP-MATH-PUBLISH-FAIL"]
            )
            runtime_store = SubjectSolRuntimeStore(root)
            coordinator = NightlySolCoordinator(
                runtime_store=runtime_store,
                package_store=package_store,
            )
            adapter = FakeSubjectAdapter("math")
            formal = root / "formal"
            formal.mkdir()
            with mock.patch.object(
                coordinator.writer_publisher,
                "publish_apply_from_execution_result_directory",
                side_effect=RuntimeError("synthetic apply publication failure"),
            ), self.assertRaisesRegex(
                NightlySolError, "subjectsol_publication_failed"
            ):
                coordinator.execute_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=native_executor(
                        subject="math", state_root=formal
                    ),
                    authorized_at="2026-08-21T00:00:00Z",
                    owner_id="publication-failure-test",
                )
            global_state = runtime_store.read_global()
            self.assertIsNone(global_state["active_writer"])
            self.assertEqual(global_state["queue"][0]["status"], "safe_paused")

    def test_conflict_releases_writer_and_resolution_runs_only_one_capture(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            captures = ["CAP-EN-A", "CAP-EN-B", "CAP-EN-C"]
            package_store, batch, _skill = prepare_batch(
                root, subject="english", capture_ids=captures
            )
            runtime_store = SubjectSolRuntimeStore(root)
            coordinator = NightlySolCoordinator(
                runtime_store=runtime_store,
                package_store=package_store,
            )
            adapter = FakeSubjectAdapter("english")
            formal = root / "formal"
            formal.mkdir()
            native = native_executor(
                subject="english",
                state_root=formal,
                conflict_capture_id="CAP-EN-B",
            )
            partial = coordinator.execute_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native,
                authorized_at="2026-08-21T00:00:00Z",
                owner_id="first-pass",
            )
            self.assertEqual(partial["status"], "PARTIAL_AWAITING_USER")
            self.assertTrue(partial["writer_lease_released"])
            self.assertIsNone(runtime_store.read_global()["active_writer"])
            a_hash = hashlib.sha256((formal / "CAP-EN-A.json").read_bytes()).hexdigest()
            c_hash = hashlib.sha256((formal / "CAP-EN-C.json").read_bytes()).hexdigest()
            self.assertFalse((formal / "CAP-EN-B.json").exists())

            def broken_resume(_invocation: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError("synthetic resumed item infrastructure failure")

            with self.assertRaisesRegex(
                NightlySolError, "subject_adapter_execution_failed"
            ):
                coordinator.resume_batch(
                    batch=batch,
                    adapter=adapter,
                    native_executor=broken_resume,
                    continuation_receipt_sha256=partial[
                        "continuation_receipt_sha256"
                    ],
                    conflict_id=partial["conflict_id"],
                    conflicted_capture_id="CAP-EN-B",
                    user_option="synthetic-target-b",
                    resolved_at="2026-08-21T00:10:00Z",
                    owner_id="resume-b-failed-once",
                )
            self.assertIsNone(runtime_store.read_global()["active_writer"])

            completed = coordinator.resume_batch(
                batch=batch,
                adapter=adapter,
                native_executor=native,
                continuation_receipt_sha256=partial[
                    "continuation_receipt_sha256"
                ],
                conflict_id=partial["conflict_id"],
                conflicted_capture_id="CAP-EN-B",
                user_option="synthetic-target-b",
                resolved_at="2026-08-21T00:10:00Z",
                owner_id="resume-b",
            )
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(completed["resume_scope"], "conflicted_capture_only")
            self.assertFalse(completed["rerun_analysis_package"])
            self.assertEqual(
                [call["capture_ids"] for call in adapter.calls],
                [captures, ["CAP-EN-B"], ["CAP-EN-B"]],
            )
            self.assertEqual(
                hashlib.sha256((formal / "CAP-EN-A.json").read_bytes()).hexdigest(),
                a_hash,
            )
            self.assertEqual(
                hashlib.sha256((formal / "CAP-EN-C.json").read_bytes()).hexdigest(),
                c_hash,
            )
            self.assertTrue((formal / "CAP-EN-B.json").is_file())
            global_state = runtime_store.read_global()
            self.assertEqual(global_state["formal_write_count"], 3)
            self.assertEqual(global_state["queue"][0]["status"], "committed")

    def test_nonconflict_result_cannot_smuggle_conflict_id(self) -> None:
        batch = {"subject": "cs408", "batch_id": "BATCH-1"}
        native = {
            "capture_results": [],
            "execution_evidence": {
                "pre_state_sha256": "a" * 64,
                "post_state_sha256": "b" * 64,
                "operations": [],
                "adapter_run_id": "run",
                "pid": 1,
                "transaction_id": "tx",
                "ended_at": "2026-08-21T00:00:00Z",
                "stopped_at": "2026-08-21T00:00:01Z",
                "exit_code": 0,
            },
        }
        result = {
            "schema_version": "study-intake-nightly-sol-adapter-result-v1",
            "adapter_name": "CS408NightlySolAdapter",
            "subject": "cs408",
            "batch_id": "BATCH-1",
            "status": "complete",
            "native_receipt": native,
            "native_receipt_sha256": sha256_value(native),
            "conflict_id": "CONFLICT-BAD",
            "formal_write_count": 0,
        }
        with self.assertRaisesRegex(
            NightlySolError, "adapter_conflict_binding_invalid"
        ):
            validate_adapter_result(result, batch=batch)


if __name__ == "__main__":
    unittest.main()
