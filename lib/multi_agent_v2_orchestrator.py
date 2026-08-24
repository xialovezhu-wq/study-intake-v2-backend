"""Small host-side primitives for one Multi-Agent V2 Capture orchestration.

The model prompts and report contracts remain owned by ``preprocessor_core``.
This module owns only the concurrency and frozen-authority lifecycle that must
be shared by the three or four independent Luna read sessions.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from read_fanout import ReadFanoutScheduler


class MultiAgentV2OrchestratorError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_SNAPSHOT_KEYS = {
    "authority_snapshot_manifest_path",
    "authority_snapshot_manifest_sha256",
    "authority_snapshot_root",
    "generation",
    "authority_fingerprint",
    "file_count",
    "total_bytes",
    "formal_write_count",
    "authority_snapshot_receipt",
    "authority_snapshot_receipt_sha256",
    "authority_snapshot_receipt_ref",
}


def frozen_authority_snapshot(context: Mapping[str, Any]) -> dict[str, Any]:
    """Recover the single immutable snapshot frozen by the first read session."""

    session = context.get("mcp_read_session")
    receipt = context.get("authority_snapshot_receipt")
    if not isinstance(session, Mapping) or not isinstance(receipt, Mapping):
        raise MultiAgentV2OrchestratorError(
            "multi_agent_authority_snapshot_missing"
        )
    snapshot = {
        "authority_snapshot_manifest_path": session.get(
            "authority_snapshot_manifest_path"
        ),
        "authority_snapshot_manifest_sha256": session.get(
            "authority_snapshot_manifest_sha256"
        ),
        "authority_snapshot_root": session.get("authority_snapshot_root"),
        "generation": receipt.get("generation"),
        "authority_fingerprint": receipt.get("authority_fingerprint"),
        "file_count": receipt.get("file_count"),
        "total_bytes": receipt.get("total_bytes"),
        "formal_write_count": receipt.get("formal_write_count"),
        "authority_snapshot_receipt": copy.deepcopy(dict(receipt)),
        "authority_snapshot_receipt_sha256": context.get(
            "authority_snapshot_receipt_sha256"
        ),
        "authority_snapshot_receipt_ref": context.get(
            "authority_snapshot_receipt_ref"
        ),
    }
    if (
        set(snapshot) != _SNAPSHOT_KEYS
        or snapshot["formal_write_count"] != 0
        or snapshot["generation"] != session.get("generation")
        or snapshot["authority_fingerprint"]
        != session.get("authority_fingerprint")
        or snapshot["authority_snapshot_manifest_sha256"]
        != receipt.get("authority_snapshot_manifest_sha256")
        or snapshot["authority_snapshot_receipt_sha256"]
        != session.get("authority_snapshot_receipt_sha256")
    ):
        raise MultiAgentV2OrchestratorError(
            "multi_agent_authority_snapshot_invalid"
        )
    return copy.deepcopy(snapshot)


def bind_frozen_authority_snapshot(
    processing_host: Any, snapshot: Mapping[str, Any]
) -> None:
    """Make a branch Host reuse the Capture-level snapshot while opening a new session."""

    freezer = getattr(processing_host, "_freeze_authority_snapshot", None)
    if not callable(freezer):
        # Lightweight test hosts may already model a shared immutable snapshot.
        return
    checked = copy.deepcopy(dict(snapshot))
    if set(checked) != _SNAPSHOT_KEYS:
        raise MultiAgentV2OrchestratorError(
            "multi_agent_authority_snapshot_invalid"
        )

    def reuse_snapshot(**kwargs: Any) -> dict[str, Any]:
        if (
            kwargs.get("generation") != checked["generation"]
            or kwargs.get("authority_fingerprint")
            != checked["authority_fingerprint"]
        ):
            raise MultiAgentV2OrchestratorError(
                "multi_agent_authority_snapshot_drift"
            )
        return copy.deepcopy(checked)

    processing_host._freeze_authority_snapshot = reuse_snapshot


def run_capture_fanout(
    *, plan: Mapping[str, Any], worker: Any, physical_branch_slots: int
) -> dict[str, Any]:
    """Run only this Capture's three or four branch slots; no global cap is added."""

    branches = plan.get("branches")
    if (
        not isinstance(branches, list)
        or len(branches) not in {3, 4}
        or physical_branch_slots not in {3, 4}
    ):
        raise MultiAgentV2OrchestratorError(
            "multi_agent_physical_branch_slots_invalid"
        )
    return ReadFanoutScheduler(
        physical_slots=min(physical_branch_slots, len(branches))
    ).run(plan, worker)


def completion_status(branch_results: list[Mapping[str, Any]]) -> str:
    """Technical Luna failures are reportable warnings, including zero success."""

    if not branch_results:
        raise MultiAgentV2OrchestratorError("multi_agent_branch_results_missing")
    return (
        "multi_agent_analysis_package_ready"
        if all(row.get("status") == "succeeded" for row in branch_results)
        else "completed_with_warnings"
    )


__all__ = [
    "MultiAgentV2OrchestratorError",
    "bind_frozen_authority_snapshot",
    "completion_status",
    "frozen_authority_snapshot",
    "run_capture_fanout",
]
