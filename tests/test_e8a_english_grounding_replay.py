#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from preprocessor_core import (  # noqa: E402
    CodexRunner,
    PreprocessorError,
    StructuredStageResult,
    mcp_grounding_manifest,
    model_mcp_item_ref,
    sha256_value,
    validate_english_mcp_grounding,
)
sys.path.insert(0, str(ROOT / "tests"))

from synthetic_e8a_sealed_fixture import (  # noqa: E402
    SyntheticE8aFixture,
    build_synthetic_e8a_fixture,
)


_COMPAT_E8A_TEMP = tempfile.TemporaryDirectory(
    prefix="synthetic-e8a-compat-"
)
_COMPAT_E8A_FIXTURE = build_synthetic_e8a_fixture(
    Path(_COMPAT_E8A_TEMP.name)
)
# These two names remain as a synthetic compatibility surface for the sealed
# three-subject replay module.  They point only at the temporary builder output
# and are not historical deployment paths.
E8A_TRANSCRIPT_PATH = _COMPAT_E8A_FIXTURE.transcript_path
E8A_TRANSCRIPT_SHA256 = _COMPAT_E8A_FIXTURE.transcript_sha256


def _synthetic_e8a_fixture() -> SyntheticE8aFixture:
    with tempfile.TemporaryDirectory(prefix="synthetic-e8a-replay-") as temp:
        return build_synthetic_e8a_fixture(Path(temp))


def e8a_analysis_stage_result() -> StructuredStageResult:
    """Build the synthetic English Analysis stage without a model call."""

    return _synthetic_e8a_fixture().stage


def e8a_analysis_grounding_fixture(
) -> tuple[StructuredStageResult, dict[str, Any], str, str]:
    """Expose a synthetic stage, manifest, artifact ref, and library ref."""

    fixture = _synthetic_e8a_fixture()
    return (
        fixture.stage,
        fixture.manifest,
        fixture.artifact_ref,
        fixture.library_ref,
    )


def _synthetic_manifest(
    rows: list[tuple[str, str]], *, transcript_sha256: str
) -> dict[str, Any]:
    manifest_rows = [
        {
            "evidence_ref": evidence_ref,
            "subject": "english",
            "generation": "english-test-generation-v1",
            "collection": collection,
            "stable_id": f"TEST-{index}",
            "source_hash": f"{index:064x}",
            "data_role": "test",
            "consumed_in": [
                {
                    "transcript_sha256": transcript_sha256,
                    "call_sequence": index,
                    "result_sha256": f"{index + 100:064x}",
                }
            ],
        }
        for index, (evidence_ref, collection) in enumerate(rows, start=1)
    ]
    core = {
        "schema_version": "model_mcp_grounding_manifest_v1",
        "items": manifest_rows,
        "item_count": len(manifest_rows),
        "host_semantic_prefetch": False,
        "formal_write_count": 0,
    }
    return {**core, "manifest_sha256": sha256_value(core)}


def _item(*refs: str) -> dict[str, Any]:
    return {"grounding": {"mcp_evidence_refs": list(refs)}}


