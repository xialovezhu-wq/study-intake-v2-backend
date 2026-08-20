from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from synthetic_a03_a04_fixture import (  # noqa: E402
    SyntheticA03Fixture,
    build_synthetic_a03_fixture,
)


def physical_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class A03EnglishTranscriptReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture: SyntheticA03Fixture = build_synthetic_a03_fixture()
        cls.addClassCleanup(cls.fixture.cleanup)

    def setUp(self) -> None:
        self.fixture = type(self).fixture
        self.host = self.fixture.host
        self.context = self.fixture.context
        self.scenario = self.fixture.scenario

    @staticmethod
    def publication_row(stage_receipt: dict) -> dict:
        grounding = stage_receipt["mcp_grounding_manifest"]
        return {
            "transcript_sha256": stage_receipt["mcp_transcript_sha256"],
            "transcript_ref": stage_receipt["mcp_transcript_ref"],
            "call_receipt_sha256": stage_receipt["mcp_call_receipt_sha256"],
            "call_receipt_ref": stage_receipt["mcp_call_receipt_ref"],
            "grounding_manifest_sha256": stage_receipt[
                "mcp_grounding_manifest_sha256"
            ],
            "grounding_refs": [
                item["evidence_ref"] for item in grounding["items"]
            ],
            "provider_request_count": stage_receipt[
                "provider_request_count"
            ],
            "mcp_tool_call_count": stage_receipt["mcp_tool_call_count"],
        }

    def _reopen(self, stage: str) -> tuple[dict, dict]:
        stage_receipt = self.fixture.stage_receipts[stage]
        return self.host._reopen_stage_transcript(
            subject="english",
            context=self.context,
            stage=stage,
            stage_receipt=stage_receipt,
            publication_row=self.publication_row(stage_receipt),
        )

    def test_synthetic_a03_analysis_transcript_reopens_with_semantic_stage_name(
        self,
    ) -> None:
        stage_receipt = self.fixture.stage_receipts["analysis"]
        transcript_path = self.fixture.transcript_paths["analysis"]
        transcript_sha256 = stage_receipt["mcp_transcript_sha256"]
        self.assertEqual(transcript_path.stem, transcript_sha256)
        self.assertEqual(physical_sha256(transcript_path), transcript_sha256)
        self.assertEqual(
            len(self.fixture.transcripts["analysis"]["calls"]),
            self.scenario["tool_call_count"]["analysis"],
        )
        self.assertEqual(
            stage_receipt["mcp_tool_call_count"],
            self.scenario["tool_call_count"]["analysis"],
        )
        self.assertEqual(
            stage_receipt["provider_request_count"],
            self.scenario["provider_request_count"]["analysis"],
        )
        self.assertEqual(
            self.fixture.call_receipt_paths["analysis"].stem,
            stage_receipt["mcp_call_receipt_sha256"],
        )
        self.assertEqual(
            physical_sha256(self.fixture.call_receipt_paths["analysis"]),
            stage_receipt["mcp_call_receipt_sha256"],
        )
        self.assertEqual(
            stage_receipt["formal_write_count"],
            self.scenario["formal_write_count"],
        )

        transcript, call_receipt = self._reopen("analysis")
        self.assertEqual(transcript["stage_name"], "english_analysis")
        self.assertEqual(
            call_receipt["stage_name"],
            "study-intake-english-luna-analysis-v1",
        )
        self.assertEqual(
            len(transcript["calls"]),
            self.scenario["tool_call_count"]["analysis"],
        )
        self.assertEqual(
            transcript["formal_write_count"],
            self.scenario["formal_write_count"],
        )
        self.assertEqual(
            call_receipt["formal_write_count"],
            self.scenario["formal_write_count"],
        )

    def test_synthetic_a03_critical_transcript_reopens_with_semantic_stage_name(
        self,
    ) -> None:
        stage_receipt = self.fixture.stage_receipts["critical_review"]
        transcript_path = self.fixture.transcript_paths["critical_review"]
        transcript_sha256 = stage_receipt["mcp_transcript_sha256"]
        self.assertEqual(transcript_path.stem, transcript_sha256)
        self.assertEqual(physical_sha256(transcript_path), transcript_sha256)
        self.assertEqual(
            len(self.fixture.transcripts["critical_review"]["calls"]),
            self.scenario["tool_call_count"]["critical_review"],
        )
        self.assertEqual(
            stage_receipt["mcp_tool_call_count"],
            self.scenario["tool_call_count"]["critical_review"],
        )

        reopened, call_receipt = self._reopen("critical_review")
        self.assertEqual(reopened["stage_name"], "english_critical_review")
        self.assertEqual(
            call_receipt["stage_name"],
            "study-intake-english-luna-critical-review-v2",
        )
        self.assertEqual(
            len(reopened["calls"]),
            self.scenario["tool_call_count"]["critical_review"],
        )
        self.assertEqual(
            reopened["formal_write_count"],
            self.scenario["formal_write_count"],
        )
        self.assertEqual(
            call_receipt["formal_write_count"],
            self.scenario["formal_write_count"],
        )


if __name__ == "__main__":
    unittest.main()
