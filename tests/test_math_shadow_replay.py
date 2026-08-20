from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "lib"))

import math_shadow_backaudit as backaudit  # noqa: E402
import math_shadow_replay as replay  # noqa: E402
from preprocessor_core import sha256_value  # noqa: E402
from tests.test_math_shadow_backaudit import Fixture  # noqa: E402

class MathShadowReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="math-shadow-replay-")
        self.fixture = Fixture(Path(self.temporary.name))
        self.manifest = backaudit.build_manifest(
            self.fixture.runtime,
            self.fixture.repo,
            self.fixture.study_date,
            expected_count=len(self.fixture.capture_ids),
        )
        self.config = {
            "model": {"max_images": 8},
            "math_deep_v2": {"mode": "shadow"},
            "adapters": {
                "math": {
                    "repo_root": str(self.fixture.repo),
                    "adapter_version": "math-pending-v1",
                }
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _candidate(self, item: dict) -> object:
        with (
            mock.patch.object(
                replay,
                "math_processing_contract",
                return_value={"processing_contract_sha256": "c" * 64},
            ),
            mock.patch.object(
                replay, "math_adapter_build_sha256", return_value="d" * 64
            ),
        ):
            return replay.build_replay_candidate(
                manifest=self.manifest,
                item=item,
                config=self.config,
            )

    def test_synthetic_manifest_builds_frozen_shadow_candidates(self) -> None:
        before_runtime = replay._protected_runtime_hashes(self.fixture.runtime)
        before_formal = replay._formal_hashes(
            self.manifest, self.fixture.repo
        )
        manifest = self.manifest
        backaudit.verify_manifest(manifest)
        candidates = [self._candidate(item) for item in manifest["items"]]
        self.assertEqual(
            before_runtime,
            replay._protected_runtime_hashes(self.fixture.runtime),
        )
        self.assertEqual(
            before_formal, replay._formal_hashes(manifest, self.fixture.repo)
        )
        self.assertEqual(len(candidates), len(self.fixture.capture_ids))
        for candidate, item in zip(candidates, manifest["items"]):
            binding = candidate.input_binding
            semantic_binding = dict(binding)
            semantic_binding.pop("loaded_core_sha256")
            self.assertEqual(candidate.input_fingerprint, sha256_value(semantic_binding))
            self.assertEqual(
                binding["backaudit_manifest_sha256"], manifest["manifest_sha256"]
            )
            self.assertEqual(
                binding["replay_input_sha256"], item["replay_input_sha256"]
            )
            self.assertEqual(candidate.canonical_state, "historical_shadow_replay")
            self.assertEqual(candidate.sol_state, "evaluation_only")
            if item["replay_status"] == "limited_by_evidence":
                historical = item["replay_input"]["formal_target"]["historical_card"]
                if historical["status"] == "historical_preimage_unavailable":
                    self.assertIsNone(candidate.model_input["formal_card"])
                    current_sha = item["current_formal_target"].get("sha256")
                    if current_sha and current_sha != historical.get("sha256"):
                        self.assertNotIn(
                            current_sha,
                            json.dumps(candidate.model_input, ensure_ascii=False),
                        )
            self.assertEqual(
                candidate.model_input["historical_replay"]["limitations"],
                [*item["replay_limitations"], "historical_math_knowledge_network_unavailable"],
            )
            coverage = candidate.model_input["knowledge_distribution_snapshot"]["coverage_manifest"]
            self.assertFalse(coverage["network_coverage_complete"])
            self.assertTrue(coverage["post_freeze_current_network_excluded"])

    def test_plan_hash_checks_are_read_only(self) -> None:
        manifest = self.manifest
        runtime = self.fixture.runtime
        repo = self.fixture.repo
        before_runtime = replay._protected_runtime_hashes(runtime)
        before_formal = replay._formal_hashes(manifest, repo)
        self._candidate(manifest["items"][0])
        self.assertEqual(before_runtime, replay._protected_runtime_hashes(runtime))
        self.assertEqual(before_formal, replay._formal_hashes(manifest, repo))


if __name__ == "__main__":
    unittest.main()
