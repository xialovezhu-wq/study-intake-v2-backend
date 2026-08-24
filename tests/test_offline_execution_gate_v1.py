from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.portable_plugin_fixture import build_portable_plugin_fixture


ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "lib", ROOT / "bin"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import preprocess_dispatcher as dispatcher
from concurrent_dispatch import DispatchError
from live_execution_gate import LiveExecutionDenied, assert_external_launch_allowed
from preprocessor_core import (
    CodexRunner,
    PreprocessorError,
    _validate_hosted_synthetic_trial_config,
    load_config,
)


def substitute(
    value: object,
    release_root: Path,
    runtime_root: Path,
    replacements: dict[str, str] | None = None,
) -> object:
    markers = {
        "${RELEASE_ROOT}": str(release_root),
        "${RUNTIME_DATA_ROOT}": str(runtime_root),
        **(replacements or {}),
    }
    if isinstance(value, dict):
        return {
            key: substitute(item, release_root, runtime_root, markers)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [substitute(item, release_root, runtime_root, markers) for item in value]
    if isinstance(value, str):
        for marker, replacement in markers.items():
            value = value.replace(marker, replacement)
        return value
    return value


class OfflineExecutionGateV1Tests(unittest.TestCase):
    def test_release_config_is_live_authorized_and_role_separated(self) -> None:
        template = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            portable = build_portable_plugin_fixture(Path(temporary), ROOT)
            runtime = Path(temporary) / "runtime"
            value = substitute(
                template,
                ROOT,
                runtime,
                {
                    "${CODEX_EXECUTABLE}": sys.executable,
                    "${PYTHON_EXECUTABLE}": sys.executable,
                    "${MCP_PYTHON_EXECUTABLE}": str(portable.mcp_python),
                    "${MCP_ROOT}": str(portable.mcp_root),
                    "${MATH_ROOT}": str(portable.subject_roots["math"]),
                    "${CS408_ROOT}": str(portable.subject_roots["cs408"]),
                    "${ENGLISH_ROOT}": str(portable.subject_roots["english"]),
                },
            )
            value["processing_plugin"].update(
                {
                    "root": str(portable.plugin_root),
                    "component_lock_path": str(
                        portable.plugin_root / "component-lock.json"
                    ),
                    "mcp_client_python": str(portable.mcp_python),
                    "mcp_project_root": str(portable.mcp_root),
                }
            )
            config_path = Path(temporary) / "config.json"
            config_path.write_text(json.dumps(value), encoding="utf-8")
            config = load_config(config_path)
        self.assertEqual(config["execution_mode"], "live_authorized")
        self.assertTrue(config["live_execution_gate"]["default_locked"])
        self.assertEqual(config["models"]["orchestrator"]["model"], "gpt-5.6-terra")
        self.assertEqual(config["models"]["orchestrator"]["reasoning_effort"], "ultra")
        self.assertEqual(config["models"]["reader"]["model"], "gpt-5.6-luna")
        self.assertEqual(config["models"]["reader"]["reasoning_effort"], "max")
        self.assertFalse(config["models"]["reader"]["agents_enabled"])

    def test_offline_commands_fail_before_worker_or_scanner_construction(self) -> None:
        with self.assertRaises(DispatchError) as run_once:
            dispatcher._run_once(
                {"execution_mode": "offline"}, "math", Path("/nonexistent")
            )
        self.assertEqual(run_once.exception.code, "offline_run_once_forbidden")
        with self.assertRaises(DispatchError) as audit:
            dispatcher._audit({"execution_mode": "offline"}, "math")
        self.assertEqual(audit.exception.code, "offline_producer_scan_forbidden")
        runtime = object.__new__(dispatcher.ProductionDispatchRuntime)
        runtime.config = {"execution_mode": "offline"}
        with self.assertRaises(DispatchError) as scan:
            runtime.scan_and_submit()
        self.assertEqual(scan.exception.code, "offline_producer_scan_forbidden")

    def test_live_launch_requires_current_task_execution_proof(self) -> None:
        config = {
            "execution_mode": "live_authorized",
            "runtime_root": "/private/tmp/study-intake-proof-test",
        }
        with self.assertRaises(LiveExecutionDenied) as missing:
            assert_external_launch_allowed(
                config,
                purpose="provider_model_request",
                command=["/Applications/ChatGPT.app/Contents/Resources/codex"],
            )
        self.assertEqual(
            missing.exception.code, "task_execution_proof_missing"
        )

        identity = {
            "subject": "math",
            "capture_id": "CAP-1",
            "capture_content_sha256": "1" * 64,
            "release_id": "2" * 64,
            "activation_id": "3" * 64,
            "unit_sha256": "4" * 64,
            "frozen_payload_sha256": "5" * 64,
            "lease_owner_id": "dispatcher-test",
            "lease_fence": 1,
        }
        reference = {
            "schema_version": (
                "study-intake-task-execution-proof-reference-v1"
            ),
            "task_execution_proof_sha256": "6" * 64,
            "task_execution_proof_path": "/private/tmp/proof.json",
            "task_identity": identity,
            "formal_write_count": 0,
        }
        with mock.patch(
            "concurrent_dispatch.validate_task_execution_proof_for_launch",
            return_value={
                **identity,
                "authorized_pipeline": "analysis_package_v2",
            },
        ) as verifier:
            decision = assert_external_launch_allowed(
                config,
                purpose="provider_model_request",
                command=["/Applications/ChatGPT.app/Contents/Resources/codex"],
                task_identity=identity,
                authorization=reference,
            )
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "task_execution_proof_valid")
        verifier.assert_called_once()

        with self.assertRaises(LiveExecutionDenied) as writer:
            assert_external_launch_allowed(
                config,
                purpose="formal_writer",
                command=["/forbidden/writer"],
                task_identity=identity,
                authorization=reference,
            )
        self.assertEqual(
            writer.exception.code,
            "task_execution_proof_purpose_forbidden",
        )

    def test_codex_runner_offline_tripwire_precedes_popen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = CodexRunner(
                {
                    "execution_mode": "offline",
                    "live_execution_gate": {
                        "enabled": True,
                        "default_locked": True,
                        "authorization_required": True,
                    },
                },
                Path(temporary),
            )
            with (
                mock.patch.dict(
                    os.environ,
                    {"STUDY_INTAKE_FIXTURE_EXECUTION": ""},
                ),
                mock.patch("preprocessor_core.subprocess.Popen") as popen,
            ):
                with self.assertRaises(PreprocessorError) as caught:
                    runner._invoke_subprocess(
                        [
                            "/Applications/ChatGPT.app/Contents/Resources/codex",
                            "exec",
                            "--model",
                            "gpt-5.6-terra",
                        ],
                        input=b"{}",
                        timeout=None,
                        cwd=Path(temporary),
                        stage_name="orchestrator",
                    )
                self.assertEqual(
                    caught.exception.code, "offline_external_launch_forbidden"
                )
                popen.assert_not_called()

    def test_hosted_synthetic_mode_requires_all_roots_inside_temp_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary).resolve()
            roots = {}
            for subject in ("math", "cs408", "english"):
                path = runtime / "subjects" / subject
                path.mkdir(parents=True)
                roots[subject] = str(path)
            config = {
                "execution_mode": "hosted_synthetic",
                "hosted_synthetic_trial": {
                    "enabled": True,
                    "synthetic_only": True,
                    "capture_source_kind": "synthetic",
                    "runtime_root": str(runtime),
                    "subject_roots": roots,
                    "formal_write_count": 0,
                },
            }
            decision = assert_external_launch_allowed(
                config,
                purpose="provider_model_request",
                command=[
                    "/Applications/ChatGPT.app/Contents/Resources/codex",
                    "exec",
                    "--model",
                    "gpt-5.6-terra",
                ],
            )
            self.assertTrue(decision["allowed"])
            drifted = json.loads(json.dumps(config))
            drifted["hosted_synthetic_trial"]["subject_roots"]["math"] = str(
                runtime.parent
            )
            with self.assertRaises(LiveExecutionDenied) as caught:
                assert_external_launch_allowed(
                    drifted,
                    purpose="provider_model_request",
                    command=[
                        "/Applications/ChatGPT.app/Contents/Resources/codex",
                        "exec",
                        "--model",
                        "gpt-5.6-terra",
                    ],
                )
            self.assertEqual(
                caught.exception.code, "hosted_synthetic_launch_invalid"
            )

    def test_hosted_synthetic_config_requires_three_isolated_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trial_root = Path(temporary).resolve()
            runtime_root = trial_root / "runtime"
            runtime_root.mkdir()
            roots = {}
            for subject in ("math", "cs408", "english"):
                root = trial_root / "subjects" / subject
                root.mkdir(parents=True)
                roots[subject] = str(root)
            config = {
                "runtime_root": str(runtime_root),
                "hosted_synthetic_trial": {
                    "enabled": True,
                    "synthetic_only": True,
                    "capture_source_kind": "synthetic",
                    "runtime_root": str(trial_root),
                    "subject_roots": roots,
                    "formal_write_count": 0,
                },
            }
            _validate_hosted_synthetic_trial_config(config)

            outside = json.loads(json.dumps(config))
            outside["hosted_synthetic_trial"]["subject_roots"]["math"] = str(
                trial_root.parent
            )
            with self.assertRaisesRegex(
                PreprocessorError, "config_hosted_synthetic_trial_invalid"
            ):
                _validate_hosted_synthetic_trial_config(outside)

            extra = json.loads(json.dumps(config))
            extra["hosted_synthetic_trial"]["unexpected"] = True
            with self.assertRaisesRegex(
                PreprocessorError, "config_hosted_synthetic_trial_invalid"
            ):
                _validate_hosted_synthetic_trial_config(extra)


if __name__ == "__main__":
    unittest.main()
