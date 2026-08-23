from __future__ import annotations

import contextlib
import copy
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
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))
import core_dispatch_bridge  # noqa: E402
import preprocessor_core  # noqa: E402
from concurrent_dispatch import DispatchResult  # noqa: E402

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
            "tests/helpers.py": (
                b"from study_read_mcp import __version__\nHELPER=True\n"
            ),
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
        python_real = self.base / "python-real"
        python_real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python_real.chmod(0o755)
        python = self.base / "venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(python_real)
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
        self.assertEqual(
            verified["python_executable"], binding["python_executable"]
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
        release_manifest = json.loads((mirror / "release.json").read_text())
        self.assertEqual(release_manifest["source_mode"], True)
        self.assertEqual(release_manifest["component_inventory"], {})

    def test_source_acceptance_preserves_synthetic_task_stderr_diagnostic(
        self,
    ) -> None:
        release_id = "c" * 64
        runtime_root = self.producer / "diagnostic-trial/runtime"
        release_root = runtime_root / "releases" / release_id
        release_root.mkdir(parents=True)
        (release_root / "release.json").write_bytes(
            acceptance.canonical_bytes(
                {
                    "schema_version": "study-intake-preprocessor-release-v2",
                    "release_id": release_id,
                    "component_inventory": {},
                    "source_mode": True,
                    "formal_write_count": 0,
                }
            )
        )
        (runtime_root / "current").symlink_to(
            Path("releases") / release_id,
            target_is_directory=True,
        )
        context_root = (
            runtime_root
            / "dispatch/contexts"
            / ("a" * 64)
            / "fence-1"
        )
        context_root.mkdir(parents=True)
        wrapper = ROOT / "scripts/run_source_subject_provider_acceptance.py"
        stderr = (
            b'Traceback (most recent call last):\n'
            b'  File "/isolated/preprocess_task_runner.py", line 1, in main\n'
            b"KeyError: 'synthetic_field'\n"
        )
        with mock.patch.dict(
            os.environ,
            {
                "STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS": "1",
                "STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT": str(self.producer),
                "STUDY_SOURCE_ACCEPTANCE_WRAPPER_PATH": str(wrapper),
                "STUDY_SOURCE_ACCEPTANCE_WRAPPER_SHA256": (
                    hashlib.sha256(wrapper.read_bytes()).hexdigest()
                ),
            },
            clear=False,
        ):
            diagnostic_path = (
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=context_root,
                    subject="math",
                    unit_sha256="a" * 64,
                    lease_fence=1,
                    returncode=1,
                    stderr=stderr,
                )
            )
        self.assertIsNotNone(diagnostic_path)
        value = json.loads(Path(diagnostic_path).read_text(encoding="utf-8"))
        self.assertEqual(value["stderr_utf8"], stderr.decode("utf-8"))
        self.assertEqual(
            value["stderr_sha256"], hashlib.sha256(stderr).hexdigest()
        )
        self.assertEqual(value["formal_write_count"], 0)

    def test_source_acceptance_diagnostic_rejects_unbound_context(self) -> None:
        context_root = self.runtime / "dispatch/contexts" / ("b" * 64) / "fence-1"
        context_root.mkdir(parents=True)
        with mock.patch.dict(
            os.environ,
            {"STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS": "1"},
            clear=False,
        ):
            os.environ.pop("STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT", None)
            diagnostic_path = (
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=context_root,
                    subject="math",
                    unit_sha256="b" * 64,
                    lease_fence=1,
                    returncode=1,
                    stderr=b"synthetic failure\n",
                )
            )
        self.assertIsNone(diagnostic_path)
        self.assertFalse(
            (context_root / "source-acceptance-diagnostics").exists()
        )

    def test_source_acceptance_diagnostic_security_bindings_fail_closed(
        self,
    ) -> None:
        wrapper = ROOT / "scripts/run_source_subject_provider_acceptance.py"

        def fixture(
            label: str,
            *,
            source_mode: bool = True,
            current_symlink: bool = True,
        ):
            unit = hashlib.sha256(label.encode("utf-8")).hexdigest()
            producer = self.base / f"producer-{label}"
            runtime = producer / "trial/runtime"
            release_id = hashlib.sha256(
                f"release-{label}".encode("utf-8")
            ).hexdigest()
            release = runtime / "releases" / release_id
            release.mkdir(parents=True)
            (release / "release.json").write_bytes(
                acceptance.canonical_bytes(
                    {
                        "schema_version": (
                            "study-intake-preprocessor-release-v2"
                        ),
                        "release_id": release_id,
                        "component_inventory": {},
                        "source_mode": source_mode,
                        "formal_write_count": 0,
                    }
                )
            )
            current = runtime / "current"
            if current_symlink:
                current.symlink_to(
                    Path("releases") / release_id,
                    target_is_directory=True,
                )
            else:
                current.mkdir()
            context = (
                runtime / "dispatch/contexts" / unit / "fence-1"
            )
            context.mkdir(parents=True)
            environment = {
                "STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS": "1",
                "STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT": str(producer),
                "STUDY_SOURCE_ACCEPTANCE_WRAPPER_PATH": str(wrapper),
                "STUDY_SOURCE_ACCEPTANCE_WRAPPER_SHA256": hashlib.sha256(
                    wrapper.read_bytes()
                ).hexdigest(),
            }
            return producer, context, unit, environment

        producer, context, unit, environment = fixture("gate-off")
        with mock.patch.dict(os.environ, environment, clear=False):
            os.environ.pop(
                "STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS", None
            )
            self.assertIsNone(
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=context,
                    subject="math",
                    unit_sha256=unit,
                    lease_fence=1,
                    returncode=1,
                    stderr=b"synthetic\n",
                )
            )
        self.assertFalse(
            (context / "source-acceptance-diagnostics").exists()
        )

        for label, mutation in (
            (
                "wrapper-path",
                lambda env: env.update(
                    {"STUDY_SOURCE_ACCEPTANCE_WRAPPER_PATH": str(producer)}
                ),
            ),
            (
                "wrapper-hash",
                lambda env: env.update(
                    {"STUDY_SOURCE_ACCEPTANCE_WRAPPER_SHA256": "0" * 64}
                ),
            ),
        ):
            _producer, bound_context, bound_unit, bound_environment = fixture(
                label
            )
            mutation(bound_environment)
            with mock.patch.dict(
                os.environ, bound_environment, clear=False
            ):
                self.assertIsNone(
                    core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                        context_root=bound_context,
                        subject="math",
                        unit_sha256=bound_unit,
                        lease_fence=1,
                        returncode=1,
                        stderr=b"synthetic\n",
                    )
                )

        for label, options in (
            ("not-source-mode", {"source_mode": False}),
            ("current-not-link", {"current_symlink": False}),
        ):
            _producer, bound_context, bound_unit, bound_environment = fixture(
                label, **options
            )
            with mock.patch.dict(
                os.environ, bound_environment, clear=False
            ):
                self.assertIsNone(
                    core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                        context_root=bound_context,
                        subject="math",
                        unit_sha256=bound_unit,
                        lease_fence=1,
                        returncode=1,
                        stderr=b"synthetic\n",
                    )
                )

        producer_escape, context_escape, unit_escape, environment_escape = (
            fixture("current-escape")
        )
        runtime_escape = context_escape.parents[3]
        (runtime_escape / "current").unlink()
        escaped_release = producer_escape / ("e" * 64)
        escaped_release.mkdir()
        (escaped_release / "release.json").write_bytes(
            acceptance.canonical_bytes(
                {
                    "schema_version": "study-intake-preprocessor-release-v2",
                    "release_id": "e" * 64,
                    "component_inventory": {},
                    "source_mode": True,
                    "formal_write_count": 0,
                }
            )
        )
        (runtime_escape / "current").symlink_to(
            escaped_release,
            target_is_directory=True,
        )
        with mock.patch.dict(os.environ, environment_escape, clear=False):
            self.assertIsNone(
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=context_escape,
                    subject="math",
                    unit_sha256=unit_escape,
                    lease_fence=1,
                    returncode=1,
                    stderr=b"synthetic\n",
                )
            )

        _outside_producer, outside_context, outside_unit, _outside_env = fixture(
            "symlink-outside"
        )
        linked_producer = self.base / "producer-symlink-parent"
        linked_producer.mkdir()
        (linked_producer / "runtime-link").symlink_to(
            outside_context.parents[3],
            target_is_directory=True,
        )
        linked_context = (
            linked_producer
            / "runtime-link/dispatch/contexts"
            / outside_unit
            / "fence-1"
        )
        linked_environment = {
            "STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS": "1",
            "STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT": str(linked_producer),
            "STUDY_SOURCE_ACCEPTANCE_WRAPPER_PATH": str(wrapper),
            "STUDY_SOURCE_ACCEPTANCE_WRAPPER_SHA256": hashlib.sha256(
                wrapper.read_bytes()
            ).hexdigest(),
        }
        with mock.patch.dict(os.environ, linked_environment, clear=False):
            self.assertIsNone(
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=linked_context,
                    subject="math",
                    unit_sha256=outside_unit,
                    lease_fence=1,
                    returncode=1,
                    stderr=b"synthetic\n",
                )
            )

        _producer, bound_context, bound_unit, bound_environment = fixture(
            "diagnostic-link"
        )
        outside = self.base / "diagnostic-outside"
        outside.mkdir()
        (bound_context / "source-acceptance-diagnostics").symlink_to(
            outside,
            target_is_directory=True,
        )
        with mock.patch.dict(os.environ, bound_environment, clear=False):
            self.assertIsNone(
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=bound_context,
                    subject="math",
                    unit_sha256=bound_unit,
                    lease_fence=1,
                    returncode=1,
                    stderr=b"synthetic\n",
                )
            )
        self.assertEqual(list(outside.iterdir()), [])

        _producer, bound_context, bound_unit, bound_environment = fixture(
            "stderr-limit"
        )
        with mock.patch.dict(os.environ, bound_environment, clear=False):
            self.assertIsNone(
                core_dispatch_bridge._persist_source_acceptance_task_diagnostic(
                    context_root=bound_context,
                    subject="math",
                    unit_sha256=bound_unit,
                    lease_fence=1,
                    returncode=1,
                    stderr=b"x" * (1024 * 1024 + 1),
                )
            )

    def test_completion_v2_report_refs_reopen_for_legacy_consumer(self) -> None:
        runtime_root = self.runtime
        json_payload = b'{"report":true}\n'
        markdown_payload = b"report\n"
        json_sha = hashlib.sha256(json_payload).hexdigest()
        markdown_sha = hashlib.sha256(markdown_payload).hexdigest()
        json_path = (
            runtime_root
            / "dispatch/reports/json/sha256"
            / json_sha[:2]
            / f"{json_sha}.json"
        )
        markdown_path = (
            runtime_root
            / "dispatch/reports/markdown/sha256"
            / markdown_sha[:2]
            / f"{markdown_sha}.md"
        )
        json_path.parent.mkdir(parents=True)
        markdown_path.parent.mkdir(parents=True)
        json_path.write_bytes(json_payload)
        markdown_path.write_bytes(markdown_payload)
        completion = acceptance.reopen_completion_report_paths(
            {
                "schema_version": "study-intake-concurrent-completion-v2",
                "outcome": "succeeded",
                "report_json_ref": (
                    "study-intake-report://sha256/" + json_sha
                ),
                "report_json_sha256": json_sha,
                "report_markdown_ref": (
                    "study-intake-report-markdown://sha256/" + markdown_sha
                ),
                "report_markdown_sha256": markdown_sha,
            },
            runtime_root,
        )
        self.assertEqual(
            completion["report_json_path"], str(json_path.resolve())
        )
        self.assertEqual(
            completion["report_markdown_path"], str(markdown_path.resolve())
        )

    def test_completion_report_reopen_rejects_symlink_parent(self) -> None:
        runtime_root = self.runtime
        outside = self.base / "outside-reports"
        json_payload = b'{"outside":true}\n'
        markdown_payload = b"outside\n"
        json_sha = hashlib.sha256(json_payload).hexdigest()
        markdown_sha = hashlib.sha256(markdown_payload).hexdigest()
        json_path = (
            outside / "json/sha256" / json_sha[:2] / f"{json_sha}.json"
        )
        markdown_path = (
            outside
            / "markdown/sha256"
            / markdown_sha[:2]
            / f"{markdown_sha}.md"
        )
        json_path.parent.mkdir(parents=True)
        markdown_path.parent.mkdir(parents=True)
        json_path.write_bytes(json_payload)
        markdown_path.write_bytes(markdown_payload)
        reports = runtime_root / "dispatch/reports"
        reports.parent.mkdir(parents=True, exist_ok=True)
        reports.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(
            acceptance.AcceptanceError,
            "completion_report_object_invalid",
        ):
            acceptance.reopen_completion_report_paths(
                {
                    "schema_version": "study-intake-concurrent-completion-v2",
                    "outcome": "succeeded",
                    "report_json_ref": (
                        "study-intake-report://sha256/" + json_sha
                    ),
                    "report_json_sha256": json_sha,
                    "report_markdown_ref": (
                        "study-intake-report-markdown://sha256/"
                        + markdown_sha
                    ),
                    "report_markdown_sha256": markdown_sha,
                },
                runtime_root,
            )

    def test_first_wait_bridge_retries_then_preserves_original_v2_result(
        self,
    ) -> None:
        runtime_root = self.runtime
        json_payload = b'{"bridge":true}\n'
        markdown_payload = b"bridge\n"
        json_sha = hashlib.sha256(json_payload).hexdigest()
        markdown_sha = hashlib.sha256(markdown_payload).hexdigest()
        completion = {
            "schema_version": "study-intake-concurrent-completion-v2",
            "outcome": "succeeded",
            "report_json_ref": "study-intake-report://sha256/" + json_sha,
            "report_json_sha256": json_sha,
            "report_markdown_ref": (
                "study-intake-report-markdown://sha256/" + markdown_sha
            ),
            "report_markdown_sha256": markdown_sha,
        }
        original = DispatchResult(
            unit_sha256="d" * 64,
            status="completed",
            outcome="succeeded",
            completion=completion,
        )
        calls: list[float | None] = []

        def original_wait(_handle, timeout=None):
            calls.append(timeout)
            return original

        bridge = acceptance.FirstWaitCompletionBridge(
            original_wait,
            runtime_root,
        )
        handle = object()
        with self.assertRaisesRegex(
            acceptance.AcceptanceError,
            "completion_report_object_missing",
        ):
            bridge.wait(handle, 1)
        json_path = (
            runtime_root
            / "dispatch/reports/json/sha256"
            / json_sha[:2]
            / f"{json_sha}.json"
        )
        markdown_path = (
            runtime_root
            / "dispatch/reports/markdown/sha256"
            / markdown_sha[:2]
            / f"{markdown_sha}.md"
        )
        json_path.parent.mkdir(parents=True)
        markdown_path.parent.mkdir(parents=True)
        json_path.write_bytes(json_payload)
        markdown_path.write_bytes(markdown_payload)
        compatible = bridge.wait(handle, 2)
        self.assertIn("report_json_path", compatible.completion)
        projected = bridge.wait(handle, 0)
        self.assertNotIn("report_json_path", projected.completion)
        self.assertIs(projected, original)
        self.assertEqual(calls, [1, 2, 0])

    def test_completion_report_reopen_rejects_bad_ref_and_hash(self) -> None:
        runtime_root = self.runtime
        payload = b'{"hash":true}\n'
        digest = hashlib.sha256(payload).hexdigest()
        markdown = b"hash\n"
        markdown_digest = hashlib.sha256(markdown).hexdigest()
        base = {
            "schema_version": "study-intake-concurrent-completion-v2",
            "outcome": "succeeded",
            "report_json_ref": "wrong://sha256/" + digest,
            "report_json_sha256": digest,
            "report_markdown_ref": (
                "study-intake-report-markdown://sha256/" + markdown_digest
            ),
            "report_markdown_sha256": markdown_digest,
        }
        with self.assertRaisesRegex(
            acceptance.AcceptanceError,
            "completion_report_binding_invalid",
        ):
            acceptance.reopen_completion_report_paths(base, runtime_root)
        base["report_json_ref"] = "study-intake-report://sha256/" + digest
        json_path = (
            runtime_root
            / "dispatch/reports/json/sha256"
            / digest[:2]
            / f"{digest}.json"
        )
        markdown_path = (
            runtime_root
            / "dispatch/reports/markdown/sha256"
            / markdown_digest[:2]
            / f"{markdown_digest}.md"
        )
        json_path.parent.mkdir(parents=True)
        markdown_path.parent.mkdir(parents=True)
        json_path.write_bytes(b"wrong bytes\n")
        markdown_path.write_bytes(markdown)
        with self.assertRaisesRegex(
            acceptance.AcceptanceError,
            "completion_report_object_invalid",
        ):
            acceptance.reopen_completion_report_paths(base, runtime_root)

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

    def test_math_and_cs408_workers_reopen_bound_english_runtime_closure(
        self,
    ) -> None:
        semantic_files = (
            "english_pipeline/candidates.py",
            "english_pipeline/cli.py",
            "english_pipeline/constants.py",
            "english_pipeline/errors.py",
            "english_pipeline/events.py",
            "english_pipeline/formal.py",
            "english_pipeline/migrations.py",
            "english_pipeline/nightly.py",
            "english_pipeline/quick_flush.py",
            "english_pipeline/review_status.py",
            "english_pipeline/util.py",
            "english_pipeline/views.py",
            "english_pipeline/writer.py",
            "scripts/build_old_word_memory_curve_index.py",
            "scripts/build_review_status_proposals.py",
            "scripts/english_learning_pipeline.py",
            "scripts/select_bbdc_foundation.py",
            "schema/english_pipeline/capture-event-v2.schema.json",
            "schema/english_pipeline/luna-candidate-v2.schema.json",
        )
        historical_files = {
            "scripts/build_old_word_memory_curve_index.py": "evidence_only",
            "scripts/build_review_status_proposals.py": "replaced_by_current",
            "scripts/select_bbdc_foundation.py": "evidence_only",
        }

        def write_source(root: Path, relative: str, payload: bytes) -> None:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        math = self.base / "canonical-math"
        cs408 = self.base / "canonical-cs408"
        english = self.base / "canonical-english"
        historical = self.base / "bound-english-historical-source"
        for root, source, skill, contract in (
            (
                math,
                "数学一回滚复习系统/scripts/quick_intake.py",
                "skills/math/SKILL.md",
                "schema/math-capture.json",
            ),
            (
                cs408,
                "scripts/intake_fact_capture_408.py",
                "skills/cs408/SKILL.md",
                "schema/cs408-capture.json",
            ),
        ):
            write_source(root, source, b"# canonical source\n")
            write_source(root, skill, b"# canonical skill\n")
            write_source(root, contract, b"{}\n")

        english_closure_files = (
            "english_pipeline/__init__.py",
            "english_pipeline/candidates.py",
            "english_pipeline/cli.py",
            "english_pipeline/constants.py",
            "english_pipeline/errors.py",
            "english_pipeline/events.py",
            "english_pipeline/formal.py",
            "english_pipeline/migrations.py",
            "english_pipeline/nightly.py",
            "english_pipeline/producer_binding_attestation.py",
            "english_pipeline/quick_flush.py",
            "english_pipeline/review_status.py",
            "english_pipeline/util.py",
            "english_pipeline/views.py",
            "english_pipeline/writer.py",
            "scripts/english_learning_pipeline.py",
        )
        for relative in sorted(
            set(english_closure_files)
            | (set(semantic_files) - set(historical_files))
        ):
            payload = b"{}\n" if relative.endswith(".json") else b"# canonical English\n"
            write_source(english, relative, payload)
        write_source(
            english,
            "skills/english/SKILL.md",
            b"# canonical English skill\n",
        )
        write_source(
            english,
            "schema/english-capture.json",
            b"{}\n",
        )

        manifest_rows: list[dict[str, object]] = []
        for relative, classification in historical_files.items():
            payload = f"# bound historical source: {relative}\n".encode("utf-8")
            write_source(historical, relative, payload)
            manifest_rows.append(
                {
                    "path": relative,
                    "file_sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                    "classification": classification,
                    "replacement_reference": (
                        "english_pipeline/review_status.py"
                        if classification == "replaced_by_current"
                        else None
                    ),
                }
            )
        write_source(
            english,
            "schema/study-intake-historical-source-closure-v1.json",
            acceptance.canonical_bytes(
                {
                    "schema_version": (
                        "study-intake-historical-source-closure-v1"
                    ),
                    "files": manifest_rows,
                }
            ),
        )

        spec = {
            "subjects": {
                "math": {
                    "canonical_root": str(math),
                    "expected_head": "1" * 40,
                    "closure_files": [
                        "数学一回滚复习系统/scripts/quick_intake.py"
                    ],
                    "canonical_skill_file": "skills/math/SKILL.md",
                    "capture_contract_files": ["schema/math-capture.json"],
                },
                "cs408": {
                    "canonical_root": str(cs408),
                    "expected_head": "2" * 40,
                    "closure_files": ["scripts/intake_fact_capture_408.py"],
                    "canonical_skill_file": "skills/cs408/SKILL.md",
                    "capture_contract_files": ["schema/cs408-capture.json"],
                },
                "english": {
                    "canonical_root": str(english),
                    "expected_head": "3" * 40,
                    "closure_files": list(english_closure_files),
                    "canonical_skill_file": "skills/english/SKILL.md",
                    "capture_contract_files": [
                        "schema/english-capture.json"
                    ],
                    "semantic_runtime_source_root": str(historical),
                },
            }
        }
        closures, _heads = acceptance.materialize_canonical_closures(
            spec,
            self.producer,
        )
        english_runtime = closures["english"]
        schema = english_runtime / "schema/english_pipeline/luna-candidate-v2.schema.json"
        profile = {
            "enabled": True,
            "analysis_prompt_version": "english-analysis-test-v1",
            "critical_review_prompt_version": "english-review-test-v1",
            "analysis_output_schema": str(schema),
            "critical_review_output_schema": str(schema),
            "controlled_contract_path": str(schema),
            "max_prompt_bytes": 4096,
            "max_output_bytes": 4096,
        }
        base_config = {
            "runtime_root": str(self.runtime),
            "worker": {"model_timeout_seconds": 60},
            "model": {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
            "adapters": {
                "math": {"enabled": False, "repo_root": str(closures["math"])},
                "cs408": {
                    "enabled": False,
                    "repo_root": str(closures["cs408"]),
                },
                "english": {
                    "enabled": False,
                    "repo_root": str(english_runtime),
                    "candidate_schema": str(schema),
                },
            },
            preprocessor_core.ENGLISH_PROFILE: profile,
        }
        with (
            mock.patch.object(
                preprocessor_core,
                "release_identity",
                return_value=("a" * 64, "source_mode_test"),
            ),
            mock.patch.object(
                preprocessor_core,
                "subject_semantic_code_closure_manifest",
                return_value={"code_closure_sha256": "b" * 64},
            ),
            mock.patch.object(
                preprocessor_core,
                "subject_dispatch_bridge_code_closure_manifest",
                return_value={"code_closure_sha256": "c" * 64},
            ),
            mock.patch.object(
                preprocessor_core,
                "processing_plugin_contract",
                return_value=None,
            ),
            mock.patch.object(
                preprocessor_core,
                "make_adapters",
                return_value={name: object() for name in acceptance.SUBJECTS},
            ),
            mock.patch.object(
                preprocessor_core.CodexRunner,
                "_invoke_subprocess",
                side_effect=AssertionError("Provider path invoked"),
            ) as provider_call,
        ):
            for subject in ("math", "cs408"):
                with self.subTest(subject=subject):
                    config = copy.deepcopy(base_config)
                    config["source_acceptance_subject"] = subject
                    worker = preprocessor_core.Worker(
                        config,
                        model_runner=object(),
                        logger=mock.Mock(),
                    )
                    self.assertEqual(
                        worker.model_config[
                            "english_processing_contract"
                        ]["external_semantic_source_set_sha256"],
                        preprocessor_core.source_file_set_manifest(
                            english_runtime,
                            semantic_files,
                        )["file_set_sha256"],
                    )
        provider_call.assert_not_called()
        self.assertEqual(
            english_runtime.name,
            "kaoyan-english-runtime-closure",
        )
        for relative in semantic_files:
            self.assertTrue((english_runtime / relative).is_file(), relative)

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

    def test_bound_mcp_helper_imports_only_from_canonical_source_root(self) -> None:
        source = self._mcp_source()
        helper = source / "tests/helpers.py"
        module = acceptance.load_bound_module(
            "bound_mcp_helper",
            helper,
            acceptance.sha256_file(helper),
            import_root=source / "src",
        )
        self.assertTrue(module.HELPER)
        self.assertEqual(module.__version__, "1")

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
