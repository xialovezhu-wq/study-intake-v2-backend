from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_source_subject_provider_acceptance.py"
SPEC = importlib.util.spec_from_file_location(
    "source_subject_provider_acceptance", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


class SourceSubjectProviderAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.runtime = self.base / "round/runtime"
        self.producer = self.base / "round/producer"
        self.scratch = self.base / "round/scratch"
        self.evidence = self.base / "round/evidence"
        for path in (self.runtime, self.producer, self.scratch, self.evidence):
            path.mkdir(parents=True)
        self.environment = {
            "STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT": str(self.runtime),
            "STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT": str(self.producer),
            "STUDY_SOURCE_ACCEPTANCE_SCRATCH_ROOT": str(self.scratch),
            "STUDY_SOURCE_ACCEPTANCE_EVIDENCE_ROOT": str(self.evidence),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _mcp_source(self) -> Path:
        root = self.base / "mcp-source"
        files = {
            "pyproject.toml": b"[project]\nname='fixture'\n",
            "requirements.lock": b"fixture\n",
            "config/codex-mcp-snippet.toml": b"[mcp_servers]\n",
            "config/skill-tool-policy.json": b"{}\n",
            "scripts/sealed_launcher.py": b"# sealed\n",
            "src/study_read_mcp/__init__.py": b"__version__='1'\n",
            "src/study_read_mcp/server.py": b"SERVER=True\n",
            "tests/helpers.py": b"HELPER=True\n",
        }
        for relative, payload in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return root

    def _sealed_mcp(self) -> tuple[Path, Path, dict[str, object]]:
        source = self._mcp_source()
        source_files = acceptance._digest_map(source)
        release_id = hashlib.sha256(
            acceptance.canonical_bytes(dict(sorted(source_files.items())))
        ).hexdigest()
        release = self.base / "sealed" / release_id
        for relative in source_files:
            source_path = source / relative
            target = release / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_path.read_bytes())
            target.chmod(0o444)
        manifest = {
            "schema_version": "study-read-mcp-release.v1",
            "release_id": release_id,
            "server_release": f"fixture+sha256.{release_id}",
            "source_files": source_files,
            "formal_write_count": 0,
        }
        manifest_path = release / "release.json"
        manifest_path.write_bytes(acceptance.canonical_bytes(manifest))
        manifest_path.chmod(0o444)
        python = self.base / "python"
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        spec = {
            "canonical_source_root": str(source),
            "release_root": str(release),
            "release_manifest_sha256": acceptance.sha256_file(manifest_path),
            "python_executable": str(python),
            "canonical_helpers_sha256": acceptance.sha256_file(
                source / "tests/helpers.py"
            ),
        }
        return source, release, spec

    def _backend(self) -> Path:
        root = self.base / "backend-latest-source"
        root.mkdir()
        (root / "lib").mkdir()
        (root / "lib/preprocessor_core.py").write_text(
            "SOURCE_ONLY=True\n", encoding="utf-8"
        )
        (root / "config.example.json").write_text(
            json.dumps(
                {
                    "release": {"manifest_path": "${RELEASE_ROOT}/release.json"},
                    "processing_plugin": {
                        "mcp_project_root": "${MCP_ROOT}",
                        "mcp_client_python": "${MCP_PYTHON_EXECUTABLE}",
                    },
                    "model": {"codex_path": "${CODEX_EXECUTABLE}"},
                    "adapters": {
                        "math": {"repo_root": "${MATH_ROOT}"},
                        "cs408": {"repo_root": "${CS408_ROOT}"},
                        "english": {"repo_root": "${ENGLISH_ROOT}"},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "source@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Source Test"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "source"], cwd=root, check=True
        )
        return root

    def test_real_execution_requires_both_environment_and_spec_gate(self) -> None:
        spec_path = self.base / "spec.json"
        spec_path.write_bytes(
            acceptance.canonical_bytes(
                {
                    "schema_version": acceptance.SPEC_SCHEMA,
                    "subject": "math",
                    "real_provider": True,
                }
            )
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "real_provider_environment_gate_closed",
            ):
                acceptance.run(spec_path)
        value = json.loads(spec_path.read_text(encoding="utf-8"))
        value["real_provider"] = False
        spec_path.write_bytes(acceptance.canonical_bytes(value))
        with mock.patch.dict(
            os.environ,
            {"STUDY_SOURCE_ACCEPTANCE_REAL_PROVIDER_ENABLED": "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                acceptance.AcceptanceError, "real_provider_spec_gate_closed"
            ):
                acceptance.run(spec_path)

    def test_writable_roots_must_be_distinct_and_share_narrow_parent(self) -> None:
        roots = acceptance.isolated_roots(self.environment)
        self.assertEqual(set(roots), {"runtime", "producer", "scratch", "evidence"})
        bad = dict(self.environment)
        bad["STUDY_SOURCE_ACCEPTANCE_EVIDENCE_ROOT"] = str(self.runtime)
        with self.assertRaisesRegex(
            acceptance.AcceptanceError, "writable_roots_not_distinct"
        ):
            acceptance.isolated_roots(bad)

    def test_existing_mcp_release_is_bound_to_canonical_source_without_build(self) -> None:
        _source, _release, binding = self._sealed_mcp()
        verified = acceptance.verify_sealed_mcp({"sealed_mcp": binding})
        self.assertFalse(verified["build_release_invoked"])
        self.assertEqual(
            verified["release_root"], str(Path(binding["release_root"]).resolve())
        )
        self.assertEqual(
            verified["canonical_helpers_sha256"],
            binding["canonical_helpers_sha256"],
        )

    def test_mcp_source_or_release_drift_fails_closed(self) -> None:
        source, release, binding = self._sealed_mcp()
        (source / "src/study_read_mcp/server.py").write_text(
            "SERVER='drift'\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            acceptance.AcceptanceError, "sealed_mcp_canonical_binding_mismatch"
        ):
            acceptance.verify_sealed_mcp({"sealed_mcp": binding})
        (source / "src/study_read_mcp/server.py").write_text(
            "SERVER=True\n", encoding="utf-8"
        )
        helpers = source / "tests/helpers.py"
        helpers.write_text("HELPER='drift'\n", encoding="utf-8")
        with self.assertRaisesRegex(
            acceptance.AcceptanceError, "sealed_mcp_helpers_hash_mismatch"
        ):
            acceptance.verify_sealed_mcp({"sealed_mcp": binding})
        helpers.write_text("HELPER=True\n", encoding="utf-8")
        target = release / "src/study_read_mcp/server.py"
        target.chmod(0o644)
        with self.assertRaisesRegex(
            acceptance.AcceptanceError, "sealed_mcp_release_file_invalid"
        ):
            acceptance.verify_sealed_mcp({"sealed_mcp": binding})

    def test_source_release_is_a_scratch_mirror_not_backend_build(self) -> None:
        _source, _release, binding = self._sealed_mcp()
        mcp = acceptance.verify_sealed_mcp({"sealed_mcp": binding})
        backend = self._backend()
        backend_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=backend,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        backend_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=backend,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        roots = acceptance.isolated_roots(self.environment)
        mirror, provenance = acceptance.materialize_source_release(
            {
                "backend_source_root": str(backend),
                "backend_test_module_sha256": "a" * 64,
                "backend_expected_head": backend_head,
                "backend_expected_tree": backend_tree,
                "codex_executable": "/nonexistent/codex",
            },
            roots,
            mcp,
        )
        self.assertTrue((mirror / "release.json").is_file())
        self.assertEqual(mirror.name, provenance["release_id"])
        self.assertFalse(provenance["backend_build_executed"])
        self.assertFalse((self.runtime / "current").exists())
        self.assertEqual(
            json.loads((mirror / "release.json").read_text())["source_mode"],
            True,
        )

    def test_canonical_descriptor_contains_only_canonical_closure_bytes(self) -> None:
        canonical = self.base / "canonical-math"
        relative = "数学一回滚复习系统/scripts/quick_intake.py"
        source = canonical / relative
        source.parent.mkdir(parents=True)
        source.write_bytes(b"CANONICAL_ONLY\n")
        skill_relative = "codex-skill-sources/math/SKILL.md"
        skill = canonical / skill_relative
        skill.parent.mkdir(parents=True)
        skill.write_bytes(b"CANONICAL_SKILL\n")
        contract_relative = "schema/capture.json"
        contract = canonical / contract_relative
        contract.parent.mkdir(parents=True)
        contract.write_bytes(b'{"canonical":true}\n')
        closure = self.producer / "math-closure"
        closure.mkdir()
        acceptance._descriptor(
            "math",
            canonical,
            closure,
            [relative],
            skill_relative,
            [contract_relative],
        )
        descriptor = json.loads(
            (
                closure
                / "数学一回滚复习系统/schema/producer-binding-v1.json"
            ).read_text(encoding="utf-8")
        )
        rows = descriptor["producer"]["source_files"]
        self.assertEqual(len(rows), 1)
        copied = Path(rows[0]["path"])
        self.assertTrue(copied.is_relative_to(closure.resolve()))
        self.assertEqual(copied.read_bytes(), b"CANONICAL_ONLY\n")
        self.assertEqual(
            Path(descriptor["foreground_skill"]["installed_path"]).read_bytes(),
            b"CANONICAL_SKILL\n",
        )
        self.assertEqual(
            Path(descriptor["capture_contract"]["files"][0]["path"]).read_bytes(),
            b'{"canonical":true}\n',
        )
        self.assertNotIn("Documents/kaoyan-math", rows[0]["path"])

    def test_canonical_descriptor_rejects_relative_path_escape(self) -> None:
        canonical = self.base / "canonical-escape"
        canonical.mkdir()
        outside = self.base / "escape.py"
        outside.write_bytes(b"escape\n")
        skill = canonical / "skill/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_bytes(b"skill\n")
        contract = canonical / "schema/capture.json"
        contract.parent.mkdir(parents=True)
        contract.write_bytes(b"{}\n")
        closure = self.producer / "escape-closure"
        closure.mkdir()

        with self.assertRaisesRegex(
            acceptance.AcceptanceError,
            "canonical_relative_path_invalid",
        ):
            acceptance._descriptor(
                "math",
                canonical,
                closure,
                ["../escape.py"],
                "skill/SKILL.md",
                ["schema/capture.json"],
            )

    def test_main_returns_nonzero_for_failed_acceptance_result(self) -> None:
        spec_path = self.base / "main-spec.json"
        spec_path.write_bytes(
            acceptance.canonical_bytes(
                {
                    "schema_version": acceptance.SPEC_SCHEMA,
                    "subject": "math",
                    "real_provider": True,
                }
            )
        )
        output = io.StringIO()
        with (
            mock.patch.object(
                acceptance,
                "run",
                return_value={
                    "status": "failed",
                    "real_model_call_count": 1,
                    "provider_request_count": 2,
                    "provider_pids": [123],
                    "formal_write_count": 0,
                },
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(SCRIPT), "--spec", str(spec_path)],
            ),
            contextlib.redirect_stdout(output),
        ):
            returncode = acceptance.main()
        self.assertEqual(1, returncode)
        self.assertEqual("failed", json.loads(output.getvalue())["status"])

    def test_external_driver_and_backend_test_module_are_hash_bound(self) -> None:
        driver = self.base / "driver.py"
        driver.write_text("VALUE=1\n", encoding="utf-8")
        module = acceptance.load_bound_module(
            "bound_test_driver", driver, acceptance.sha256_file(driver)
        )
        self.assertEqual(module.VALUE, 1)
        with self.assertRaisesRegex(
            acceptance.AcceptanceError, "bound_test_driver_drift_hash_mismatch"
        ):
            acceptance.load_bound_module(
                "bound_test_driver_drift", driver, "0" * 64
            )

    def test_execution_evidence_uses_physical_hashes_and_real_pids(self) -> None:
        root = self.evidence / "retained/trial/runtime"
        provider = root / "dispatch/provider-process-identities/x.json"
        provider.parent.mkdir(parents=True)
        provider.write_bytes(
            acceptance.canonical_bytes(
                {
                    "schema_version": "study-intake-provider-process-identity-v2",
                    "provider_pid": 4321,
                    "stage_name": "math_luna_analysis",
                    "launched_at": "2026-08-23T00:00:00Z",
                    "formal_write_count": 0,
                }
            )
        )
        task = root / "dispatch/process-identities/x.json"
        task.parent.mkdir(parents=True)
        task.write_bytes(
            acceptance.canonical_bytes(
                {
                    "schema_version": "study-intake-task-process-identity-v1",
                    "child_pid": 1234,
                    "formal_write_count": 0,
                }
            )
        )
        transcript_value = {
            "schema_version": "fixture-transcript-v1",
            "calls": [{}],
            "formal_write_count": 0,
        }
        payload = acceptance.canonical_bytes(transcript_value)
        digest = hashlib.sha256(payload).hexdigest()
        transcript = (
            root
            / "private/reports/mcp-stage-transcripts/sha256"
            / digest[:2]
            / f"{digest}.json"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_bytes(payload)
        collected = acceptance.collect_execution_evidence(self.evidence)
        self.assertEqual(collected["provider_pids"], [4321])
        self.assertEqual(collected["task_runner_pids"], [1234])
        self.assertTrue(collected["mcp_transcripts_physical_reopen"])

    def test_script_contains_no_mcp_or_backend_build_invocation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("build_release.py", source)
        self.assertNotIn("release_manager.py", source)
        self.assertNotIn("/runtime/current", source)


if __name__ == "__main__":
    unittest.main()
