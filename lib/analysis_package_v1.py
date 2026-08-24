"""Read-only compatibility for historical AnalysisPackageV1 objects.

The V1 model driver is retired. This module can only reopen and validate
already-persisted V1 packages and their durable captures.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from analysis_package_store import (
    AnalysisPackageError,
    AnalysisPackageStore as _AnalysisPackageStore,
    SUBJECTS,
    capture_intake_date,
    sha256_value,
    validate_durable_capture,
    validate_sha256,
)


PACKAGE_SCHEMA = "study-intake-analysis-package-v1"
REPORT_SCHEMA = "study-intake-analysis-stage-report-v2"
EXECUTION_RECEIPT_SCHEMA = "study-intake-analysis-stage-execution-receipt-v1"
NORMALIZATION_RECEIPT_SCHEMA = (
    "study-intake-analysis-stage-normalization-receipt-v1"
)
STAGE_ORDER = ("terra_analysis", "luna_analysis", "terra_final")


def _binding(
    row: Mapping[str, Any],
    *,
    digest_key: str,
    ref_key: str,
    prefixes: tuple[str, ...],
) -> None:
    digest = validate_sha256(
        row.get(digest_key), "historical_package_binding_invalid"
    )
    ref = row.get(ref_key)
    if not isinstance(ref, str) or not any(
        ref == prefix + digest for prefix in prefixes
    ):
        raise AnalysisPackageError("historical_package_binding_invalid")


def reopen_analysis_package_v1(
    store: _AnalysisPackageStore, digest: str
) -> dict[str, Any]:
    """Reopen one immutable V1 package without exposing an execution path."""

    package = store.reopen_package(digest)
    required = {
        "schema_version", "package_id", "capture_id", "subject",
        "study_date", "captured_at", "capture_intake_date",
        "capture_sha256", "capture_ref", "stage_order", "stages",
        "warnings", "status", "formal_write_count",
    }
    if (
        set(package) != required
        or package.get("schema_version") != PACKAGE_SCHEMA
        or package.get("subject") not in SUBJECTS
        or package.get("status") != "ready_for_nightly"
        or package.get("formal_write_count") != 0
        or package.get("stage_order") != list(STAGE_ORDER)
        or not isinstance(package.get("warnings"), list)
    ):
        raise AnalysisPackageError("historical_package_invalid")
    capture_sha256 = validate_sha256(
        package.get("capture_sha256"), "historical_capture_binding_invalid"
    )
    if package.get("capture_ref") != (
        "study-intake-durable-capture://sha256/" + capture_sha256
    ):
        raise AnalysisPackageError("historical_capture_binding_invalid")
    capture = validate_durable_capture(store.reopen_capture(capture_sha256))
    if any(
        package.get(key) != capture.get(key)
        for key in (
            "capture_id", "subject", "study_date", "captured_at",
            "capture_intake_date",
        )
    ) or package.get("capture_intake_date") != capture_intake_date(
        package.get("captured_at")
    ):
        raise AnalysisPackageError("historical_capture_binding_invalid")
    stages = package.get("stages")
    if (
        not isinstance(stages, list)
        or len(stages) != len(STAGE_ORDER)
        or [row.get("stage") for row in stages if isinstance(row, Mapping)]
        != list(STAGE_ORDER)
    ):
        raise AnalysisPackageError("historical_package_stage_set_invalid")
    for row in stages:
        if not isinstance(row, Mapping) or row.get("formal_write_count") != 0:
            raise AnalysisPackageError("historical_package_stage_invalid")
        _binding(
            row,
            digest_key="report_sha256",
            ref_key="report_ref",
            prefixes=("study-intake-analysis-stage-report://sha256/",),
        )
        _binding(
            row,
            digest_key="raw_output_sha256",
            ref_key="raw_output_ref",
            prefixes=(
                "study-intake-model-stage-raw-output://sha256/",
                "study-intake-direct-model-stage-raw://sha256/",
            ),
        )
        _binding(
            row,
            digest_key="execution_receipt_sha256",
            ref_key="execution_receipt_ref",
            prefixes=(
                "study-intake-analysis-stage-execution-receipt://sha256/",
            ),
        )
        _binding(
            row,
            digest_key="normalization_receipt_sha256",
            ref_key="normalization_receipt_ref",
            prefixes=(
                "study-intake-analysis-stage-normalization-receipt://sha256/",
            ),
        )
        report = store.reopen_analysis_object(str(row["report_sha256"]))
        execution = store.reopen_execution_receipt(
            str(row["execution_receipt_sha256"])
        )
        normalization = store.reopen_normalization_receipt(
            str(row["normalization_receipt_sha256"])
        )
        stage = str(row["stage"])
        if (
            report.get("schema_version") != REPORT_SCHEMA
            or report.get("stage") != stage
            or report.get("subject") != package["subject"]
            or report.get("capture_id") != package["capture_id"]
            or report.get("formal_write_count") != 0
            or execution.get("schema_version") != EXECUTION_RECEIPT_SCHEMA
            or execution.get("stage") != stage
            or execution.get("subject") != package["subject"]
            or execution.get("capture_id") != package["capture_id"]
            or execution.get("raw_output_sha256")
            != row.get("raw_output_sha256")
            or execution.get("raw_output_ref") != row.get("raw_output_ref")
            or execution.get("executor_receipt_sha256")
            != sha256_value(execution.get("executor_receipt"))
            or execution.get("formal_write_count") != 0
            or normalization.get("schema_version")
            != NORMALIZATION_RECEIPT_SCHEMA
            or normalization.get("stage") != stage
            or normalization.get("subject") != package["subject"]
            or normalization.get("capture_id") != package["capture_id"]
            or normalization.get("execution_receipt_sha256")
            != row.get("execution_receipt_sha256")
            or normalization.get("report_sha256") != row.get("report_sha256")
            or normalization.get("raw_output_sha256")
            != row.get("raw_output_sha256")
            or normalization.get("formal_write_count") != 0
        ):
            raise AnalysisPackageError("historical_stage_object_binding_invalid")
    return copy.deepcopy(package)


__all__ = [
    "AnalysisPackageError", "PACKAGE_SCHEMA", "STAGE_ORDER",
    "reopen_analysis_package_v1",
]
