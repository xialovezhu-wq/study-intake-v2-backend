"""Pure Phase 1 contract for Luna investigation reports and Terra fan-in.

This module intentionally has no production-runtime wiring.  It freezes the
content-addressed object shapes and the cross-object membership invariants.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from orchestration_plan import validate_read_plan


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SUBJECTS = frozenset({"math", "cs408", "english"})
SOL_ACTIONS = ("adopt", "modify", "reject", "request_more_evidence")
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low", "unknown"})
LUNA_REF_PREFIX = "study-intake-luna-investigation-report://sha256/"
TERRA_REF_PREFIX = "study-intake-terra-final-report://sha256/"


class MultiAgentReportContractError(ValueError):
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


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _report_ref(prefix: str, digest: str) -> str:
    return prefix + digest


def validate_dual_report_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the existing read plan plus the Phase 1 3-or-4-reader profile."""

    checked = validate_read_plan(plan)
    branches = checked["branches"]
    if len(branches) not in {3, 4}:
        raise MultiAgentReportContractError("dual_report_branch_count_invalid")
    if any(row["required"] is not True for row in branches):
        raise MultiAgentReportContractError("dual_report_optional_branch_forbidden")
    if any(row["depends_on"] for row in branches):
        raise MultiAgentReportContractError("dual_report_branch_dependency_forbidden")
    return checked


