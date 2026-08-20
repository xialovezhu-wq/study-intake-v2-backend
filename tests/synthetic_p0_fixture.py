from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _write_json(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


@dataclass
class SyntheticP0Fixture:
    temporary_directory: tempfile.TemporaryDirectory[str]
    audit_root: Path
    artifact_root: Path
    matrix_path: Path
    golden_path: Path

    def cleanup(self) -> None:
        self.temporary_directory.cleanup()


def build_synthetic_p0_fixture() -> SyntheticP0Fixture:
    """Build a zero-model P0 gate fixture without historical user evidence."""

    temporary_directory = tempfile.TemporaryDirectory(prefix="study-intake-p0-")
    root = Path(temporary_directory.name).resolve()
    audit_root = root / "audit"
    artifact_root = root / "artifacts"
    issue_rows = {
        "math": {
            "issue_id": "MATH-AUDIT-P0-001",
            "title": "Synthetic math closure contract",
        },
        "cs408": {
            "issue_id": "CS408-AUDIT-P0-001",
            "title": "Synthetic cs408 closure contract",
        },
        "english": {
            "issue_id": "EN-P0-006",
            "title": "Synthetic English legacy disposition contract",
        },
    }
    audit_bindings: dict[str, str] = {}
    for subject, issue in issue_rows.items():
        audit_bindings[subject] = _write_json(
            audit_root / subject / "issue-register.json",
            {
                "schema_version": "synthetic-p0-audit-register-v1",
                "subject": subject,
                "issues": [
                    {
                        "issue_id": issue["issue_id"],
                        "title": issue["title"],
                        "priority": "P0",
                        "status": "synthetic_closed",
                    }
                ],
            },
        )

    golden_path = artifact_root / "zero-model-golden-inventory.json"
    golden_sha256 = _write_json(
        golden_path,
        {
            "schema_version": "study-intake-zero-model-golden-inventory-v1",
            "status": "static_fixtures_ready_model_not_run",
            "semantic_assertion_status": "pending_model_replay_after_p0_gate",
            "fixture_scope": "synthetic_contract_only",
            "model_call_count": 0,
            "formal_write_count": 0,
        },
    )

    issues = []
    for subject in ("math", "cs408"):
        issue_id = issue_rows[subject]["issue_id"]
        issues.append(
            {
                "issue_id": issue_id,
                "subject": subject,
                "gate_status": "closed",
                "implementation_state": "synthetic_contract_verified",
                "remaining_action": "none",
                "closure_checks": [
                    {
                        "check_id": f"synthetic-{subject}-closure",
                        "status": "passed",
                        "evidence": "isolated synthetic fixture",
                    }
                ],
                "requires_user_disposition": False,
                "legacy_disposition": None,
            }
        )
    issues.append(
        {
            "issue_id": "EN-P0-006",
            "subject": "english",
            "gate_status": "closed",
            "implementation_state": "execution_closure_verified",
            "remaining_action": "none",
            "closure_checks": [
                {
                    "check_id": "synthetic-english-closure",
                    "status": "passed",
                    "evidence": "isolated synthetic fixture",
                }
            ],
            "requires_user_disposition": True,
            "legacy_disposition": {
                "inventory_status": "complete",
                "identified_target_count": 98,
                "unidentified_target_count": 0,
                "disposition_receipt_count": 98,
                "allowed_dispositions": [
                    "legacy_attestation",
                    "deterministic_recuration",
                    "rollback",
                ],
                "mastered_items_state": {
                    "row_count": 0,
                    "sha256": "0" * 64,
                    "status": "unchanged_no_rows",
                },
            },
        }
    )
    matrix_path = artifact_root / "p0-matrix.json"
    _write_json(
        matrix_path,
        {
            "schema_version": "study-intake-three-subject-p0-matrix-v1",
            "audit_register_sha256": audit_bindings,
            "issues": issues,
            "golden_inventory_sha256": golden_sha256,
            "model_call_count": 0,
            "formal_write_count": 0,
        },
    )
    return SyntheticP0Fixture(
        temporary_directory=temporary_directory,
        audit_root=audit_root,
        artifact_root=artifact_root,
        matrix_path=matrix_path,
        golden_path=golden_path,
    )
