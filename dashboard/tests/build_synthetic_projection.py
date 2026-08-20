#!/usr/bin/env python3
"""Build the Dashboard fixture from synthetic decisions only."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))

from concurrent_dispatch import FrozenTask, REQUIRED_MODEL, REQUIRED_REASONING_EFFORT  # noqa: E402
from dashboard_projection import _counts, update_subject_and_main_projection  # noqa: E402


STUDY_DATE = "2026-08-04"
GENERATED_AT = "2026-08-04T12:00:00+00:00"
RELEASE_ID = "1" * 64


def _decision(subject: str, capture_id: str) -> dict[str, object]:
    fingerprint = hashlib.sha256(capture_id.encode("utf-8")).hexdigest()
    task = FrozenTask(
        {
            "subject": subject,
            "capture_id": capture_id,
            "study_date": STUDY_DATE,
            "input_fingerprint": fingerprint,
            "input_binding": {"capture_id": capture_id},
            "model_input": {"synthetic": True, "subject": subject},
            "allowed_evidence_refs": [],
            "image_paths": [],
            "dispatch_contract": {
                "schema_version": "study-intake-dispatch-release-binding-v1",
                "release_id": RELEASE_ID,
            },
        }
    )
    return {
        "subject": subject,
        "capture_id": capture_id,
        "target_label": (
            "OS_2020_001"
            if subject == "cs408"
            else f"Synthetic {subject} fixture {capture_id}"
        ),
        "study_date": STUDY_DATE,
        "input_fingerprint": fingerprint,
        "eligible": True,
        "reason": "synthetic_portable_fixture",
        "unit_sha256": task.unit_sha256,
        "frozen_payload_sha256": task.frozen_payload_sha256,
        "release_id": RELEASE_ID,
        "rule_version": "study-intake-concurrent-dispatch-contract-v2",
        "phase": "frozen_evidence",
        "model": REQUIRED_MODEL,
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
        "model_enqueue_allowed": True,
    }


def _complete(item: dict[str, object], *, luna_status: str) -> None:
    item.update(
        {
            "queue_state": "ready",
            "current_stage": "quality_ready",
            "local_dispatch_status": "terminal",
            "model_stage": "quality_closed",
            "terminal_status": "succeeded",
            "local_model_submitted": None,
            "luna_status": luna_status,
            "evidence_access_status": "ready",
            "analysis_execution_status": "completed",
            "analysis_report_status": "available",
            "review_execution_status": "completed",
            "review_report_status": "available",
            "sol_review_status": "adopted",
            "formal_write_status": "not_authorized",
            "report_available": True,
            "formal_write_eligible": False,
            "quality_outcome": "accepted",
            "analysis_execution_receipt_sha256": "2" * 64,
            "analysis_raw_output_sha256": "3" * 64,
            "analysis_normalization_receipt_sha256": "4" * 64,
            "analysis_report_sha256": "5" * 64,
            "critical_review_execution_receipt_sha256": "6" * 64,
            "critical_review_raw_output_sha256": "7" * 64,
            "critical_review_normalization_receipt_sha256": "8" * 64,
            "critical_review_report_sha256": "9" * 64,
            "sol_handoff_envelope_sha256": "a" * 64,
            "terminal_receipt_sha256": "b" * 64,
            "package_sha256": "c" * 64,
            "warning_codes": [],
        }
    )


def _shape_fixture_items(projection: dict[str, object]) -> None:
    subjects = projection["subjects"]
    math_items = subjects["math"]["items"]
    cs408_items = subjects["cs408"]["items"]
    math_by_id = {item["capture_id"]: item for item in math_items}
    for capture_id in ("MFI-20260804-001", "MFI-20260804-002"):
        _complete(math_by_id[capture_id], luna_status="ready")
    _complete(cs408_items[0], luna_status="ready")
    math_by_id["MFI-20260804-001"].update(
        {
            "delivery_status": "consumed",
            "adoption_status": "direct_adopted",
            "adoption_receipt_id": "sol-receipt:001",
            "adoption_recorded_at": GENERATED_AT,
            "pipeline_status": "two_pass_ready",
            "runtime_model": "gpt-5.6-luna",
            "runtime_reasoning_effort": "max",
            "stage_receipts": {
                "analysis": {
                    "status": "completed",
                    "runtime_model": "gpt-5.6-luna",
                    "runtime_reasoning_effort": "max",
                },
                "critical_review": {
                    "status": "completed",
                    "runtime_model": "gpt-5.6-luna",
                    "runtime_reasoning_effort": "max",
                },
            },
            "quality_receipt_sha256": "d" * 64,
            "evidence_claim_count": 10,
            "evidence_claims_with_refs": 10,
            "evidence_coverage_pct": 100.0,
            "visual_claim_count": 4,
            "visual_claims_with_refs": 4,
            "visual_coverage_pct": 100.0,
            "visual_evidence_required": True,
        }
    )
    math_by_id["MFI-20260804-002"].update(
        {"delivery_status": "consumed", "adoption_status": "unknown"}
    )
    math_by_id["MFI-20260804-004"].update(
        {
            "queue_state": "running",
            "current_stage": "analysis",
            "local_dispatch_status": "running",
            "model_stage": "analysis",
            "terminal_status": None,
            "local_model_submitted": True,
            "luna_status": "processing",
            "analysis_execution_status": "running",
        }
    )
    for subject, section in subjects.items():
        section["items"].sort(key=lambda item: item["capture_id"])
        section["counts"] = _counts(section["items"])
        if "batch_partition" in section:
            section["batch_partition"]["outside_batch_pending_task_count"] = sum(
                item["local_dispatch_status"] in {"pending", "retrying"}
                for item in section["items"]
            )
        section["enabled"] = True
        section["status"] = "active"
        section["control_state"] = "running"
        section["control_reason"] = "synthetic_fixture_running"
    subjects["math"]["current_stage"] = "analysis"
    subjects["math"]["metrics"] = {
        "review_time_delta_pct": -27.5,
        "critical_errors": 0,
        "unverified_claim": 999,
    }
    subjects["math"]["metric_sources"] = {
        "review_time_delta_pct": "eval-receipt:math-trial-001",
        "critical_errors": "eval-receipt:math-trial-001",
        "unverified_claim": "local-path:/not-public",
    }
    subjects["cs408"]["current_stage"] = "quality_ready"
    for subject, dispatcher in projection["dispatchers"].items():
        dispatcher["status"] = "running"
        dispatcher["enabled"] = True
        dispatcher["paused"] = False
        dispatcher["control_state"] = "running"
        dispatcher["control_reason"] = "synthetic_fixture_running"
        dispatcher["current_stage"] = subjects[subject]["current_stage"]
        dispatcher["runtime_identity_status"] = "requested_unverified"


def _lease_status() -> dict[str, object]:
    return {
        "active_count": 0,
        "stale_count": 0,
        "completed_count": 0,
        "max_fence": 1,
        "draining": False,
        "heartbeat_interval_seconds": 15,
        "lease_ttl_seconds": 120,
    }


def build() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="dashboard-synthetic-") as raw:
        runtime = Path(raw)
        manifest = runtime / "release.json"
        manifest.write_text(
            json.dumps({"release_id": RELEASE_ID}), encoding="utf-8"
        )
        projection_path = runtime / "state" / "dashboard_projection.json"
        config = {
            "runtime_root": str(runtime),
            "timezone": "UTC",
            "release": {"manifest_path": str(manifest)},
            "dashboard": {
                "projection_schema_version": "study-intake-dashboard-projection-v3",
                "projection_path": str(projection_path),
            },
            "dispatch": {
                "production_canary": {"continuous_concurrency_limit": 20}
            },
        }
        captures = {
            "math": [
                "MFI-20260804-001",
                "MFI-20260804-002",
                "MFI-20260804-003",
                "MFI-20260804-004",
            ],
            "cs408": ["CAP-20260804-408001"],
            "english": [],
        }
        projection: dict[str, object] = {}
        for subject in ("math", "cs408", "english"):
            projection = update_subject_and_main_projection(
                config,
                subject,
                study_date=STUDY_DATE,
                daemon_status="running",
                eligible_count=len(captures[subject]),
                submitted_count=0,
                decisions=[
                    _decision(subject, capture_id)
                    for capture_id in captures[subject]
                ],
                lease_status=_lease_status(),
                control_reason="synthetic_fixture_running",
                updated_at=GENERATED_AT,
            )
        _shape_fixture_items(projection)
        projection["global_sol"] = {
            "state_available": True,
            "status": "idle",
            "active_subject": None,
            "active_batch_id": None,
            "current_item": None,
            "authorized_queue": [],
            "fencing_token": None,
            "active_writer_count": 0,
            "committed_count": None,
            "remaining_count": None,
            "formal_write_count": 0,
            "updated_at": GENERATED_AT,
        }
        return projection


def main() -> None:
    print(
        json.dumps(
            build(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