class E8aEnglishGroundingReplayTests(unittest.TestCase):
    def test_e8a_english_analysis_grounding_replay_matches_exact_transcript(
        self,
    ) -> None:
        fixture = _synthetic_e8a_fixture()
        output = fixture.output
        transcript = fixture.transcript
        provider_schema = fixture.provider_schema
        stage = fixture.stage
        manifest = fixture.manifest
        artifact_ref = fixture.artifact_ref
        library_ref = fixture.library_ref

        self.assertEqual(output["payload"], {"items": []})
        self.assertNotIn("minItems", provider_schema["properties"]["items"])
        self.assertEqual(stage.mcp_tool_call_count, len(transcript["calls"]))
        self.assertEqual(
            stage.provider_request_count,
            stage.mcp_tool_call_count + 1,
        )
        self.assertEqual(transcript["formal_write_count"], 0)
        self.assertEqual(transcript["model_call_count"], 1)
        self.assertEqual(transcript["coverage"]["duplicate_argument_count"], 0)
        self.assertTrue(
            all(call["result"]["ok"] is True for call in transcript["calls"])
        )
        self.assertEqual(manifest["item_count"], len(manifest["items"]))
        self.assertEqual(
            Counter(row["collection"] for row in manifest["items"]),
            Counter(fixture.collection_counts),
        )
        self.assertEqual(
            artifact_ref,
            fixture.artifact_ref,
        )
        self.assertTrue(library_ref.startswith("mcp-item:english:"))
        with self.assertRaisesRegex(
            PreprocessorError, "^english_mcp_grounding_missing$"
        ):
            validate_english_mcp_grounding(
                output["payload"]["items"], grounding_manifest=manifest
            )

    def test_exact_artifact_and_library_members_pass(self) -> None:
        _stage, manifest, artifact_ref, library_ref = (
            e8a_analysis_grounding_fixture()
        )
        validate_english_mcp_grounding(
            [_item(artifact_ref, library_ref)], grounding_manifest=manifest
        )

    def test_missing_artifact_library_and_context_only_fail_missing(self) -> None:
        _stage, manifest, artifact_ref, library_ref = (
            e8a_analysis_grounding_fixture()
        )
        context_ref = next(
            row["evidence_ref"]
            for row in manifest["items"]
            if row["collection"] == "task_context"
        )
        for refs in (
            (library_ref,),
            (artifact_ref,),
            (context_ref,),
            (context_ref, artifact_ref),
        ):
            with self.subTest(refs=refs), self.assertRaisesRegex(
                PreprocessorError, "^english_mcp_grounding_missing$"
            ):
                validate_english_mcp_grounding(
                    [_item(*refs)], grounding_manifest=manifest
                )

    def test_nonmember_failed_cross_subject_and_duplicate_refs_are_invalid(
        self,
    ) -> None:
        _stage, manifest, artifact_ref, library_ref = (
            e8a_analysis_grounding_fixture()
        )
        invalid_cases = (
            (artifact_ref, library_ref, "mcp-item:english:" + "0" * 64),
            (artifact_ref, library_ref, "mcp-item:english:" + "f" * 64),
            (artifact_ref, library_ref, "mcp-item:cs408:" + "1" * 64),
            (artifact_ref, artifact_ref, library_ref),
        )
        manifest_refs = {
            row["evidence_ref"] for row in manifest["items"]
        }
        self.assertNotIn(invalid_cases[0][-1], manifest_refs)
        self.assertNotIn(invalid_cases[1][-1], manifest_refs)
        for refs in invalid_cases:
            with self.subTest(refs=refs), self.assertRaisesRegex(
                PreprocessorError, "^english_mcp_grounding_invalid$"
            ):
                validate_english_mcp_grounding(
                    [_item(*refs)], grounding_manifest=manifest
                )

    def test_synthetic_failed_request_is_excluded_from_canonical_grounding(
        self,
    ) -> None:
        fixture = _synthetic_e8a_fixture()
        raw_transport = fixture.raw_transport
        transcript = fixture.transcript
        manifest = fixture.manifest
        artifact_ref = fixture.artifact_ref
        library_ref = fixture.library_ref

        self.assertEqual(
            raw_transport["mcp_item_count"], len(raw_transport["mcp_items"])
        )
        failed = raw_transport["mcp_items"][-1]
        self.assertEqual(failed["sequence"], fixture.failed_sequence)
        self.assertEqual(failed["item"]["tool"], "search_records")
        self.assertEqual(failed["item"]["arguments"], fixture.failed_arguments)
        failed_result = failed["item"]["result"]["structured_content"]
        self.assertIs(failed_result["ok"], False)
        self.assertEqual(failed_result["error"]["code"], "OUTPUT_LIMIT")
        self.assertEqual(failed_result["items"], [])

        failed_request_id = failed_result["request_id"]
        self.assertEqual(len(transcript["calls"]), fixture.stage.mcp_tool_call_count)
        self.assertEqual(
            [call["sequence"] for call in transcript["calls"]],
            list(range(1, len(transcript["calls"]) + 1)),
        )
        self.assertTrue(
            all(call["result"]["ok"] is True for call in transcript["calls"])
        )
        self.assertNotIn(
            failed_request_id,
            json.dumps(transcript, ensure_ascii=False, sort_keys=True),
        )

        would_be_failed_ref = model_mcp_item_ref(
            subject="english",
            generation=str(raw_transport["generation"]),
            collection="search",
            stable_id=f"failed-request:{failed_request_id}",
            source_hash=sha256_value(failed_result),
        )
        self.assertRegex(would_be_failed_ref, r"^mcp-item:english:[0-9a-f]{64}$")
        self.assertNotIn(
            would_be_failed_ref,
            {row["evidence_ref"] for row in manifest["items"]},
        )
        with self.assertRaisesRegex(
            PreprocessorError, "^english_mcp_grounding_invalid$"
        ):
            validate_english_mcp_grounding(
                [_item(artifact_ref, library_ref, would_be_failed_ref)],
                grounding_manifest=manifest,
            )

    def test_manifest_sha_drift_is_invalid(self) -> None:
        _stage, manifest, artifact_ref, library_ref = (
            e8a_analysis_grounding_fixture()
        )
        drifted = copy.deepcopy(manifest)
        drifted["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            PreprocessorError, "^english_mcp_grounding_invalid$"
        ):
            validate_english_mcp_grounding(
                [_item(artifact_ref, library_ref)],
                grounding_manifest=drifted,
            )

    def test_mixed_stage_transcript_manifest_is_invalid(self) -> None:
        _stage, manifest, artifact_ref, library_ref = (
            e8a_analysis_grounding_fixture()
        )
        mixed = copy.deepcopy(manifest)
        mixed["items"][0]["consumed_in"][0]["transcript_sha256"] = "1" * 64
        core = {
            key: value
            for key, value in mixed.items()
            if key != "manifest_sha256"
        }
        mixed["manifest_sha256"] = sha256_value(core)
        with self.assertRaisesRegex(
            PreprocessorError, "^english_mcp_grounding_invalid$"
        ):
            validate_english_mcp_grounding(
                [_item(artifact_ref, library_ref)],
                grounding_manifest=mixed,
            )

    def test_analysis_refs_cannot_substitute_for_fresh_review_refs(self) -> None:
        _stage, analysis_manifest, analysis_artifact, analysis_library = (
            e8a_analysis_grounding_fixture()
        )
        review_artifact = "mcp-item:english:" + "7" * 64
        review_library = "mcp-item:english:" + "8" * 64
        review_manifest = _synthetic_manifest(
            [
                (review_artifact, "task_artifact"),
                (review_library, "search"),
            ],
            transcript_sha256="2" * 64,
        )
        validate_english_mcp_grounding(
            [_item(analysis_artifact, analysis_library)],
            grounding_manifest=analysis_manifest,
        )
        validate_english_mcp_grounding(
            [_item(review_artifact, review_library)],
            grounding_manifest=review_manifest,
        )
        with self.assertRaisesRegex(
            PreprocessorError, "^english_mcp_grounding_invalid$"
        ):
            validate_english_mcp_grounding(
                [_item(analysis_artifact, analysis_library)],
                grounding_manifest=review_manifest,
            )

    def test_private_provider_schema_overlay_requires_one_item(self) -> None:
        event_ids = ("EVT-20260810-ENGLISH-GROUNDING-001",)
        cases = (
            (
                "english_analysis",
                ROOT / "schemas/luna-english-candidate-draft-v1.json",
                "items",
            ),
            (
                "english_critical_review",
                ROOT / "schemas/luna-english-critical-review-v1.json",
                "revised_items",
            ),
        )
        for stage_name, path, items_key in cases:
            static_bytes = path.read_bytes()
            static_schema = json.loads(static_bytes)
            self.assertNotIn(
                "minItems", static_schema["properties"][items_key]
            )
            payload, payload_sha256 = (
                CodexRunner._bound_english_source_event_schema_bytes(
                    path,
                    stage_name=stage_name,
                    allowed_source_event_ids=event_ids,
                )
            )
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), payload_sha256
            )
            self.assertEqual(
                json.loads(payload)["properties"][items_key]["minItems"], 1
            )
            self.assertEqual(path.read_bytes(), static_bytes)


if __name__ == "__main__":
    unittest.main()
