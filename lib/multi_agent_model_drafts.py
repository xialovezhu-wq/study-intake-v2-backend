"""Strict model-facing drafts and Host-owned read-plan sealing.

Drafts intentionally have no self hashes.  Only the Host may inject runtime
authority and seal an existing ``orchestration_read_plan_v1``.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

from orchestration_plan import READ_TOOLS, seal_read_plan


SUBJECTS = frozenset({"math", "cs408", "english"})
SUBJECT_COLLECTION_ALLOWLISTS = {
    "math": frozenset({
        "formal_card_catalog", "formal_card_records", "knowledge_catalog",
        "math_taxonomy_items", "activity", "search",
    }),
    "cs408": frozenset({
        "formal_wrong_item_catalog", "formal_nodes",
        "formal_knowledge_catalog", "knowledge_nodes",
        "knowledge_safe_notes", "curation_inventory", "morning_sessions",
        "review_events", "search",
    }),
    "english": frozenset({
        "article_catalog", "articles", "sentences", "vocabulary",
        "mastered_items", "patterns", "events", "raw_events",
        "effective_events", "article_learning_catalog",
        "article_learning_pages", "search",
    }),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ModelDraftError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _nonempty_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelDraftError(code)
    return value


def _unique_strings(
    value: Any, code: str, *, nonempty: bool = False
) -> list[str]:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ModelDraftError(code)
    return list(value)


def _binding(value: Mapping[str, Any], *, subject: str, capture_id: str) -> None:
    if value.get("subject") != subject or value.get("capture_id") != capture_id:
        raise ModelDraftError("model_draft_binding_invalid")
    if value.get("formal_write_count") != 0:
        raise ModelDraftError("model_draft_formal_write_nonzero")


def validate_terra_initial_draft(
    value: Mapping[str, Any], *, subject: str, capture_id: str
) -> dict[str, Any]:
    required = {
        "schema_version", "subject", "capture_id", "learning_sections",
        "proposed_branches", "warnings", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ModelDraftError("terra_initial_shape_invalid")
    if value.get("schema_version") != "terra_initial_draft_v1" or subject not in SUBJECTS:
        raise ModelDraftError("terra_initial_binding_invalid")
    _binding(value, subject=subject, capture_id=capture_id)
    sections = value.get("learning_sections")
    if not isinstance(sections, Mapping) or set(sections) != {
        "task_summary", "known_facts", "open_questions"
    }:
        raise ModelDraftError("terra_initial_learning_sections_invalid")
    _nonempty_text(sections.get("task_summary"), "terra_initial_learning_sections_invalid")
    _unique_strings(sections.get("known_facts"), "terra_initial_learning_sections_invalid", nonempty=True)
    _unique_strings(sections.get("open_questions"), "terra_initial_learning_sections_invalid", nonempty=True)
    branches = value.get("proposed_branches")
    if not isinstance(branches, list) or len(branches) not in {3, 4}:
        raise ModelDraftError("terra_initial_branch_count_invalid")
    expected_keys = {
        "branch_id", "purpose", "rationale", "collection_scope",
        "expected_evidence_kinds",
    }
    branch_ids: set[str] = set()
    allowed = SUBJECT_COLLECTION_ALLOWLISTS[subject]
    for branch in branches:
        if not isinstance(branch, Mapping) or set(branch) != expected_keys:
            raise ModelDraftError("terra_initial_branch_shape_invalid")
        branch_id = _nonempty_text(branch.get("branch_id"), "terra_initial_branch_id_invalid")
        if branch_id in branch_ids:
            raise ModelDraftError("terra_initial_branch_id_invalid")
        branch_ids.add(branch_id)
        _nonempty_text(branch.get("purpose"), "terra_initial_branch_content_invalid")
        _nonempty_text(branch.get("rationale"), "terra_initial_branch_content_invalid")
        collections = _unique_strings(
            branch.get("collection_scope"),
            "terra_initial_collection_invalid",
            nonempty=True,
        )
        if not set(collections) <= allowed:
            raise ModelDraftError("terra_initial_collection_forbidden")
        _unique_strings(
            branch.get("expected_evidence_kinds"),
            "terra_initial_evidence_kinds_invalid",
            nonempty=True,
        )
    _unique_strings(value.get("warnings"), "terra_initial_warnings_invalid")
    return copy.deepcopy(dict(value))


def build_sealed_read_plan_from_draft(
    draft: Mapping[str, Any], *, runtime_binding: Mapping[str, Any]
) -> dict[str, Any]:
    """Inject immutable Host policy and seal orchestration_read_plan_v1."""

    subject = runtime_binding.get("subject")
    capture_id = runtime_binding.get("capture_id")
    if subject not in SUBJECTS or not isinstance(capture_id, str) or not capture_id:
        raise ModelDraftError("host_runtime_identity_invalid")
    checked = validate_terra_initial_draft(
        draft, subject=subject, capture_id=capture_id
    )
    required_runtime = {
        "subject", "capture_id", "plan_id", "frozen_task_sha256", "release_id",
        "activation_id", "authority_snapshot_sha256", "generation",
        "orchestrate_skill", "terra_agent_contract_sha256", "created_at",
        "allowed_task_artifact_ids", "allowed_mcp_tools", "query_constraints",
        "maximum_calls", "maximum_records", "maximum_bytes",
        "completion_requirements", "failure_policy",
    }
    if set(runtime_binding) != required_runtime:
        raise ModelDraftError("host_runtime_shape_invalid")
    for key in (
        "frozen_task_sha256", "release_id", "activation_id",
        "authority_snapshot_sha256", "terra_agent_contract_sha256",
    ):
        if not isinstance(runtime_binding.get(key), str) or SHA256_RE.fullmatch(runtime_binding[key]) is None:
            raise ModelDraftError("host_runtime_hash_invalid")
    artifacts = _unique_strings(
        runtime_binding.get("allowed_task_artifact_ids"),
        "host_runtime_artifacts_invalid",
        nonempty=True,
    )
    tools = _unique_strings(
        runtime_binding.get("allowed_mcp_tools"),
        "host_runtime_tools_invalid",
        nonempty=True,
    )
    if not set(tools) <= READ_TOOLS:
        raise ModelDraftError("host_runtime_tools_forbidden")
    completion = _unique_strings(
        runtime_binding.get("completion_requirements"),
        "host_runtime_completion_invalid",
        nonempty=True,
    )
    budgets: dict[str, int] = {}
    for key in ("maximum_calls", "maximum_records", "maximum_bytes"):
        number = runtime_binding.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ModelDraftError("host_runtime_budget_invalid")
        budgets[key] = number
    if runtime_binding.get("failure_policy") != "fail_closed":
        raise ModelDraftError("host_runtime_failure_policy_invalid")
    query_constraints = runtime_binding.get("query_constraints")
    if not isinstance(query_constraints, Mapping):
        raise ModelDraftError("host_runtime_query_constraints_invalid")
    branches = []
    for proposed in checked["proposed_branches"]:
        branches.append(
            {
                "branch_id": proposed["branch_id"],
                "purpose": proposed["purpose"],
                "rationale": proposed["rationale"],
                "required": True,
                "depends_on": [],
                "allowed_task_artifact_ids": copy.deepcopy(artifacts),
                "allowed_mcp_tools": copy.deepcopy(tools),
                "collection_scope": copy.deepcopy(proposed["collection_scope"]),
                "query_constraints": copy.deepcopy(dict(query_constraints)),
                **budgets,
                "completion_requirements": copy.deepcopy(completion),
                "failure_policy": "fail_closed",
                "expected_evidence_kinds": copy.deepcopy(
                    proposed["expected_evidence_kinds"]
                ),
            }
        )
    return seal_read_plan(
        {
            "schema_version": "orchestration_read_plan_v1",
            "plan_id": runtime_binding["plan_id"],
            "subject": subject,
            "capture_id": capture_id,
            "frozen_task_sha256": runtime_binding["frozen_task_sha256"],
            "release_id": runtime_binding["release_id"],
            "activation_id": runtime_binding["activation_id"],
            "authority_snapshot_sha256": runtime_binding["authority_snapshot_sha256"],
            "generation": runtime_binding["generation"],
            "orchestrate_skill": copy.deepcopy(dict(runtime_binding["orchestrate_skill"])),
            "terra_agent_contract_sha256": runtime_binding["terra_agent_contract_sha256"],
            "created_at": runtime_binding["created_at"],
            "branches": branches,
            "formal_write_count": 0,
        }
    )


def validate_luna_investigation_draft(
    value: Mapping[str, Any], *, subject: str, capture_id: str, branch_id: str
) -> dict[str, Any]:
    required = {
        "schema_version", "subject", "capture_id", "branch_id", "summary",
        "findings", "conflicts", "missing_evidence", "confidence",
        "evidence_refs", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ModelDraftError("luna_draft_shape_invalid")
    if value.get("schema_version") != "luna_investigation_draft_v1":
        raise ModelDraftError("luna_draft_schema_invalid")
    _binding(value, subject=subject, capture_id=capture_id)
    if value.get("branch_id") != branch_id:
        raise ModelDraftError("luna_draft_branch_binding_invalid")
    _nonempty_text(value.get("summary"), "luna_draft_summary_invalid")
    for key in ("findings", "conflicts", "missing_evidence"):
        _unique_strings(value.get(key), "luna_draft_content_invalid")
    if value.get("confidence") not in {"low", "medium", "high"}:
        raise ModelDraftError("luna_draft_confidence_invalid")
    _unique_strings(value.get("evidence_refs"), "luna_draft_evidence_invalid", nonempty=True)
    return copy.deepcopy(dict(value))


def validate_terra_final_draft(
    value: Mapping[str, Any], *, subject: str, capture_id: str,
    presented_branches: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    required = {
        "schema_version", "subject", "capture_id", "branch_assessments",
        "subject_sections", "proposals", "conflicts", "gaps", "checklist",
        "warnings", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ModelDraftError("terra_final_draft_shape_invalid")
    if value.get("schema_version") != "terra_final_draft_v1":
        raise ModelDraftError("terra_final_draft_schema_invalid")
    _binding(value, subject=subject, capture_id=capture_id)
    if any(
        not isinstance(row, Mapping)
        or set(row) != {"branch_id", "input_kind"}
        or row.get("input_kind") not in {"report", "diagnostic"}
        for row in presented_branches
    ):
        raise ModelDraftError("terra_final_presented_branches_invalid")
    expected_ids = [row["branch_id"] for row in presented_branches]
    if (
        len(expected_ids) not in {3, 4}
        or any(not isinstance(item, str) or not item for item in expected_ids)
        or len(expected_ids) != len(set(expected_ids))
    ):
        raise ModelDraftError("terra_final_presented_branches_invalid")
    assessments = value.get("branch_assessments")
    if not isinstance(assessments, list) or [
        row.get("branch_id") if isinstance(row, Mapping) else None
        for row in assessments
    ] != expected_ids:
        raise ModelDraftError("terra_final_branch_membership_invalid")
    for row in assessments:
        if set(row) != {
            "branch_id", "input_kind", "disposition", "assessment",
            "evidence_refs",
        }:
            raise ModelDraftError("terra_final_assessment_shape_invalid")
        if row.get("input_kind") not in {"report", "diagnostic"}:
            raise ModelDraftError("terra_final_assessment_kind_invalid")
        if row.get("disposition") not in {
            "adopt", "modify", "reject", "request_more_evidence"
        }:
            raise ModelDraftError("terra_final_assessment_disposition_invalid")
        _nonempty_text(row.get("assessment"), "terra_final_assessment_invalid")
        _unique_strings(row.get("evidence_refs"), "terra_final_assessment_evidence_invalid")
    if [
        {"branch_id": row["branch_id"], "input_kind": row["input_kind"]}
        for row in assessments
    ] != [dict(row) for row in presented_branches]:
        raise ModelDraftError("terra_final_branch_membership_invalid")
    sections = value.get("subject_sections")
    if not isinstance(sections, Mapping) or set(sections) != {
        "learning_summary", "cross_branch_synthesis", "recommended_next_step"
    }:
        raise ModelDraftError("terra_final_sections_invalid")
    for key in sections:
        _nonempty_text(sections[key], "terra_final_sections_invalid")
    for key in ("proposals", "conflicts", "gaps", "checklist", "warnings"):
        _unique_strings(value.get(key), "terra_final_content_invalid")
    return copy.deepcopy(dict(value))


__all__ = [
    "ModelDraftError", "SUBJECT_COLLECTION_ALLOWLISTS",
    "build_sealed_read_plan_from_draft", "validate_luna_investigation_draft",
    "validate_terra_final_draft", "validate_terra_initial_draft",
]