def _validate_common_binding(value: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    if (
        value.get("subject") != plan["subject"]
        or value.get("capture_id") != plan["capture_id"]
        or value.get("plan_sha256") != plan["plan_sha256"]
        or value.get("formal_write_count") != 0
    ):
        raise MultiAgentReportContractError("report_binding_invalid")


def _validate_string_list(value: Any, code: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise MultiAgentReportContractError(code)
    return list(value)


def _validate_read_bundle(
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    branch_results: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    required = {
        "schema_version", "plan_id", "plan_sha256", "subject", "capture_id",
        "frozen_task_sha256", "release_id", "activation_id",
        "authority_snapshot_sha256", "generation", "ordered_branch_results",
        "successful_branch_ids", "failed_branch_ids", "missing_branch_ids",
        "required_failure_branch_ids", "evidence_index", "evidence_membership",
        "deduplicated_evidence_count", "conflict_index",
        "technical_integrity_errors", "coverage_complete", "fanout_telemetry",
        "formal_write_count", "read_bundle_sha256",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("schema_version") != "read_bundle_v1"
        or value.get("plan_id") != plan["plan_id"]
        or value.get("plan_sha256") != plan["plan_sha256"]
        or value.get("subject") != plan["subject"]
        or value.get("capture_id") != plan["capture_id"]
        or value.get("frozen_task_sha256") != plan["frozen_task_sha256"]
        or value.get("release_id") != plan["release_id"]
        or value.get("activation_id") != plan["activation_id"]
        or value.get("authority_snapshot_sha256")
        != plan["authority_snapshot_sha256"]
        or value.get("generation") != plan["generation"]
        or value.get("formal_write_count") != 0
    ):
        raise MultiAgentReportContractError("read_bundle_binding_invalid")
    core = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != "read_bundle_sha256"
    }
    if (
        not _is_sha(value.get("read_bundle_sha256"))
        or value["read_bundle_sha256"] != sha256_value(core)
    ):
        raise MultiAgentReportContractError("read_bundle_digest_invalid")
    ordered = value.get("ordered_branch_results")
    if not isinstance(ordered, list):
        raise MultiAgentReportContractError("read_bundle_membership_invalid")
    if branch_results is not None:
        expected_by_id = {
            row.get("branch_id"): row.get("result_sha256")
            for row in branch_results
            if isinstance(row, Mapping)
        }
        plan_ids = [row["branch_id"] for row in plan["branches"]]
        if (
            len(expected_by_id) != len(branch_results)
            or set(expected_by_id) != set(plan_ids)
            or ordered
            != [
                {
                    "branch_id": branch_id,
                    "result_sha256": expected_by_id[branch_id],
                }
                for branch_id in plan_ids
            ]
        ):
            raise MultiAgentReportContractError("read_bundle_membership_invalid")
    return copy.deepcopy(dict(value))


def build_luna_investigation_report(
    *,
    plan: Mapping[str, Any],
    read_bundle: Mapping[str, Any],
    branch_result: Mapping[str, Any],
    summary: str,
    confidence: str,
) -> dict[str, Any]:
    checked = validate_dual_report_plan(plan)
    branch_id = branch_result.get("branch_id")
    if branch_id not in [row["branch_id"] for row in checked["branches"]]:
        raise MultiAgentReportContractError("luna_report_branch_unknown")
    if branch_result.get("status") != "succeeded":
        raise MultiAgentReportContractError("luna_report_failed_branch_forbidden")
    result_core = {
        key: copy.deepcopy(value)
        for key, value in branch_result.items()
        if key != "result_sha256"
    }
    result_sha = branch_result.get("result_sha256")
    if not _is_sha(result_sha) or result_sha != sha256_value(result_core):
        raise MultiAgentReportContractError("luna_report_branch_result_invalid")
    checked_bundle = _validate_read_bundle(read_bundle, plan=plan)
    if {
        "branch_id": branch_id,
        "result_sha256": result_sha,
    } not in checked_bundle["ordered_branch_results"]:
        raise MultiAgentReportContractError("luna_report_bundle_binding_invalid")
    core = {
        "schema_version": "luna_investigation_report_v1",
        "report_id": f"LUNA-{branch_id}",
        "subject": checked["subject"],
        "capture_id": checked["capture_id"],
        "plan_sha256": checked["plan_sha256"],
        "read_bundle_sha256": checked_bundle["read_bundle_sha256"],
        "branch_id": branch_id,
        "branch_result_sha256": result_sha,
        "purpose": next(
            row["purpose"] for row in checked["branches"]
            if row["branch_id"] == branch_id
        ),
        "summary": summary,
        "mcp_calls": copy.deepcopy(list(branch_result.get("calls") or [])),
        "findings": copy.deepcopy(list(branch_result.get("findings") or [])),
        "conflicts": copy.deepcopy(list(branch_result.get("conflicts") or [])),
        "missing_evidence": copy.deepcopy(
            list(branch_result.get("missing_evidence") or [])
        ),
        "confidence": confidence,
        "evidence_refs": sorted(
            {
                row["evidence_ref"]
                for row in branch_result.get("evidence") or []
                if isinstance(row, Mapping)
                and isinstance(row.get("evidence_ref"), str)
                and row["evidence_ref"]
            }
        ),
        "formal_write_count": 0,
    }
    report = {**core, "report_sha256": sha256_value(core)}
    return validate_luna_investigation_report(
        report, plan=plan, read_bundle=read_bundle, branch_result=branch_result
    )


def validate_luna_investigation_report(
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    read_bundle: Mapping[str, Any],
    branch_result: Mapping[str, Any],
) -> dict[str, Any]:
    checked = validate_dual_report_plan(plan)
    _validate_read_bundle(read_bundle, plan=plan)
    required = {
        "schema_version", "report_id", "subject", "capture_id", "plan_sha256",
        "read_bundle_sha256", "branch_id", "branch_result_sha256", "purpose",
        "summary", "mcp_calls", "findings", "conflicts", "missing_evidence",
        "confidence", "evidence_refs",
        "formal_write_count", "report_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise MultiAgentReportContractError("luna_report_shape_invalid")
    _validate_common_binding(value, checked)
    branch_id = value.get("branch_id")
    if branch_id not in [row["branch_id"] for row in checked["branches"]]:
        raise MultiAgentReportContractError("luna_report_branch_unknown")
    if value.get("report_id") != f"LUNA-{branch_id}":
        raise MultiAgentReportContractError("luna_report_id_invalid")
    branch = next(
        row for row in checked["branches"] if row["branch_id"] == branch_id
    )
    if (
        value.get("schema_version") != "luna_investigation_report_v1"
        or value.get("read_bundle_sha256") != read_bundle.get("read_bundle_sha256")
        or read_bundle.get("plan_sha256") != checked["plan_sha256"]
        or value.get("branch_result_sha256") != branch_result.get("result_sha256")
        or branch_result.get("branch_id") != branch_id
        or branch_result.get("status") != "succeeded"
        or value.get("purpose") != branch["purpose"]
    ):
        raise MultiAgentReportContractError("luna_report_binding_invalid")
    result_core = {key: copy.deepcopy(item) for key, item in branch_result.items() if key != "result_sha256"}
    if branch_result.get("result_sha256") != sha256_value(result_core):
        raise MultiAgentReportContractError("luna_report_branch_result_invalid")
    if not isinstance(value.get("summary"), str) or not value["summary"].strip():
        raise MultiAgentReportContractError("luna_report_summary_invalid")
    expected_content = {
        "mcp_calls": copy.deepcopy(list(branch_result.get("calls") or [])),
        "findings": copy.deepcopy(list(branch_result.get("findings") or [])),
        "conflicts": copy.deepcopy(list(branch_result.get("conflicts") or [])),
        "missing_evidence": copy.deepcopy(
            list(branch_result.get("missing_evidence") or [])
        ),
        "evidence_refs": sorted(
            {
                row["evidence_ref"]
                for row in branch_result.get("evidence") or []
                if isinstance(row, Mapping)
                and isinstance(row.get("evidence_ref"), str)
                and row["evidence_ref"]
            }
        ),
    }
    if not expected_content["mcp_calls"]:
        raise MultiAgentReportContractError("luna_report_mcp_calls_missing")
    for key in ("mcp_calls", "findings", "conflicts", "missing_evidence"):
        if not isinstance(value.get(key), list) or value.get(key) != expected_content[key]:
            raise MultiAgentReportContractError("luna_report_content_invalid")
    if value.get("evidence_refs") != expected_content["evidence_refs"]:
        raise MultiAgentReportContractError("luna_report_evidence_invalid")
    _validate_string_list(value.get("evidence_refs"), "luna_report_evidence_invalid")
    if value.get("confidence") not in CONFIDENCE_LEVELS:
        raise MultiAgentReportContractError("luna_report_confidence_invalid")
    core = {key: copy.deepcopy(item) for key, item in value.items() if key != "report_sha256"}
    if not _is_sha(value.get("report_sha256")) or value["report_sha256"] != sha256_value(core):
        raise MultiAgentReportContractError("luna_report_digest_invalid")
    return copy.deepcopy(dict(value))


def _ordered_luna_reports(
    *,
    plan: Mapping[str, Any],
    read_bundle: Mapping[str, Any],
    branch_results: Sequence[Mapping[str, Any]],
    luna_reports: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    checked = validate_dual_report_plan(plan)
    _validate_read_bundle(
        read_bundle, plan=plan, branch_results=branch_results
    )
    if len(branch_results) != len(checked["branches"]):
        raise MultiAgentReportContractError("luna_report_branch_membership_invalid")
    results = {row.get("branch_id"): row for row in branch_results}
    reports = {row.get("branch_id"): row for row in luna_reports}
    branch_ids = [row["branch_id"] for row in checked["branches"]]
    if (
        len(results) != len(branch_results)
        or len(reports) != len(luna_reports)
        or set(results) != set(branch_ids)
        or set(reports) != set(branch_ids)
    ):
        raise MultiAgentReportContractError("luna_report_branch_membership_invalid")
    return [
        validate_luna_investigation_report(
            reports[branch_id], plan=plan, read_bundle=read_bundle,
            branch_result=results[branch_id]
        )
        for branch_id in branch_ids
    ]


def build_terra_final_input(
    *,
    plan: Mapping[str, Any],
    read_bundle: Mapping[str, Any],
    branch_results: Sequence[Mapping[str, Any]],
    luna_reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ordered = _ordered_luna_reports(
        plan=plan, read_bundle=read_bundle, branch_results=branch_results,
        luna_reports=luna_reports
    )
    return {
        "subject": plan["subject"],
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "read_bundle_sha256": read_bundle["read_bundle_sha256"],
        "luna_reports": copy.deepcopy(ordered),
        "formal_write_count": 0,
    }


def _luna_bindings(reports: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if len(reports) not in {3, 4}:
        raise MultiAgentReportContractError("terra_input_luna_reports_invalid")
    bindings: list[dict[str, str]] = []
    branch_ids: set[str] = set()
    for report in reports:
        if not isinstance(report, Mapping):
            raise MultiAgentReportContractError("terra_input_luna_reports_invalid")
        branch_id = report.get("branch_id")
        digest = report.get("report_sha256")
        core = {
            key: copy.deepcopy(item)
            for key, item in report.items()
            if key != "report_sha256"
        }
        if (
            report.get("schema_version") != "luna_investigation_report_v1"
            or not isinstance(branch_id, str)
            or not branch_id
            or branch_id in branch_ids
            or report.get("formal_write_count") != 0
            or not _is_sha(digest)
            or digest != sha256_value(core)
        ):
            raise MultiAgentReportContractError("terra_input_luna_reports_invalid")
        branch_ids.add(branch_id)
        bindings.append(
            {
                "branch_id": branch_id,
                "report_sha256": digest,
                "report_ref": _report_ref(LUNA_REF_PREFIX, digest),
            }
        )
    return bindings


def build_terra_final_report(
    *, terra_input: Mapping[str, Any], summary: str,
    proposals: Sequence[Mapping[str, Any]] = (), warnings: Sequence[str] = (),
    luna_assessments: Sequence[Mapping[str, Any]] = (),
    conflicts: Sequence[Mapping[str, Any]] = (),
    evidence_gaps: Sequence[str] = (),
    sol_checklist: Sequence[str] = (),
    subject_analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reports = terra_input.get("luna_reports")
    if not isinstance(reports, list) or len(reports) not in {3, 4}:
        raise MultiAgentReportContractError("terra_input_luna_reports_invalid")
    if terra_input.get("formal_write_count") != 0:
        raise MultiAgentReportContractError("terra_input_binding_invalid")
    expected_branch_ids = [str(report["branch_id"]) for report in reports]
    assessments = copy.deepcopy(list(luna_assessments))
    if not assessments:
        assessments = [
            {
                "branch_id": branch_id,
                "disposition": "adopt",
                "rationale": "accepted by Terra final synthesis",
            }
            for branch_id in expected_branch_ids
        ]
    if (
        [row.get("branch_id") for row in assessments if isinstance(row, Mapping)]
        != expected_branch_ids
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"branch_id", "disposition", "rationale"}
            or row.get("disposition") not in {"adopt", "modify", "reject", "uncertain"}
            or not isinstance(row.get("rationale"), str)
            or not row["rationale"].strip()
            for row in assessments
        )
    ):
        raise MultiAgentReportContractError("terra_final_assessments_invalid")
    analysis = copy.deepcopy(dict(subject_analysis or {}))
    if not analysis:
        analysis = {
            "subject": terra_input["subject"],
            "sections": [
                {
                    "kind": "summary",
                    "summary": summary,
                    "details": [],
                    "evidence_refs": [],
                    "confidence": "unknown",
                }
            ],
            "formal_write_count": 0,
        }
    core = {
        "schema_version": "terra_final_report_v1",
        "subject": terra_input["subject"],
        "capture_id": terra_input["capture_id"],
        "plan_sha256": terra_input["plan_sha256"],
        "read_bundle_sha256": terra_input["read_bundle_sha256"],
        "ordered_luna_reports": _luna_bindings(reports),
        "luna_assessments": assessments,
        "summary": summary,
        "subject_analysis": analysis,
        "proposals": copy.deepcopy(list(proposals)),
        "conflicts": copy.deepcopy(list(conflicts)),
        "evidence_gaps": list(evidence_gaps),
        "sol_checklist": list(sol_checklist),
        "warnings": list(warnings),
        "formal_write_count": 0,
    }
    return {**core, "report_sha256": sha256_value(core)}


def validate_terra_final_report(
    value: Mapping[str, Any], *, terra_input: Mapping[str, Any]
) -> dict[str, Any]:
    required = {
        "schema_version", "subject", "capture_id", "plan_sha256",
        "read_bundle_sha256", "ordered_luna_reports", "luna_assessments",
        "summary", "subject_analysis", "proposals", "conflicts",
        "evidence_gaps", "sol_checklist", "warnings", "formal_write_count",
        "report_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise MultiAgentReportContractError("terra_final_shape_invalid")
    expected = {
        key: terra_input.get(key)
        for key in ("subject", "capture_id", "plan_sha256", "read_bundle_sha256")
    }
    if (
        value.get("schema_version") != "terra_final_report_v1"
        or any(value.get(key) != item for key, item in expected.items())
        or value.get("ordered_luna_reports")
        != _luna_bindings(terra_input.get("luna_reports") or [])
        or value.get("formal_write_count") != 0
    ):
        raise MultiAgentReportContractError("terra_final_binding_invalid")
    if not isinstance(value.get("summary"), str) or not value["summary"].strip():
        raise MultiAgentReportContractError("terra_final_summary_invalid")
    if not isinstance(value.get("proposals"), list):
        raise MultiAgentReportContractError("terra_final_proposals_invalid")
    subject_analysis = value.get("subject_analysis")
    sections = (
        subject_analysis.get("sections")
        if isinstance(subject_analysis, Mapping)
        else None
    )
    if (
        not isinstance(subject_analysis, Mapping)
        or set(subject_analysis) != {
            "subject", "sections", "formal_write_count"
        }
        or subject_analysis.get("subject") != value.get("subject")
        or subject_analysis.get("formal_write_count") != 0
        or not isinstance(sections, list)
        or not sections
        or any(
            not isinstance(section, Mapping)
            or set(section) != {
                "kind", "summary", "details", "evidence_refs", "confidence"
            }
            or not isinstance(section.get("kind"), str)
            or not section["kind"]
            or not isinstance(section.get("summary"), str)
            or not section["summary"]
            or not isinstance(section.get("details"), list)
            or any(
                not isinstance(item, str) or not item
                for item in section["details"]
            )
            or not isinstance(section.get("evidence_refs"), list)
            or any(
                not isinstance(item, str) or not item
                for item in section["evidence_refs"]
            )
            or section.get("confidence") not in CONFIDENCE_LEVELS
            for section in sections
        )
    ):
        raise MultiAgentReportContractError("terra_final_subject_analysis_invalid")
    if not isinstance(value.get("conflicts"), list):
        raise MultiAgentReportContractError("terra_final_conflicts_invalid")
    expected_branch_ids = [
        str(report["branch_id"]) for report in terra_input.get("luna_reports") or []
    ]
    assessments = value.get("luna_assessments")
    if (
        not isinstance(assessments, list)
        or [row.get("branch_id") for row in assessments if isinstance(row, Mapping)]
        != expected_branch_ids
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"branch_id", "disposition", "rationale"}
            or row.get("disposition") not in {"adopt", "modify", "reject", "uncertain"}
            or not isinstance(row.get("rationale"), str)
            or not row["rationale"].strip()
            for row in assessments
        )
    ):
        raise MultiAgentReportContractError("terra_final_assessments_invalid")
    _validate_string_list(value.get("evidence_gaps"), "terra_final_gaps_invalid")
    _validate_string_list(value.get("sol_checklist"), "terra_final_checklist_invalid")
    _validate_string_list(value.get("warnings"), "terra_final_warnings_invalid")
    core = {key: copy.deepcopy(item) for key, item in value.items() if key != "report_sha256"}
    if not _is_sha(value.get("report_sha256")) or value["report_sha256"] != sha256_value(core):
        raise MultiAgentReportContractError("terra_final_digest_invalid")
    return copy.deepcopy(dict(value))


def build_sol_handoff_v2(
    *, legacy_handoff: Mapping[str, Any], terra_input: Mapping[str, Any],
    terra_final_report: Mapping[str, Any],
) -> dict[str, Any]:
    terra = validate_terra_final_report(terra_final_report, terra_input=terra_input)
    legacy_required = {
        "schema_version", "subject", "capture_id", "read_bundle_sha256",
        "candidate_sha256", "review_sha256", "risk_report_sha256",
        "sol_review_ready", "diagnostic_review_ready", "quality_clean",
        "allowed_sol_actions", "formal_apply_authorized", "formal_write_count",
        "handoff_sha256",
    }
    if not isinstance(legacy_handoff, Mapping) or set(legacy_handoff) != legacy_required:
        raise MultiAgentReportContractError("sol_handoff_legacy_binding_invalid")
    legacy_core = {
        key: copy.deepcopy(item)
        for key, item in legacy_handoff.items()
        if key != "handoff_sha256"
    }
    candidate_sha = legacy_handoff.get("candidate_sha256")
    boolean_fields = (
        "sol_review_ready", "diagnostic_review_ready", "quality_clean"
    )
    if (
        legacy_handoff.get("schema_version") != "sol_handoff_envelope_v1"
        or not _is_sha(legacy_handoff.get("read_bundle_sha256"))
        or (candidate_sha is not None and not _is_sha(candidate_sha))
        or not _is_sha(legacy_handoff.get("review_sha256"))
        or not _is_sha(legacy_handoff.get("risk_report_sha256"))
        or any(
            not isinstance(legacy_handoff.get(field), bool)
            for field in boolean_fields
        )
        or legacy_handoff.get("allowed_sol_actions")
        != ["adopt", "defer", "modify", "reject", "request_reread"]
        or legacy_handoff.get("formal_apply_authorized") is not False
        or legacy_handoff.get("formal_write_count") != 0
        or not _is_sha(legacy_handoff.get("handoff_sha256"))
        or legacy_handoff["handoff_sha256"] != sha256_value(legacy_core)
    ):
        raise MultiAgentReportContractError("sol_handoff_legacy_binding_invalid")
    copy_keys = legacy_required - {
        "schema_version", "allowed_sol_actions", "handoff_sha256"
    }
    core = {key: copy.deepcopy(legacy_handoff[key]) for key in copy_keys}
    if (
        core["subject"] != terra["subject"]
        or core["capture_id"] != terra["capture_id"]
        or core["read_bundle_sha256"] != terra["read_bundle_sha256"]
        or core["formal_apply_authorized"] is not False
        or core["formal_write_count"] != 0
    ):
        raise MultiAgentReportContractError("sol_handoff_legacy_binding_invalid")
    core.update(
        {
            "schema_version": "sol_handoff_envelope_v2",
            "plan_sha256": terra["plan_sha256"],
            "allowed_sol_actions": list(SOL_ACTIONS),
            "ordered_luna_reports": copy.deepcopy(terra["ordered_luna_reports"]),
            "terra_final_report": {
                "report_sha256": terra["report_sha256"],
                "report_ref": _report_ref(TERRA_REF_PREFIX, terra["report_sha256"]),
            },
        }
    )
    return {**core, "handoff_sha256": sha256_value(core)}


def validate_sol_handoff_v2(
    value: Mapping[str, Any], *, legacy_handoff: Mapping[str, Any],
    terra_input: Mapping[str, Any], terra_final_report: Mapping[str, Any],
) -> dict[str, Any]:
    expected = build_sol_handoff_v2(
        legacy_handoff=legacy_handoff, terra_input=terra_input,
        terra_final_report=terra_final_report
    )
    if value != expected:
        raise MultiAgentReportContractError("sol_handoff_v2_binding_invalid")
    return copy.deepcopy(dict(value))


__all__ = [
    "MultiAgentReportContractError", "build_luna_investigation_report",
    "build_sol_handoff_v2", "build_terra_final_input",
    "build_terra_final_report", "canonical_bytes", "sha256_value",
    "validate_dual_report_plan", "validate_luna_investigation_report",
    "validate_sol_handoff_v2", "validate_terra_final_report",
]
