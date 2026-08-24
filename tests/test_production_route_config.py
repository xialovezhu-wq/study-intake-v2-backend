from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.portable_plugin_fixture import build_portable_plugin_fixture


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from preprocessor_core import PreprocessorError, load_config  # noqa: E402


def _substitute(value: object, replacements: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {
            key: _substitute(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_substitute(item, replacements) for item in value]
    if isinstance(value, str):
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
    return value


class ProductionRouteConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="production-route-config-"
        )
        self.base = Path(self.temp.name)
        self.portable = build_portable_plugin_fixture(self.base, ROOT)
        runtime_root = self.base / "runtime"
        template = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )
        self.config = _substitute(
            template,
            {
                "${RELEASE_ROOT}": str(ROOT),
                "${RUNTIME_DATA_ROOT}": str(runtime_root),
                "${CODEX_EXECUTABLE}": sys.executable,
                "${PYTHON_EXECUTABLE}": sys.executable,
                "${MCP_PYTHON_EXECUTABLE}": str(self.portable.mcp_python),
                "${MCP_ROOT}": str(self.portable.mcp_root),
                "${MATH_ROOT}": str(self.portable.subject_roots["math"]),
                "${CS408_ROOT}": str(self.portable.subject_roots["cs408"]),
                "${ENGLISH_ROOT}": str(self.portable.subject_roots["english"]),
            },
        )
        self.config["processing_plugin"].update(
            {
                "root": str(self.portable.plugin_root),
                "component_lock_path": str(
                    self.portable.plugin_root / "component-lock.json"
                ),
                "mcp_client_python": str(self.portable.mcp_python),
                "mcp_project_root": str(self.portable.mcp_root),
            }
        )
        self.path = self.base / "config.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _load(self, value: dict) -> dict:
        self.path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return load_config(self.path)

    def test_live_config_requires_v2_and_package_v4(self) -> None:
        loaded = self._load(copy.deepcopy(self.config))
        self.assertTrue(loaded["analysis_package_v2"]["enabled"])
        self.assertEqual(
            loaded["live_execution_gate"]["authorization_kind"],
            "task_execution_proof_v1",
        )
        for profile_name in (
            "math_deep_v2",
            "cs408_deep_v2",
            "english_two_pass_v1",
        ):
            self.assertTrue(
                loaded[profile_name]["package_output_schema"].endswith(
                    "preprocess-package-v4.json"
                )
            )

    def test_live_config_rejects_retired_activation_items(self) -> None:
        for key in ("consumer_stage_chain", "analysis_package_v1"):
            with self.subTest(key=key):
                invalid = copy.deepcopy(self.config)
                invalid[key] = {"enabled": True}
                with self.assertRaisesRegex(
                    PreprocessorError, "config_retired_analysis_route_present"
                ):
                    self._load(invalid)

    def test_live_config_without_v2_fails_closed(self) -> None:
        invalid = copy.deepcopy(self.config)
        invalid.pop("analysis_package_v2")
        for profile_name in (
            "math_deep_v2",
            "cs408_deep_v2",
            "english_two_pass_v1",
        ):
            invalid[profile_name]["package_output_schema"] = str(
                ROOT / "schemas/preprocess-package-v3.json"
            )
        with self.assertRaisesRegex(
            PreprocessorError, "config_analysis_package_v2_required"
        ):
            self._load(invalid)

    def test_v2_config_rejects_package_v3(self) -> None:
        invalid = copy.deepcopy(self.config)
        invalid["math_deep_v2"]["package_output_schema"] = str(
            ROOT / "schemas/preprocess-package-v3.json"
        )
        with self.assertRaisesRegex(
            PreprocessorError, "config_math_package_output_schema_not_v4"
        ):
            self._load(invalid)

    def test_historical_fixture_without_v2_keeps_package_v3_validation(
        self,
    ) -> None:
        historical = copy.deepcopy(self.config)
        historical["execution_mode"] = "fixture"
        historical.pop("analysis_package_v2")
        for profile_name in (
            "math_deep_v2",
            "cs408_deep_v2",
            "english_two_pass_v1",
        ):
            historical[profile_name]["package_output_schema"] = str(
                ROOT / "schemas/preprocess-package-v3.json"
            )
        loaded = self._load(historical)
        self.assertNotIn("analysis_package_v2", loaded)

    def test_v1_execution_symbols_are_physically_retired(self) -> None:
        core = (ROOT / "lib/preprocessor_core.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("def _run_v1(", core)
        self.assertNotIn("def _analysis_package_prompt(", core)
        self.assertFalse((ROOT / "lib/analysis_package_v1.py").exists())
        self.assertFalse(
            (ROOT / "lib/manual_capture_admission.py").exists()
        )
        self.assertTrue(
            (
                ROOT
                / "lib/historical_compatibility/analysis_package_v1.py"
            ).is_file()
        )
        self.assertTrue(
            (
                ROOT
                / "lib/historical_compatibility/"
                "manual_capture_admission.py"
            ).is_file()
        )
        live_gate_source = (ROOT / "lib/live_execution_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("manual_capture_admission", live_gate_source)
        for relative in (
            "bin/preprocess_dispatcher.py",
            "bin/preprocess_task_runner.py",
            "lib/core_dispatch_bridge.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("analysis_package_v1", source, relative)
            self.assertNotIn(
                "historical_compatibility.analysis_package_v1",
                source,
                relative,
            )


if __name__ == "__main__":
    unittest.main()
