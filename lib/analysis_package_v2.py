"""Persistence-only Multi-Agent V2 analysis package successor."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from analysis_package_store import (
    AnalysisPackageError,
    AnalysisPackageStore,
    sha256_value,
    validate_durable_capture,
)
from multi_agent_report_contract import (
    _validate_read_bundle,
    validate_dual_report_plan,
    validate_luna_investigation_report,
    validate_luna_investigation_report_v2,
    validate_sol_handoff_v3,
    validate_terra_final_report_v2,
)


PACKAGE_SCHEMA = "study-intake-analysis-package-v2"
TERRA_INITIAL_SCHEMA = "terra_initial_analysis_v1"
TERRA_MODEL = "gpt-5.6-terra"
TERRA_REASONING_EFFORT = "max"
TERRA_EXECUTION_KEYS = {
    "requested_model", "requested_reasoning_effort", "provider_stage_name",
    "raw_output_sha256", "raw_output_ref",
    "stage_execution_receipt_sha256", "stage_execution_receipt_ref",
    "normalization_receipt_sha256", "normalization_receipt_ref",
    "formal_write_count",
}
TERRA_REF_PREFIXES = {
    "raw_output": (
        "study-intake-model-stage-raw-output://sha256/",
        "study-intake-direct-model-stage-raw://sha256/",
    ),
    "stage_execution_receipt": (
        "study-intake-model-stage-execution://sha256/",
        "study-intake-direct-model-stage-execution://sha256/",
    ),
    "normalization_receipt": (
        "study-intake-model-stage-normalization://sha256/",
        "study-intake-direct-model-stage-normalization://sha256/",
    ),
}


def _binding(*, kind: str, schema: str, digest: str, ref: str) -> dict[str, str]:
    return {
        "kind": kind,
        "schema_version": schema,
        "sha256": digest,
        "ref": ref,
    }


def _validate_terra_execution(
    value: Mapping[str, Any], *, subject: str, phase: str
) -> dict[str, Any]:
    expected_stage = {
        "initial": f"{subject}_analysis",
        "final": f"{subject}_critical_review",
    }.get(phase)
    if (
        expected_stage is None
        or not isinstance(value, Mapping)
        or set(value) != TERRA_EXECUTION_KEYS
        or value.get("requested_model") != TERRA_MODEL
        or value.get("requested_reasoning_effort") != TERRA_REASONING_EFFORT
        or value.get("provider_stage_name") != expected_stage
        or value.get("formal_write_count") != 0
    ):
        raise AnalysisPackageError("terra_execution_binding_invalid")
    checked = copy.deepcopy(dict(value))
    for stem, prefixes in TERRA_REF_PREFIXES.items():
        digest = checked.get(f"{stem}_sha256")
        ref = checked.get(f"{stem}_ref")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(ref, str)
            or not any(ref == prefix + digest for prefix in prefixes)
        ):
            raise AnalysisPackageError("terra_execution_artifact_invalid")
    return checked


def build_terra_initial_analysis(
    *, capture: Mapping[str, Any], summary: str, analysis: Mapping[str, Any],
    proposed_branches: Sequence[Mapping[str, Any]],
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    checked = validate_durable_capture(capture)
    branches = [copy.deepcopy(dict(row)) for row in proposed_branches if isinstance(row, Mapping)]
    sections = analysis.get("sections") if isinstance(analysis, Mapping) else None
    if (
        not isinstance(summary, str) or not summary.strip()
        or not isinstance(sections, list) or not sections
        or any(
            not isinstance(section, Mapping)
            or set(section) != {"kind", "summary", "evidence_refs"}
            or not isinstance(section.get("kind"), str) or not section["kind"]
            or not isinstance(section.get("summary"), str) or not section["summary"]
            or not isinstance(section.get("evidence_refs"), list)
            or any(not isinstance(ref, str) or not ref for ref in section["evidence_refs"])
            for section in sections
        )
        or len(branches) not in {3, 4} or len(branches) != len(proposed_branches)
        or any(
            set(row) != {"branch_id", "purpose", "rationale"}
            or not all(isinstance(row[key], str) and row[key] for key in row)
            for row in branches
        )
        or len({row["branch_id"] for row in branches}) != len(branches)
    ):
        raise AnalysisPackageError("terra_initial_analysis_invalid")
    core = {
        "schema_version": TERRA_INITIAL_SCHEMA,
        "subject": checked["subject"], "capture_id": checked["capture_id"],
        "frozen_capture_sha256": sha256_value(checked),
        "summary": summary,
        "analysis": {"sections": copy.deepcopy(sections)},
        "proposed_branches": branches,
        "evidence_refs": list(evidence_refs),
        "formal_write_count": 0,
    }
    return {**core, "report_sha256": sha256_value(core)}


def _validate_terra_initial(
    value: Mapping[str, Any], *, capture: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    expected_keys = {
        "schema_version", "subject", "capture_id", "frozen_capture_sha256",
        "summary", "analysis", "proposed_branches", "evidence_refs",
        "formal_write_count", "report_sha256",
    }
    core = {key: copy.deepcopy(item) for key, item in value.items() if key != "report_sha256"}
    if (
        not isinstance(value, Mapping) or set(value) != expected_keys
        or value.get("schema_version") != TERRA_INITIAL_SCHEMA
        or value.get("subject") != capture.get("subject")
        or value.get("capture_id") != capture.get("capture_id")
        or value.get("frozen_capture_sha256") != sha256_value(capture)
        or not isinstance(value.get("summary"), str) or not value["summary"].strip()
        or not isinstance(value.get("analysis"), Mapping)
        or not isinstance(value.get("evidence_refs"), list)
        or any(not isinstance(item, str) or not item for item in value["evidence_refs"])
        or value.get("formal_write_count") != 0
        or value.get("report_sha256") != sha256_value(core)
    ):
        raise AnalysisPackageError("terra_initial_analysis_invalid")
    proposed = value.get("proposed_branches")
    if (
        not isinstance(proposed, list)
        or [row.get("branch_id") for row in proposed]
        != [row.get("branch_id") for row in plan.get("branches", [])]
        or any(
            row.get("purpose") != branch.get("purpose")
            or row.get("rationale") != branch.get("rationale")
            for row, branch in zip(proposed, plan.get("branches", []))
        )
    ):
        raise AnalysisPackageError("terra_initial_plan_binding_invalid")
    return copy.deepcopy(dict(value))


def _diagnostic_valid(value: Mapping[str, Any], *, plan: Mapping[str, Any]) -> bool:
    core = {key: copy.deepcopy(item) for key, item in value.items() if key != "result_sha256"}
    return bool(
        value.get("schema_version") == "read_branch_result_v1"
        and value.get("plan_sha256") == plan.get("plan_sha256")
        and value.get("subject") == plan.get("subject")
        and value.get("capture_id") == plan.get("capture_id")
        and value.get("status") in {"failed", "cancelled", "timed_out"}
        and value.get("formal_write_count") == 0
        and value.get("result_sha256") == sha256_value(core)
    )


def publish_analysis_package_v2(
    store: AnalysisPackageStore, *, capture: Mapping[str, Any],
    plan: Mapping[str, Any], read_bundle: Mapping[str, Any],
    branch_results: Sequence[Mapping[str, Any]],
    terra_initial: Mapping[str, Any],
    terra_initial_execution: Mapping[str, Any],
    luna_outputs: Sequence[Mapping[str, Any]],
    terra_final: Mapping[str, Any],
    terra_final_execution: Mapping[str, Any],
    sol_handoff: Mapping[str, Any],
) -> dict[str, Any]:
    checked_capture = validate_durable_capture(capture)
    checked_plan = validate_dual_report_plan(plan)
    checked_bundle = _validate_read_bundle(
        read_bundle, plan=plan, branch_results=branch_results
    )
    if (
        checked_capture["subject"] != checked_plan["subject"]
        or checked_capture["capture_id"] != checked_plan["capture_id"]
        or checked_bundle.get("read_bundle_sha256") is None
    ):
        raise AnalysisPackageError("analysis_package_v2_binding_invalid")
    initial = _validate_terra_initial(terra_initial, capture=checked_capture, plan=plan)
    initial_execution = _validate_terra_execution(
        terra_initial_execution, subject=checked_capture["subject"], phase="initial"
    )
    final_execution = _validate_terra_execution(
        terra_final_execution, subject=checked_capture["subject"], phase="final"
    )
    results = {row.get("branch_id"): row for row in branch_results}
    outputs = {row.get("branch_id"): row for row in luna_outputs}
    branch_ids = [row["branch_id"] for row in checked_plan["branches"]]
    if len(branch_ids) not in {3, 4} or set(results) != set(branch_ids) or set(outputs) != set(branch_ids):
        raise AnalysisPackageError("analysis_package_v2_luna_set_invalid")

    capture_sha, capture_ref = store.publish_capture(checked_capture)
    plan_sha, plan_ref = store.publish_analysis_object(
        plan, ref_prefix="study-intake-orchestration-read-plan"
    )
    bundle_sha, bundle_ref = store.publish_analysis_object(
        checked_bundle, ref_prefix="study-intake-read-bundle"
    )
    initial_sha, initial_ref = store.publish_analysis_object(
        initial, ref_prefix="study-intake-terra-initial-analysis"
    )
    output_bindings: list[dict[str, Any]] = []
    for branch_id in branch_ids:
        result = results[branch_id]
        output = outputs[branch_id]
        if output.get("schema_version") in {
            "luna_investigation_report_v1", "luna_investigation_report_v2"
        }:
            checked_output = (
                validate_luna_investigation_report_v2(
                    output, plan=plan, read_bundle=read_bundle,
                    branch_result=result,
                )
                if output.get("schema_version") == "luna_investigation_report_v2"
                else validate_luna_investigation_report(
                    output, plan=plan, read_bundle=read_bundle,
                    branch_result=result,
                )
            )
            kind = "investigation_report"
        elif _diagnostic_valid(output, plan=plan) and output.get("branch_id") == branch_id:
            checked_output = copy.deepcopy(dict(output))
            kind = "diagnostic_record"
        else:
            raise AnalysisPackageError("analysis_package_v2_luna_output_invalid")
        result_digest, result_ref = store.publish_analysis_object(
            result, ref_prefix="study-intake-read-branch-result"
        )
        digest, ref = store.publish_analysis_object(
            checked_output,
            ref_prefix=(
                "study-intake-luna-investigation-report"
                if kind == "investigation_report"
                else "study-intake-luna-diagnostic-record"
            ),
        )
        output_bindings.append({
            "branch_id": branch_id,
            **_binding(kind=kind, schema=str(checked_output["schema_version"]), digest=digest, ref=ref),
            "report_sha256": str(
                checked_output.get("report_sha256")
                or checked_output.get("result_sha256")
            ),
            "branch_result_sha256": result_digest,
            "branch_result_ref": result_ref,
            **(
                {"execution_artifacts": copy.deepcopy(
                    checked_output["execution_artifacts"]
                )}
                if checked_output.get("schema_version")
                == "luna_investigation_report_v2"
                else {}
            ),
        })

    report_bindings = [row for row in output_bindings if row["kind"] == "investigation_report"]
    diagnostic_bindings = [row for row in output_bindings if row["kind"] == "diagnostic_record"]
    if not report_bindings:
        raise AnalysisPackageError("analysis_package_v2_success_missing")
    expected_luna = [
        {
            "branch_id": row["branch_id"],
            "report_sha256": row["report_sha256"],
            "report_ref": "study-intake-luna-investigation-report://sha256/"
            + row["report_sha256"],
        }
        for row in report_bindings
    ]
    expected_diagnostics = [
        {
            "branch_id": row["branch_id"],
            "diagnostic_sha256": row["report_sha256"],
            "diagnostic_ref": "study-intake-luna-diagnostic-record://sha256/"
            + row["report_sha256"],
            "status": str(outputs[row["branch_id"]]["status"]),
        }
        for row in diagnostic_bindings
    ]
    final = copy.deepcopy(dict(terra_final))
    final_core = {key: copy.deepcopy(item) for key, item in final.items() if key != "report_sha256"}
    successor_path = bool(diagnostic_bindings) or any(
        row["schema_version"] == "luna_investigation_report_v2"
        for row in report_bindings
    )
    if successor_path and any(
        row["schema_version"] != "luna_investigation_report_v2"
        for row in report_bindings
    ):
        raise AnalysisPackageError("analysis_package_v2_execution_artifacts_missing")
    if successor_path:
        validate_terra_final_report_v2(final, terra_input={
            "subject": checked_plan["subject"],
            "capture_id": checked_plan["capture_id"],
            "plan_sha256": checked_plan["plan_sha256"],
            "read_bundle_sha256": checked_bundle["read_bundle_sha256"],
            "branch_coverage": [
                {"branch_id": row["branch_id"], "outcome": (
                    "report" if row["kind"] == "investigation_report"
                    else "diagnostic"
                )} for row in output_bindings
            ],
            "luna_reports": [outputs[row["branch_id"]] for row in report_bindings],
            "luna_diagnostics": [outputs[row["branch_id"]] for row in diagnostic_bindings],
            "formal_write_count": 0,
        })
    elif (
        final.get("schema_version") != "terra_final_report_v1"
        or final.get("subject") != checked_plan["subject"]
        or final.get("capture_id") != checked_plan["capture_id"]
        or final.get("plan_sha256") != checked_plan["plan_sha256"]
        or final.get("read_bundle_sha256") != read_bundle["read_bundle_sha256"]
        or final.get("ordered_luna_reports") != expected_luna
        or final.get("formal_write_count") != 0
        or final.get("report_sha256") != sha256_value(final_core)
    ):
        raise AnalysisPackageError("analysis_package_v2_terra_final_invalid")
    handoff = copy.deepcopy(dict(sol_handoff))
    handoff_core = {key: copy.deepcopy(item) for key, item in handoff.items() if key != "handoff_sha256"}
    if successor_path:
        if handoff.get("schema_version") != "sol_handoff_envelope_v3":
            raise AnalysisPackageError("analysis_package_v2_handoff_invalid")
    elif handoff.get("schema_version") != "sol_handoff_envelope_v2":
        raise AnalysisPackageError("analysis_package_v2_handoff_invalid")
    if (
        handoff.get("ordered_luna_reports") != expected_luna
        or handoff.get("ordered_luna_diagnostics", []) != expected_diagnostics
        or handoff.get("terra_final_report", {}).get("report_sha256") != final["report_sha256"]
        or handoff.get("formal_apply_authorized") is not False
        or handoff.get("formal_write_count") != 0
        or handoff.get("handoff_sha256") != sha256_value(handoff_core)
    ):
        raise AnalysisPackageError("analysis_package_v2_handoff_invalid")
    final_sha, final_ref = store.publish_analysis_object(
        final, ref_prefix="study-intake-terra-final-report"
    )
    handoff_sha, handoff_ref = store.publish_analysis_object(
        handoff, ref_prefix="study-intake-sol-handoff-envelope"
    )
    core = {
        "schema_version": PACKAGE_SCHEMA,
        "package_id": "ANPKG2-" + sha256_value({"capture": capture_sha, "outputs": output_bindings})[:24].upper(),
        "capture_id": checked_capture["capture_id"], "subject": checked_capture["subject"],
        "study_date": checked_capture["study_date"], "captured_at": checked_capture["captured_at"],
        "capture_intake_date": checked_capture["capture_intake_date"],
        "capture": _binding(kind="durable_capture", schema=str(checked_capture["schema_version"]), digest=capture_sha, ref=capture_ref),
        "plan": _binding(kind="sealed_plan", schema=str(checked_plan["schema_version"]), digest=plan_sha, ref=plan_ref),
        "read_bundle": _binding(kind="read_bundle", schema=str(read_bundle["schema_version"]), digest=bundle_sha, ref=bundle_ref),
        "terra_initial": _binding(kind="terra_initial", schema=TERRA_INITIAL_SCHEMA, digest=initial_sha, ref=initial_ref),
        "terra_initial_execution": initial_execution,
        "luna_outputs": output_bindings,
        "ordered_luna_reports": report_bindings,
        "ordered_luna_diagnostics": diagnostic_bindings,
        "terra_final": _binding(kind="terra_final", schema=str(final["schema_version"]), digest=final_sha, ref=final_ref),
        "terra_final_execution": final_execution,
        "sol_handoff": _binding(kind="sol_handoff", schema=str(handoff["schema_version"]), digest=handoff_sha, ref=handoff_ref),
        "status": "ready_for_nightly", "formal_write_count": 0,
    }
    package_sha, package_ref = store.publish_package(core)
    return {**core, "package_sha256": package_sha, "package_ref": package_ref}


def reopen_analysis_package_v2(store: AnalysisPackageStore, digest: str) -> dict[str, Any]:
    package = store.reopen_package(digest)
    expected_package_keys = {
        "schema_version", "package_id", "capture_id", "subject", "study_date",
        "captured_at", "capture_intake_date", "capture", "plan", "read_bundle",
        "terra_initial", "terra_initial_execution", "luna_outputs",
        "ordered_luna_reports", "ordered_luna_diagnostics", "terra_final",
        "terra_final_execution", "sol_handoff", "status", "formal_write_count",
    }
    if (
        set(package) != expected_package_keys
        or package.get("schema_version") != PACKAGE_SCHEMA
        or package.get("status") != "ready_for_nightly"
        or package.get("formal_write_count") != 0
    ):
        raise AnalysisPackageError("analysis_package_v2_invalid")
    subject = package.get("subject")
    if subject not in {"math", "cs408", "english"}:
        raise AnalysisPackageError("analysis_package_v2_invalid")
    _validate_terra_execution(
        package.get("terra_initial_execution"), subject=str(subject), phase="initial"
    )
    _validate_terra_execution(
        package.get("terra_final_execution"), subject=str(subject), phase="final"
    )
    for key in ("capture", "plan", "read_bundle", "terra_initial", "terra_final", "sol_handoff"):
        binding = package.get(key)
        if not isinstance(binding, Mapping):
            raise AnalysisPackageError("analysis_package_v2_binding_invalid")
        reopened = (
            store.reopen_capture(binding["sha256"])
            if key == "capture"
            else store.reopen_analysis_object(binding["sha256"])
        )
        if reopened.get("schema_version") != binding.get("schema_version"):
            raise AnalysisPackageError("analysis_package_v2_binding_invalid")
    outputs = package.get("luna_outputs")
    if not isinstance(outputs, list) or len(outputs) not in {3, 4}:
        raise AnalysisPackageError("analysis_package_v2_luna_set_invalid")
    for row in outputs:
        reopened = store.reopen_analysis_object(row["sha256"])
        if reopened.get("branch_id") != row.get("branch_id") or reopened.get("schema_version") != row.get("schema_version"):
            raise AnalysisPackageError("analysis_package_v2_luna_set_invalid")
        if row.get("kind") == "investigation_report" and (
            row.get("execution_artifacts")
            != reopened.get("execution_artifacts")
        ):
            # Historical all-success v1 reports have neither field.  The
            # successor v2 report must match the package binding exactly.
            if row.get("schema_version") != "luna_investigation_report_v1":
                raise AnalysisPackageError(
                    "analysis_package_v2_execution_artifacts_invalid"
                )
        result = store.reopen_analysis_object(row["branch_result_sha256"])
        if result.get("branch_id") != row.get("branch_id"):
            raise AnalysisPackageError("analysis_package_v2_luna_set_invalid")
    if package.get("ordered_luna_reports") != [row for row in outputs if row.get("kind") == "investigation_report"] or package.get("ordered_luna_diagnostics") != [row for row in outputs if row.get("kind") == "diagnostic_record"]:
        raise AnalysisPackageError("analysis_package_v2_luna_set_invalid")
    return package
