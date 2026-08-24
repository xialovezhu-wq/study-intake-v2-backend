from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import math_live_direct_mcp as direct_mcp  # noqa: E402
from tests.portable_plugin_fixture import (  # noqa: E402
    build_portable_plugin_fixture,
)

from math_live_direct_mcp import (  # noqa: E402
    DIRECT_MCP_DISPATCH_REASON,
    LIVE_CAPTURE_IDS,
    MathLiveDirectMcpError,
    build_live_math_direct_mcp_execution,
    register_live_math_direct_candidates,
)
from preprocessor_core import (  # noqa: E402
    CodexRunner,
    PreprocessorError,
    _expected_math_dynamic_schema_sha256s,
    _math_direct_mcp_schema_mode,
    LOADED_CORE_SHA256,
    load_config,
)



def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _replace_markers(value: object, replacements: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {
            key: _replace_markers(nested, replacements)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_replace_markers(nested, replacements) for nested in value]
    if isinstance(value, str):
        rendered = value
        for marker, replacement in replacements.items():
            rendered = rendered.replace(marker, replacement)
        return rendered
    return value


def _prepare_mcp_python(base: Path, mcp_source: Path) -> Path:
    venv_root = base / "mcp-venv"
    commands = [
        [sys.executable, "-m", "venv", str(venv_root)],
        [
            str(venv_root / "bin/python"),
            "-m",
            "pip",
            "install",
            "setuptools==80.9.0",
        ],
        [
            str(venv_root / "bin/python"),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "-r",
            str(mcp_source / "requirements.lock"),
        ],
        [
            str(venv_root / "bin/python"),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            str(mcp_source),
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=mcp_source,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "portable MCP dependency setup failed: " + completed.stderr
            )
    return (venv_root / "bin/python").absolute()


def _synthetic_math_authority(base: Path) -> tuple[Path, dict]:
    authority_root = base / "math-authority"
    items_root = authority_root / "items"
    items_root.mkdir(parents=True)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001"
        "08060000001f15c4890000000d4944415408d763f8cfc0f0"
        "1f00050001ff89993d1d0000000049454e44ae426082"
    )
    task_specs = (
        (
            "LUNA-MATH-20260809-001",
            "MATH-SYNTHETIC-001",
            "GS-1001",
            "existing_formal_card_review",
            "formal_card:GS-1001",
            "independent_correct_no_false_wrong_card",
        ),
        (
            "LUNA-MATH-20260809-002",
            "MATH-SYNTHETIC-002",
            "GS-1002",
            "existing_formal_card_review",
            "formal_card:GS-1002",
            "wrong_then_corrected_weakness_proposal",
        ),
        (
            "LUNA-MATH-20260809-003",
            "MATH-SYNTHETIC-003",
            None,
            "new_source_learning_episode",
            "synthetic-source-003",
            "wrong_then_corrected_multistage_method_proposal",
        ),
    )
    tasks: list[dict] = []
    for index, (
        capture_id,
        business_task_id,
        formal_id,
        source_route,
        source_locator,
        task_kind,
    ) in enumerate(task_specs, start=1):
        item = items_root / capture_id
        item.mkdir()
        question = item / "question.png"
        solution = item / "solution.md"
        record = item / "learning-record.json"
        dialogue = item / "dialogue.json"
        question.write_bytes(png)
        solution.write_text(
            "---\nrole: solution_text\n---\nSynthetic private solution.\n",
            encoding="utf-8",
        )
        record.write_text(
            json.dumps(
                {"capture_id": capture_id, "synthetic": True},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        dialogue.write_text(
            json.dumps(
                {"capture_id": capture_id, "turns": ["synthetic"]},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        artifacts = []
        for artifact_id, kind, path, visibility in (
            ("question", "question_image", question, "answer_safe_question_surface"),
            ("solution", "solution_text", solution, "private_evidence"),
            ("record", "business_task_record", record, "private_evidence"),
            ("dialogue", "dialogue_sequence_and_provenance", dialogue, "private_evidence"),
        ):
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "path": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "visibility": visibility,
                }
            )
        captured_at = f"2026-08-09T00:0{index}:00Z"
        fingerprint = hashlib.sha256(
            f"synthetic:{business_task_id}".encode("utf-8")
        ).hexdigest()
        manifest_sha = hashlib.sha256(
            _canonical_bytes(
                {
                    "capture_id": capture_id,
                    "artifacts": artifacts,
                    "content_fingerprint": fingerprint,
                }
            )
        ).hexdigest()
        if formal_id is None:
            derived = {
                "formal_id": None,
                "formal_id_must_remain_null": True,
                "source_locator": source_locator,
                "captured_at": captured_at,
            }
        else:
            derived = {
                "formal_id_match": True,
                "question_sha256_match": True,
                "captured_at": captured_at,
            }
        tasks.append(
            {
                "business_task_id": business_task_id,
                "capture_id": capture_id,
                "source_route": source_route,
                "formal_id": formal_id,
                "source_locator": source_locator,
                "task_kind": task_kind,
                "study_date": "2026-08-09",
                "captured_at": captured_at,
                "content_fingerprint": fingerprint,
                "manifest_sha256": manifest_sha,
                "artifacts": artifacts,
                "batch_id": "MATH-SYNTHETIC-BATCH",
                "reasoning_boundary": "synthetic evidence remains proposal-only",
                "derived_capture_binding": derived,
                "solution_evidence": {
                    "source_verified": True,
                    "source_sha256": hashlib.sha256(
                        solution.read_bytes()
                    ).hexdigest(),
                },
                "authorization": {
                    "processing_authorized": True,
                    "luna_authorized": True,
                    "sol_authorized": False,
                    "formal_write_count": 0,
                    "quick_intake_written": False,
                },
                "luna_eligible": True,
                "proposal_only": True,
                "formal_id_must_remain_null": formal_id is None,
            }
        )
    authority = authority_root / "luna-real-business-samples.json"
    authority.write_bytes(
        _canonical_bytes(
            {
                "schema_version": "synthetic-math-authority-v1",
                "task_count": len(tasks),
                "tasks": [
                    {
                        "capture_id": row["capture_id"],
                        "business_task_id": row["business_task_id"],
                    }
                    for row in tasks
                ],
            }
        )
    )
    authority_sha = hashlib.sha256(authority.read_bytes()).hexdigest()
    return authority, {
        "authority_schema_version": "math-luna-real-business-samples-v1",
        "status": "passed_real_luna_business_preflight",
        "authority_manifest_path": str(authority),
        "authority_manifest_sha256": authority_sha,
        "fixture_tree_sha256": hashlib.sha256(
            _canonical_bytes(tasks)
        ).hexdigest(),
        "model_request": {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "runtime_attestation": "requested_unverified",
        },
        "task_count": 3,
        "tasks": tasks,
        "model_call_count": 0,
        "formal_write_count": 0,
    }


def _ensure_runtime_release(runtime: Path, release_id: str) -> None:
    release_root = runtime / "releases" / release_id
    release_root.mkdir(parents=True, exist_ok=True)
    (runtime / "packages" / "objects").mkdir(parents=True, exist_ok=True)
    (release_root / "release.json").write_bytes(
        _canonical_bytes(
            {
                "schema_version": "study-intake-preprocessor-release-v2",
                "release_id": release_id,
                "component_inventory": {},
            }
        )
    )
    current = runtime / "current"
    if current.exists() or current.is_symlink():
        current.unlink()
    current.symlink_to(release_root, target_is_directory=True)


class MathLiveDirectMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.portable_temp = tempfile.TemporaryDirectory(
            prefix="math-live-portable-"
        )
        portable_base = Path(cls.portable_temp.name)
        mcp_source = (ROOT.parent / "local-study-read-mcp").resolve()
        cls.mcp_python = _prepare_mcp_python(portable_base, mcp_source)
        cls.portable = build_portable_plugin_fixture(
            portable_base / "portable",
            ROOT,
            mcp_python=cls.mcp_python,
        )
        cls.authority, authority_result = _synthetic_math_authority(
            portable_base
        )
        cls.RELEASE_ID = LOADED_CORE_SHA256
        release_manifest = cls.portable.source_root / "release.json"
        release_manifest.write_bytes(
            _canonical_bytes(
                {
                    "schema_version": "study-intake-preprocessor-release-v2",
                    "release_id": cls.RELEASE_ID,
                }
            )
        )
        cls.config_root = tempfile.TemporaryDirectory(
            prefix="math-live-ordinary-mode-config-"
        )
        config_path = Path(cls.config_root.name) / "config.json"
        template_path = ROOT / "config.example.json"
        if not template_path.is_file():
            raise AssertionError("portable config template is required")
        replacements = {
            "${RELEASE_ROOT}": str(cls.portable.source_root),
            "${RUNTIME_DATA_ROOT}": str(
                Path(cls.config_root.name) / "runtime"
            ),
            "${CODEX_EXECUTABLE}": sys.executable,
            "${PYTHON_EXECUTABLE}": sys.executable,
            "${MCP_PYTHON_EXECUTABLE}": str(cls.portable.mcp_python),
            "${MCP_ROOT}": str(cls.portable.mcp_root),
            "${MATH_ROOT}": str(cls.portable.subject_roots["math"]),
            "${CS408_ROOT}": str(cls.portable.subject_roots["cs408"]),
            "${ENGLISH_ROOT}": str(cls.portable.subject_roots["english"]),
        }
        config_value = _replace_markers(
            json.loads(template_path.read_text(encoding="utf-8")),
            replacements,
        )
        assert isinstance(config_value, dict)
        config_value["model"].pop("service_tier", None)
        for profile_name, soft_warning in (
            ("math_deep_v2", 3600),
            ("cs408_deep_v2", 1800),
            ("english_two_pass_v1", 1800),
        ):
            profile = config_value[profile_name]
            profile.pop("stage_timeout_seconds", None)
            profile.update(
                {
                    "package_output_schema": str(
                        ROOT / "schemas/preprocess-package-v4.json"
                    ),
                    "soft_runtime_warning_seconds": soft_warning,
                    "stall_timeout_seconds": 1800,
                    "stall_probe_interval_seconds": 60,
                    "stall_probe_required_consecutive_failures": 2,
                }
            )
        config_value["processing_plugin"].update(
            {
                "root": str(cls.portable.plugin_root),
                "component_lock_path": str(
                    cls.portable.plugin_root / "component-lock.json"
                ),
                "mcp_client_python": str(cls.portable.mcp_python),
                "mcp_project_root": str(cls.portable.mcp_root),
            }
        )
        config_path.write_text(
            json.dumps(config_value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cls.config = load_config(config_path)
        cls.authority_result = authority_result
        cls.validate_manifest_patch = mock.patch.object(
            direct_mcp,
            "validate_manifest",
            return_value=authority_result,
        )
        cls.validate_manifest_patch.start()
        cls.execution = build_live_math_direct_mcp_execution(
            config=cls.config,
            release_id=cls.RELEASE_ID,
            authority_manifest_path=cls.authority,
            execution_attempt=1,
            execution_runtime_id="ZERO-MODEL-MATH-LIVE-1",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.validate_manifest_patch.stop()
        cls.config_root.cleanup()
        cls.portable_temp.cleanup()

    def test_exact_three_candidate_bound_units_are_private_and_proposal_only(self) -> None:
        receipt = self.execution.public_receipt()
        self.assertEqual(tuple(row["capture_id"] for row in receipt["tasks"]), LIVE_CAPTURE_IDS)
        self.assertEqual(receipt["expected_model_call_count_per_task"], 2)
        self.assertEqual(receipt["stage_order"], ["analysis", "critical_review"])
        self.assertTrue(receipt["fresh_critical_review_context"])
        self.assertEqual(receipt["model_call_count"], 0)
        self.assertEqual(receipt["formal_write_count"], 0)
        self.assertEqual(len({unit.task.unit_sha256 for unit in self.execution.units}), 3)
        for unit in self.execution.units:
            candidate = unit.candidate
            public = json.dumps(
                {
                    "input_binding": candidate.input_binding,
                    "model_input": candidate.model_input,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            self.assertNotIn("/Users/", public)
            self.assertNotIn("/private/", public)
            self.assertNotIn("/var/", public)
            self.assertEqual(candidate.allowed_evidence_refs, ())
            self.assertEqual(candidate.image_paths, ())
            self.assertTrue(candidate.input_binding["proposal_only"])
            self.assertFalse(candidate.input_binding["sol_authorized"])
            self.assertEqual(candidate.input_binding["formal_write_count"], 0)
            self.assertEqual(
                unit.task.frozen_payload["dispatch_contract"]["dispatch_reason"],
                DIRECT_MCP_DISPATCH_REASON,
            )
            checked = CodexRunner._validated_private_capture_args(candidate)
            self.assertIsNotNone(checked)
            assert checked is not None
            ids = [row["artifact_id"] for row in checked["capture_artifacts"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(
                sorted(ids),
                sorted(
                    row["artifact_id"]
                    for row in checked["capture_facts"]["facts"]["artifact_index"]
                ),
            )
        third = self.execution.units[2]
        self.assertIsNone(third.formal_id)
        self.assertEqual(
            third.candidate.input_binding["source_route"], "new_intake"
        )
        self.assertIsNone(
            third.candidate.input_binding["math_live_formal_id"]
        )

    def test_attempt_and_runtime_binding_make_distinct_units_without_changing_fingerprint(self) -> None:
        later = build_live_math_direct_mcp_execution(
            config=self.config,
            release_id=self.RELEASE_ID,
            authority_manifest_path=self.authority,
            execution_attempt=2,
            execution_runtime_id="ZERO-MODEL-MATH-LIVE-2",
        )
        self.assertEqual(
            [unit.candidate.input_fingerprint for unit in self.execution.units],
            [unit.candidate.input_fingerprint for unit in later.units],
        )
        self.assertTrue(
            all(
                first.task.unit_sha256 != second.task.unit_sha256
                for first, second in zip(self.execution.units, later.units)
            )
        )
        with self.assertRaisesRegex(
            MathLiveDirectMcpError,
            "math_live_direct_mcp_execution_identity_invalid",
        ):
            build_live_math_direct_mcp_execution(
                config=self.config,
                release_id=self.RELEASE_ID,
                authority_manifest_path=self.authority,
                execution_attempt=4,
                execution_runtime_id="INVALID-4",
            )

    def test_private_capture_binding_tamper_fails_closed(self) -> None:
        candidate = self.execution.units[0].candidate
        private = copy.deepcopy(candidate.private_context)
        assert private is not None
        private["processing_host_capture_args"]["capture_identity"][
            "formal_id"
        ] = "GS-507"
        tampered = copy.copy(candidate)
        object.__setattr__(tampered, "private_context", private)
        with self.assertRaisesRegex(
            PreprocessorError, "processing_host_capture_args_invalid"
        ):
            CodexRunner._validated_private_capture_args(tampered)

    def test_empty_predeclared_refs_are_limited_to_verified_direct_mcp_schema_mode(self) -> None:
        candidate = self.execution.units[2].candidate
        self.assertEqual(candidate.allowed_evidence_refs, ())
        self.assertTrue(_math_direct_mcp_schema_mode(candidate.input_binding))
        analysis_schema = ROOT / "schemas/luna-math-analysis-v2.json"
        review_schema = ROOT / "schemas/luna-math-critical-review-v3.json"

        with self.assertRaisesRegex(
            PreprocessorError,
            "math_analysis_evidence_refs_limit_exceeded",
        ):
            CodexRunner._bound_output_schema_bytes(
                analysis_schema,
                stage_name="math_analysis",
                allowed_evidence_refs=(),
            )

        analysis_payload, analysis_sha256 = (
            CodexRunner._bound_output_schema_bytes(
                analysis_schema,
                stage_name="math_analysis",
                allowed_evidence_refs=(),
                allow_empty_predeclared_evidence_refs=True,
            )
        )
        self.assertEqual(
            hashlib.sha256(analysis_payload).hexdigest(),
            analysis_sha256,
        )
        self.assertEqual(
            json.loads(analysis_payload)["$defs"]["evidence_ref"]["type"],
            "string",
        )

        draft = {
            "executive_summary": "只用于确定性 Schema 哈希重建。",
            "atomic_signals": [],
        }
        rebuilt = _expected_math_dynamic_schema_sha256s(
            self.config["math_deep_v2"],
            allowed_evidence_refs=(),
            image_evidence_refs=(),
            draft_analysis=draft,
            relationship_context={},
            allow_empty_predeclared_evidence_refs=True,
        )
        review_payload, review_sha256 = (
            CodexRunner._bound_output_schema_bytes(
                review_schema,
                stage_name="math_critical_review",
                allowed_evidence_refs=(),
                allowed_analysis_refs=(
                    "analysis.atomic_signals",
                    "analysis.executive_summary",
                ),
                allowed_correction_paths=(
                    "$.atomic_signals",
                    "$.executive_summary",
                ),
                allow_empty_predeclared_evidence_refs=True,
            )
        )
        self.assertEqual(
            hashlib.sha256(review_payload).hexdigest(),
            review_sha256,
        )
        self.assertEqual(
            rebuilt,
            {
                "analysis": analysis_sha256,
                "critical_review": review_sha256,
            },
        )

        with tempfile.TemporaryDirectory(prefix="math-empty-ref-gate-") as raw:
            runner = CodexRunner(self.config["model"], Path(raw))
            with mock.patch.object(runner, "_invoke_subprocess") as invoke:
                with self.assertRaisesRegex(
                    PreprocessorError,
                    "math_direct_mcp_empty_evidence_ref_mode_invalid",
                ):
                    runner._execute_prompt(
                        prompt="direct MCP schema gate only",
                        output_schema=analysis_schema,
                        image_paths=(),
                        stage_name="math_analysis",
                        max_prompt_bytes=4096,
                        max_output_bytes=4096,
                        allowed_evidence_refs=(),
                        subject="math",
                        processing_context=None,
                        allow_empty_predeclared_evidence_refs=True,
                    )
                invoke.assert_not_called()

        oversized = tuple(f"capture.ref.{index}" for index in range(193))
        with self.assertRaisesRegex(
            PreprocessorError,
            "math_analysis_evidence_refs_limit_exceeded",
        ):
            CodexRunner._bound_output_schema_bytes(
                analysis_schema,
                stage_name="math_analysis",
                allowed_evidence_refs=oversized,
                allow_empty_predeclared_evidence_refs=True,
            )

    def test_real_processing_host_freezes_every_artifact_and_bootstrap_has_no_body(self) -> None:
        candidate = self.execution.units[0].candidate
        with tempfile.TemporaryDirectory(prefix="math-live-host-freeze-") as raw:
            runtime_root = Path(raw)
            _ensure_runtime_release(runtime_root, self.RELEASE_ID)
            key = runtime_root / "authority.key"
            key.write_bytes(b"h" * 32)
            key.chmod(0o600)
            config = copy.deepcopy(self.config)
            config["authority_release_id"] = self.RELEASE_ID
            config["processing_plugin"]["authority_key_path"] = str(key)
            runner = CodexRunner(config, runtime_root)
            self.assertIsNotNone(runner._processing_host)
            assert runner._processing_host is not None
            lock = json.loads(
                Path(config["processing_plugin"]["component_lock_path"])
                .read_text(encoding="utf-8")
            )
            server_release = lock["mcp_server_release"]

            context = runner._background_context(candidate)
            assert context is not None
            session = context["mcp_read_session"]
            manifest = json.loads(
                Path(session["capture_manifest_path"]).read_text(encoding="utf-8")
            )
            private = CodexRunner._validated_private_capture_args(candidate)
            assert private is not None
            self.assertEqual(
                len(manifest["artifacts"]),
                len(private["capture_artifacts"]) + 1,
            )
            self.assertEqual(
                {row["artifact_id"] for row in manifest["artifacts"]},
                {"capture-facts"}
                | {row["artifact_id"] for row in private["capture_artifacts"]},
            )
            prompt = runner._math_analysis_prompt(
                candidate, config["math_deep_v2"], context
            )
            self.assertNotIn("/Users/", prompt)
            self.assertNotIn("/private/", prompt)
            self.assertNotIn("/var/", prompt)
            for descriptor in private["capture_artifacts"]:
                path = Path(descriptor["path"])
                if path.suffix.lower() not in {".md", ".txt", ".json"}:
                    continue
                text = path.read_text(encoding="utf-8")
                snippets = [line.strip() for line in text.splitlines() if len(line.strip()) >= 24]
                if snippets:
                    self.assertNotIn(snippets[-1], prompt)
            self.assertEqual(
                context["processing_binding"]["mcp"]["id"],
                "kaoyan_math_read",
            )
            model_config = copy.deepcopy(config["model"])
            model_config["processing_plugin"] = copy.deepcopy(
                config["processing_plugin"]
            )
            model_config["authority_release_id"] = self.RELEASE_ID
            model_config["subject_repo_roots"] = {
                name: adapter["repo_root"]
                for name, adapter in config["adapters"].items()
                if isinstance(adapter, dict)
                and isinstance(adapter.get("repo_root"), str)
            }
            execution_runner = CodexRunner(model_config, runtime_root)

            def fail_after_raw_freeze(_command, **kwargs):
                digest = hashlib.sha256(b"").hexdigest()
                execution_runner._provider_raw_refs[kwargs["stage_name"]] = {
                    "raw_output_object_sha256": digest,
                    "raw_output_object_ref": f"test-raw://sha256/{digest}",
                }
                return SimpleNamespace(
                    returncode=1,
                    stdout=b"",
                    stderr=b"",
                )

            with mock.patch.object(
                execution_runner,
                "_invoke_subprocess",
                side_effect=fail_after_raw_freeze,
            ) as invoke:
                with self.assertRaisesRegex(
                    PreprocessorError,
                    "math_analysis_nonzero_exit",
                ):
                    execution_runner._execute_prompt(
                        prompt="direct MCP transport boundary probe",
                        output_schema=(
                            ROOT / "schemas/luna-math-analysis-v2.json"
                        ),
                        image_paths=(),
                        stage_name="math_analysis",
                        max_prompt_bytes=4096,
                        max_output_bytes=4096,
                        allowed_evidence_refs=(),
                        subject="math",
                        processing_context=context,
                        allow_empty_predeclared_evidence_refs=True,
                    )
                invoke.assert_called_once()

    def test_registration_is_zero_model_and_exactly_once(self) -> None:
        class FakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def register_controlled_replay_candidate(
                self, task, candidate, *, reason
            ):
                self.calls.append((task, candidate, reason))

        runtime = FakeRuntime()
        register_live_math_direct_candidates(runtime, self.execution)
        self.assertEqual(len(runtime.calls), 3)
        self.assertEqual(
            [candidate.capture_id for _task, candidate, _reason in runtime.calls],
            list(LIVE_CAPTURE_IDS),
        )
        self.assertEqual(
            {reason for _task, _candidate, reason in runtime.calls},
            {DIRECT_MCP_DISPATCH_REASON},
        )


if __name__ == "__main__":
    unittest.main()
