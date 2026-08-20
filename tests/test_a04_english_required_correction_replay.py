#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from preprocessor_core import (  # noqa: E402
    PreprocessorError,
    StructuredStageResult,
    english_review_semantic_draft,
    materialize_english_correction_deltas,
    mcp_grounding_manifest,
    sha256_value,
    validate_english_applied_corrections,
    validate_english_critical_review,
    validate_english_mcp_grounding,
)
from synthetic_a03_a04_fixture import (  # noqa: E402
    SyntheticA04Fixture,
    build_synthetic_a04_fixture,
)


def _load_synthetic_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256 or path.stem != expected_sha256:
        raise AssertionError(
            f"synthetic fixture SHA drift: {path}: {actual_sha256}"
        )
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise AssertionError(f"synthetic fixture must be an object: {path}")
    return value


def _load_terminal_failure(path: Path, expected_sha256: str) -> dict[str, Any]:
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise AssertionError(
            f"synthetic terminal failure SHA drift: {path}: {actual_sha256}"
        )
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise AssertionError(f"synthetic terminal failure must be an object: {path}")
    return value


def _stage_result(
    output: dict[str, Any],
    transcript: dict[str, Any],
    *,
    transcript_sha256: str,
) -> StructuredStageResult:
    return StructuredStageResult(
        payload=copy.deepcopy(output["payload"]),
        duration_ms=int(output["duration_ms"]),
        runtime_model=output.get("runtime_model"),
        runtime_reasoning_effort=output.get("runtime_reasoning_effort"),
        runtime_metadata_provenance=str(
            output["runtime_metadata_provenance"]
        ),
        runtime_identity_status=str(output["runtime_identity_status"]),
        output_sha256=str(output["output_sha256"]),
        schema_sha256=str(output["schema_sha256"]),
        semantic_stage_count=int(transcript["semantic_stage_count"]),
        provider_request_count=int(transcript["provider_request_count"]),
        mcp_tool_call_count=int(transcript["mcp_tool_call_count"]),
        mcp_transcript_sha256=transcript_sha256,
        mcp_transcript_ref=(
            "study-intake-mcp-stage-transcript://sha256/"
            + transcript_sha256
        ),
        mcp_calls=tuple(copy.deepcopy(transcript["calls"])),
    )


def _first_unresolved_path(payload: dict[str, Any]) -> tuple[str, str]:
    for index, resolution in enumerate(payload["correction_resolutions"]):
        if resolution.get("resolution") == "unresolved":
            return (
                f"$.payload.correction_resolutions[{index}].resolution",
                str(resolution.get("finding_id") or ""),
            )
    raise AssertionError("synthetic A04 critical review has no unresolved row")


class A04EnglishRequiredCorrectionReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture: SyntheticA04Fixture = build_synthetic_a04_fixture()
        cls.addClassCleanup(cls.fixture.cleanup)

    def setUp(self) -> None:
        self.fixture = type(self).fixture
        self.scenario = self.fixture.scenario
        self.analysis_output = _load_synthetic_json(
            self.fixture.analysis_output_path,
            self.scenario["analysis_output_sha256"],
        )
        self.critical_output = _load_synthetic_json(
            self.fixture.critical_output_path,
            self.scenario["critical_output_sha256"],
        )
        self.analysis_raw = _load_synthetic_json(
            self.fixture.analysis_raw_path,
            self.scenario["analysis_raw_sha256"],
        )
        self.critical_raw = _load_synthetic_json(
            self.fixture.critical_raw_path,
            self.scenario["critical_raw_sha256"],
        )
        self.analysis_transcript = _load_synthetic_json(
            self.fixture.analysis_transcript_path,
            self.scenario["analysis_transcript_sha256"],
        )
        self.critical_transcript = _load_synthetic_json(
            self.fixture.critical_transcript_path,
            self.scenario["critical_transcript_sha256"],
        )
        self.checkpoint = _load_synthetic_json(
            self.fixture.checkpoint_path,
            self.scenario["checkpoint_sha256"],
        )

    def test_synthetic_a04_failed_attempt_is_non_evidence_and_recovery_closes(
        self,
    ) -> None:
        self.assertEqual(
            self.scenario["fixture_scope"], "synthetic_contract_only"
        )
        self.assertEqual(
            self.analysis_raw["mcp_item_count"],
            self.scenario["analysis_raw_item_count"],
        )
        self.assertEqual(
            len(self.analysis_transcript["calls"]),
            self.scenario["analysis_tool_call_count"],
        )
        self.assertEqual(
            self.critical_raw["mcp_item_count"],
            self.scenario["critical_raw_item_count"],
        )
        self.assertEqual(
            len(self.critical_transcript["calls"]),
            self.scenario["critical_tool_call_count"],
        )

        failed_rows = [
            row
            for row in self.critical_raw["mcp_items"]
            if row["item"]["result"]["structured_content"]["ok"] is False
        ]
        self.assertEqual(
            len(failed_rows), self.scenario["failed_attempt_count"]
        )
        failed = failed_rows[0]
        self.assertEqual(
            failed["sequence"], self.scenario["failed_attempt_sequence"]
        )
        self.assertEqual(failed["item"]["tool"], "search_records")
        self.assertEqual(
            failed["item"]["arguments"],
            {
                "page_size": 48,
                "query": self.scenario["permanent_query"],
            },
        )
        failed_result = failed["item"]["result"]["structured_content"]
        self.assertEqual(failed_result["error"]["code"], "OUTPUT_LIMIT")
        self.assertEqual(failed_result["items"], [])

        canonical_json = json.dumps(
            self.critical_transcript, ensure_ascii=False, sort_keys=True
        )
        self.assertNotIn(
            self.scenario["failed_request_id"], canonical_json
        )
        self.assertTrue(
            all(call["result"]["ok"] is True
                for call in self.critical_transcript["calls"])
        )
        first_recovered = self.critical_transcript["calls"][
            self.scenario["recovery_sequence"] - 1
        ]
        self.assertEqual(
            first_recovered["sequence"], self.scenario["recovery_sequence"]
        )
        self.assertEqual(first_recovered["tool"], "search_records")
        self.assertEqual(
            first_recovered["arguments"],
            {
                "page_size": 1,
                "query": self.scenario["permanent_query"],
            },
        )
        permanent_calls = [
            call
            for call in self.critical_transcript["calls"]
            if call["tool"] == "search_records"
            and call["arguments"].get("query")
            == self.scenario["permanent_query"]
        ]
        self.assertEqual(
            len(permanent_calls), self.scenario["permanent_recovery_call_count"]
        )
        self.assertIs(permanent_calls[-1]["result"]["complete"], True)
        self.assertIsNone(permanent_calls[-1]["result"]["next_cursor"])
        self.assertEqual(
            self.critical_transcript["coverage"],
            {
                "all_returned_pages_consumed": True,
                "call_count": self.scenario["critical_tool_call_count"],
                "duplicate_argument_count": 0,
                "host_semantic_prefetch": False,
                "unresolved_next_cursors": [],
            },
        )
        for transcript in (
            self.analysis_transcript,
            self.critical_transcript,
        ):
            self.assertEqual(
                transcript["formal_write_count"],
                self.scenario["formal_write_count"],
            )

    def test_synthetic_a04_critical_review_fails_closed_on_required_correction(
        self,
    ) -> None:
        terminal = _load_terminal_failure(
            self.fixture.terminal_failure_path,
            self.scenario["terminal_failure_sha256"],
        )
        self.assertEqual(
            terminal["failure_signature"],
            "english_required_correction_unresolved",
        )
        self.assertEqual(
            terminal["failure_stage"], "synthetic_two_stage_dispatch"
        )
        self.assertIsNone(terminal["first_failure_path"])
        self.assertEqual(
            terminal["formal_write_count"], self.scenario["formal_write_count"]
        )
        self.assertEqual(terminal["sol_status"], "disabled")

        review = copy.deepcopy(self.critical_output["payload"])
        semantic_draft = english_review_semantic_draft(
            self.checkpoint["draft_analysis"]
        )
        self.assertEqual(
            review["draft_analysis_sha256"], sha256_value(semantic_draft)
        )
        self.assertEqual(
            _first_unresolved_path(review),
            (
                "$.payload.correction_resolutions["
                f"{self.scenario['unresolved_resolution_index']}].resolution",
                self.scenario["blocking_finding_id"],
            ),
        )
        self.assertEqual(
            review["findings"][self.scenario["blocking_finding_index"]][
                "correction_id"
            ],
            self.scenario["blocking_finding_id"],
        )
        self.assertEqual(
            review["findings"][self.scenario["blocking_finding_index"]][
                "severity"
            ],
            "blocking",
        )
        self.assertEqual(review["verdict"], "reject")

        effective = materialize_english_correction_deltas(
            review, semantic_draft
        )
        self.assertEqual(
            len(effective["correction_resolutions"]),
            self.scenario["resolution_count"],
        )
        self.assertEqual(
            effective["correction_resolutions"][
                self.scenario["unresolved_resolution_index"]
            ],
            review["correction_resolutions"][
                self.scenario["unresolved_resolution_index"]
            ],
        )
        with self.assertRaises(PreprocessorError) as raised:
            validate_english_critical_review(effective, semantic_draft)
        self.assertEqual(
            raised.exception.code, "english_required_correction_unresolved"
        )
        self.assertIsNotNone(raised.exception.__cause__)
        self.assertEqual(
            str(raised.exception.__cause__), "required_correction_unresolved"
        )

    def test_synthetic_a04_corrected_recovery_closes_corrections_and_grounding(
        self,
    ) -> None:
        semantic_draft = english_review_semantic_draft(
            self.checkpoint["draft_analysis"]
        )
        corrected = copy.deepcopy(self.critical_output["payload"])
        corrected["verdict"] = "revised"
        blocking_id = self.scenario["blocking_finding_id"]
        corrected["findings"] = [
            row
            for row in corrected["findings"]
            if row["correction_id"] != blocking_id
        ]
        corrected["correction_resolutions"] = [
            row
            for row in corrected["correction_resolutions"]
            if row["finding_id"] != blocking_id
        ]

        effective = materialize_english_correction_deltas(
            corrected, semantic_draft
        )
        validate_english_critical_review(effective, semantic_draft)
        validate_english_applied_corrections(effective)
        self.assertEqual(
            len(effective["findings"]), self.scenario["corrected_finding_count"]
        )
        self.assertEqual(
            len(effective["correction_resolutions"]),
            self.scenario["corrected_resolution_count"],
        )
        self.assertTrue(
            all(
                row["resolution"] == "applied"
                for row in effective["correction_resolutions"]
            )
        )

        analysis_stage = _stage_result(
            self.analysis_output,
            self.analysis_transcript,
            transcript_sha256=self.scenario["analysis_transcript_sha256"],
        )
        critical_stage = _stage_result(
            self.critical_output,
            self.critical_transcript,
            transcript_sha256=self.scenario["critical_transcript_sha256"],
        )
        analysis_manifest = mcp_grounding_manifest((analysis_stage,))
        critical_manifest = mcp_grounding_manifest((critical_stage,))
        validate_english_mcp_grounding(
            self.checkpoint["draft_analysis"]["items"],
            grounding_manifest=analysis_manifest,
        )
        validate_english_mcp_grounding(
            effective["revised_items"],
            grounding_manifest=critical_manifest,
        )

        self.assertNotEqual(
            analysis_stage.mcp_transcript_sha256,
            critical_stage.mcp_transcript_sha256,
        )
        self.assertEqual(
            analysis_stage.semantic_stage_count
            + critical_stage.semantic_stage_count,
            self.scenario["semantic_stage_count"] * 2,
        )
        self.assertEqual(
            analysis_stage.provider_request_count
            + critical_stage.provider_request_count,
            sum(self.scenario["provider_request_count"].values()),
        )
        self.assertEqual(
            analysis_stage.mcp_tool_call_count
            + critical_stage.mcp_tool_call_count,
            self.scenario["analysis_tool_call_count"]
            + self.scenario["critical_tool_call_count"],
        )
        for transcript in (
            self.analysis_transcript,
            self.critical_transcript,
        ):
            self.assertTrue(
                transcript["coverage"]["all_returned_pages_consumed"]
            )
            self.assertEqual(
                transcript["coverage"]["unresolved_next_cursors"], []
            )
            self.assertEqual(
                transcript["formal_write_count"],
                self.scenario["formal_write_count"],
            )
        self.assertEqual(
            self.scenario["executed_model_call_count"], 0
        )
        self.assertEqual(
            self.scenario["executed_formal_write_count"], 0
        )


if __name__ == "__main__":
    unittest.main()
