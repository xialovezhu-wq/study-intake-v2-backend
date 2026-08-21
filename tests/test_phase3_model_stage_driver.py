from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from preprocessor_core import (  # noqa: E402
    CodexRunner,
    PreprocessorError,
    StructuredStageResult,
)


def role(model: str) -> dict:
    return {
        "model": model,
        "reasoning_effort": "max",
        "sandbox_mode": "read-only",
        "agents_enabled": False,
    }


class Phase3ModelStageDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = object.__new__(CodexRunner)
        self.runner.config = {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "models": {
                "terra_analysis": role("gpt-5.6-terra"),
                "luna_analysis": role("gpt-5.6-luna"),
                "terra_critical_review": role("gpt-5.6-terra"),
                "orchestrator": {
                    **role("gpt-5.6-terra"),
                    "reasoning_effort": "ultra",
                    "sandbox_mode": "workspace-write",
                    "agents_enabled": True,
                },
            },
        }

    def test_exact_phase3_roles_render_model_and_max_effort(self) -> None:
        expected = {
            "terra_analysis": "gpt-5.6-terra",
            "luna_analysis": "gpt-5.6-luna",
            "terra_critical_review": "gpt-5.6-terra",
        }
        for role_name, model in expected.items():
            with self.subTest(role=role_name):
                self.assertEqual(
                    self.runner._model_request_config_args(role_name),
                    [
                        "--model",
                        model,
                        "--config",
                        'model_reasoning_effort="max"',
                    ],
                )

    def test_orchestrator_or_role_drift_cannot_impersonate_a_model_stage(self) -> None:
        for role_name in ("orchestrator", "missing"):
            with self.subTest(role=role_name), self.assertRaisesRegex(
                PreprocessorError, "consumer_model_role_invalid"
            ):
                self.runner._model_request_config_args(role_name)
        drifted = dict(self.runner.config["models"]["terra_analysis"])
        drifted["sandbox_mode"] = "workspace-write"
        self.runner.config["models"]["terra_analysis"] = drifted
        with self.assertRaisesRegex(
            PreprocessorError, "consumer_model_role_invalid"
        ):
            self.runner._model_request_config_args("terra_analysis")

    def test_stage_receipt_records_explicit_requested_role_identity(self) -> None:
        result = StructuredStageResult(
            payload={"synthetic": True},
            duration_ms=1,
            runtime_model="gpt-5.6-terra",
            runtime_reasoning_effort="max",
            runtime_metadata_provenance="synthetic_attestation",
            runtime_identity_status="confirmed",
            output_sha256="a" * 64,
        )
        receipt = self.runner._stage_receipt(
            result,
            prompt_version="synthetic-phase3-v1",
            prompt_sha256="b" * 64,
            schema_sha256="c" * 64,
            result_sha256="d" * 64,
            requested_model="gpt-5.6-terra",
            requested_reasoning_effort="max",
        )
        self.assertEqual(receipt["requested_model"], "gpt-5.6-terra")
        self.assertEqual(receipt["requested_reasoning_effort"], "max")
        self.assertEqual(receipt["formal_write_count"], 0)

    def test_execute_prompt_keeps_hard_read_only_and_disables_mutation_tools(self) -> None:
        source = inspect.getsource(CodexRunner._execute_prompt)
        for required in (
            '"--sandbox",\n                "read-only"',
            '"features.shell_tool=false"',
            '"features.plugins=false"',
            '"agents.enabled=false"',
            'web_search="disabled"',
        ):
            self.assertIn(required, source)

    def test_analysis_package_uses_tolerant_raw_json_before_strict_canonical_report(self) -> None:
        source = inspect.getsource(CodexRunner.run_analysis_package_v1)
        self.assertIn("enforce_output_schema=False", source)
        self.assertIn("AnalysisPackageDriver", source)

    def test_live_mode_routes_to_analysis_package_driver(self) -> None:
        self.runner.config.update(
            {
                "consumer_stage_chain": {"enabled": True},
                "analysis_package_v1": {"enabled": True},
                "execution_mode": "live_authorized",
            }
        )
        sentinel = object()
        with mock.patch.object(
            self.runner, "run_analysis_package_v1", return_value=sentinel
        ) as routed:
            self.assertIs(self.runner.run(object()), sentinel)
        routed.assert_called_once()

    def test_legacy_live_mode_still_fails_closed_without_new_driver(self) -> None:
        self.runner.config.update(
            {
                "consumer_stage_chain": {"enabled": True},
                "execution_mode": "live_authorized",
            }
        )
        with self.assertRaisesRegex(
            PreprocessorError, "consumer_stage_chain_live_driver_not_integrated"
        ):
            self.runner.run(object())


if __name__ == "__main__":
    unittest.main()
