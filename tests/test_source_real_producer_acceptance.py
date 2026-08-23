#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import source_real_producer_acceptance as acceptance  # noqa: E402


class SourceRealProducerAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self._make_source_repo(self.root / "backend-latest")
        self.protected = self.root / "production-surface"
        self.protected.mkdir()
        (self.protected / "ledger.json").write_text("protected\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, *args: str, cwd: Path) -> str:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip()

    def _make_source_repo(self, path: Path) -> Path:
        path.mkdir(parents=True)
        (path / "lib").mkdir()
        (path / "lib/preprocessor_core.py").write_text(
            "SOURCE_ACCEPTANCE_TEST_MODULE = True\n", encoding="utf-8"
        )
        for subject in acceptance.SUBJECTS:
            subject_root = path / "subjects" / subject
            subject_root.mkdir(parents=True)
            (subject_root / "producer.py").write_text(
                f"SUBJECT = {subject!r}\n", encoding="utf-8"
            )
            (subject_root / "SKILL.md").write_text(
                f"{subject} test skill\n", encoding="utf-8"
            )
            (subject_root / "writer.py").write_text(
                "FORMAL_WRITES_DISABLED = True\n", encoding="utf-8"
            )
        for subject in acceptance.SUBJECTS:
            command = path / "subjects" / subject / "command.py"
            command.write_text(
                "import json, pathlib, sys\n"
                "if len(sys.argv) > 1:\n"
                "    pathlib.Path(sys.argv[1]).write_text('mutation\\n', encoding='utf-8')\n"
                "print(json.dumps({'real_model_call_count': 0, "
                "'provider_request_count': 0, 'formal_write_count': 0}))\n",
                encoding="utf-8",
            )
        failure_command = path / "subjects/math/failure_command.py"
        failure_command.write_text(
            "import json, sys\n"
            "print(json.dumps({'status': 'failed', "
            "'error_code': 'provider_failed_after_requests', "
            "'real_model_call_count': 2, 'provider_request_count': 7, "
            "'provider_pids': [4321], 'formal_write_count': 0}))\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        timeout_command = path / "subjects/math/timeout_command.py"
        timeout_command.write_text(
            "import os, pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL)\n"
            "root = pathlib.Path(os.environ["
            "'STUDY_SOURCE_ACCEPTANCE_EVIDENCE_ROOT'])\n"
            "(root / 'descendant.pid').write_text(str(child.pid), "
            "encoding='utf-8')\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        self._run("git", "init", "-q", cwd=path)
        self._run("git", "config", "user.email", "acceptance@example.invalid", cwd=path)
        self._run("git", "config", "user.name", "Source Acceptance Test", cwd=path)
        self._run("git", "add", ".", cwd=path)
        self._run("git", "commit", "-qm", "fixture", cwd=path)
        return path

    def _config(self, *, delay: float = 0.03) -> dict[str, object]:
        subjects: dict[str, object] = {}
        for subject in acceptance.SUBJECTS:
            subject_root = self.source / "subjects" / subject
            subjects[subject] = {
                "subject_root": str(subject_root),
                "producer_module_file": str(subject_root / "producer.py"),
                "skill_file": str(subject_root / "SKILL.md"),
                "writer_file": str(subject_root / "writer.py"),
                "executor": {
                    "adapter": "fixture",
                    "delay_seconds": delay,
                    "result": {"fixture_status": "completed"},
                },
            }
            if subject == "cs408":
                subjects[subject][
                    "private_root_contract"
                ] = "isolated_producer_root"
        return {
            "schema_version": acceptance.CONFIG_SCHEMA,
            "producer_mode": "canonical",
            "backend_module": "preprocessor_core",
            "shared_mcp": {
                "source_root": str(self.source),
                "module_file": str(self.source / "lib/preprocessor_core.py"),
            },
            "protected_paths": [str(self.protected)],
            "subjects": subjects,
        }

    def _accept(self, config: dict[str, object], name: str = "round") -> dict[str, object]:
        return acceptance.run_acceptance(
            source_root=self.source,
            expected_source_root=self.source,
            output_root=self.root / name,
            producer_mode="canonical",
            config=config,
            barrier_timeout_seconds=5,
        )

    def test_common_barrier_releases_only_after_all_three_ready(self) -> None:
        summary = self._accept(self._config())
        self.assertEqual("passed", summary["status"])
        self.assertTrue(summary["barrier"]["released_once"])
        self.assertEqual(
            {subject: "ready" for subject in acceptance.SUBJECTS},
            summary["barrier"]["states"],
        )
        released_at = summary["barrier"]["released_at"]
        for terminal in summary["matrix"].values():
            self.assertLessEqual(terminal["barrier_ready_at"], released_at)
            self.assertEqual(released_at, terminal["barrier_released_at"])

    def test_failure_does_not_cancel_siblings_and_all_reach_terminal(self) -> None:
        config = self._config(delay=0.12)
        config["subjects"]["math"]["executor"].update(
            {"delay_seconds": 0, "fail": True, "error_code": "injected_math_failure"}
        )
        summary = self._accept(config)
        self.assertEqual("failed", summary["status"])
        self.assertEqual("failed", summary["matrix"]["math"]["status"])
        self.assertEqual(
            "passed", summary["matrix"]["cs408"]["status"], summary
        )
        self.assertEqual(
            "passed", summary["matrix"]["english"]["status"], summary
        )
        self.assertFalse(summary["sibling_cancelled"])
        self.assertTrue(all("terminal_at" in row for row in summary["matrix"].values()))

    def test_manifest_is_deterministic_and_seals_source_identity(self) -> None:
        config = self._config()
        first = acceptance.build_source_manifest(
            source_root=self.source, producer_mode="canonical", config=config
        )
        second = acceptance.build_source_manifest(
            source_root=self.source, producer_mode="canonical", config=copy.deepcopy(config)
        )
        self.assertEqual(first, second)
        acceptance.verify_source_manifest(first)
        self.assertEqual(
            first["source_manifest_sha256"],
            acceptance.sha256_bytes(acceptance.canonical_bytes(first["core"])),
        )

    def test_child_manifest_verification_uses_no_git_operations(self) -> None:
        manifest = acceptance.build_source_manifest(
            source_root=self.source,
            producer_mode="canonical",
            config=self._config(),
        )
        with mock.patch.object(
            acceptance,
            "_git",
            side_effect=AssertionError("child git access forbidden"),
        ):
            acceptance.verify_source_manifest(manifest, verify_git=False)

    def test_noncanonical_dirty_subject_is_hash_bound_and_drift_checked(self) -> None:
        config = self._config()
        actual = self._make_source_repo(self.root / "actual-foreground")
        subject_root = actual / "subjects/math"
        writer = subject_root / "writer.py"
        writer.write_text(
            "FORMAL_WRITES_DISABLED = True\nACTUAL_WIP = True\n",
            encoding="utf-8",
        )
        config["subjects"]["math"].update(
            {
                "subject_root": str(subject_root),
                "producer_source_root": str(subject_root),
                "producer_module_file": str(subject_root / "producer.py"),
                "skill_file": str(subject_root / "SKILL.md"),
                "writer_file": str(writer),
            }
        )
        manifest = acceptance.build_source_manifest(
            source_root=self.source,
            producer_mode="canonical",
            config=config,
        )
        self.assertFalse(manifest["core"]["subjects"]["math"]["git"]["clean"])
        acceptance.verify_source_manifest(manifest)

        writer.write_text(
            "FORMAL_WRITES_DISABLED = True\nACTUAL_WIP = 'drift'\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError,
            "source_manifest_subject_(git|file)_drift",
        ):
            acceptance.verify_source_manifest(manifest)

    def test_old_release_and_dirty_source_roots_are_rejected(self) -> None:
        release_source = self._make_source_repo(self.root / "releases" / "old-backend")
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError, "forbidden_release_source_root"
        ):
            acceptance.git_identity(release_source)
        current_source = self._make_source_repo(self.root / "current" / "backend")
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError, "forbidden_release_source_root"
        ):
            acceptance.git_identity(current_source)
        wrong_source = self._make_source_repo(self.root / "wrong-backend")
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError, "backend_source_root_mismatch"
        ):
            acceptance.git_identity(wrong_source, expected_root=self.source)
        (self.source / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError, "backend_source_root_dirty"
        ):
            acceptance.git_identity(self.source)

    def test_cs408_private_root_mismatch_is_preflight_terminal(self) -> None:
        config = self._config()
        config["subjects"]["cs408"][
            "private_root_contract"
        ] = "different_root"
        summary = self._accept(config)
        self.assertEqual("failed", summary["status"])
        self.assertEqual(
            "cs408_private_root_mismatch",
            summary["matrix"]["cs408"]["error_code"],
        )
        self.assertEqual(
            "preflight_terminal", summary["barrier"]["states"]["cs408"]
        )
        self.assertEqual("passed", summary["matrix"]["math"]["status"])
        self.assertEqual("passed", summary["matrix"]["english"]["status"])

    def test_content_bound_tripwire_detects_protected_mutation(self) -> None:
        config = self._config()
        command_source = self.source / "subjects/math/command.py"
        config["subjects"]["math"]["executor"] = {
            "adapter": "command",
            "argv": [sys.executable, str(command_source), str(self.protected / "new-file")],
            "command_source": str(command_source),
            "real_provider": False,
        }
        summary = self._accept(config)
        self.assertEqual("failed", summary["status"])
        self.assertFalse(summary["protected_tripwire_pass"])
        self.assertEqual("passed", summary["matrix"]["math"]["status"])

    def test_protected_hashing_is_streamed_without_path_read_bytes(self) -> None:
        large = self.protected / "large.bin"
        large.write_bytes(b"x" * (2 * 1024 * 1024 + 17))
        with mock.patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("whole-file read forbidden"),
        ):
            signature = acceptance.path_signature(self.protected)
        file_row = next(
            row
            for row in signature["entries"]
            if row["relative_path"] == "large.bin"
        )
        self.assertEqual(
            file_row["sha256"],
            acceptance.sha256_bytes(b"x" * (2 * 1024 * 1024 + 17)),
        )

    def test_protected_hash_open_rejects_symlink_swap_race(self) -> None:
        protected = self.protected / "race.bin"
        target = self.root / "outside.bin"
        protected.write_bytes(b"inside")
        target.write_bytes(b"outside")
        real_open = os.open

        def swap_then_open(path, flags, *args, **kwargs):
            protected.unlink()
            protected.symlink_to(target)
            return real_open(path, flags, *args, **kwargs)

        with (
            mock.patch.object(os, "open", side_effect=swap_then_open),
            self.assertRaisesRegex(
                acceptance.SourceAcceptanceError,
                "protected_file_(invalid|unreadable)",
            ),
        ):
            acceptance.sha256_file_stream(protected)

    def test_same_size_restored_mtime_drift_changes_only_digest_evidence(
        self,
    ) -> None:
        protected = self.protected / "sentinel.txt"
        sentinel = "PRIVATE-STUDY-SENTINEL"
        protected.write_text(sentinel, encoding="utf-8")
        original_stat = protected.stat()
        before = acceptance.path_signature(self.protected)
        protected.write_text("X" * len(sentinel), encoding="utf-8")
        os.utime(
            protected,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        after = acceptance.path_signature(self.protected)

        self.assertNotEqual(
            acceptance.sha256_bytes(acceptance.canonical_bytes(before)),
            acceptance.sha256_bytes(acceptance.canonical_bytes(after)),
        )
        self.assertNotIn(sentinel, json.dumps(before, ensure_ascii=False))

    def test_failed_command_preserves_provider_counters_in_round_summary(self) -> None:
        config = self._config()
        failure = self.source / "subjects/math/failure_command.py"
        config["subjects"]["math"]["executor"] = {
            "adapter": "command",
            "argv": [sys.executable, str(failure)],
            "command_source": str(failure),
            "real_provider": True,
        }
        summary = acceptance.run_acceptance(
            source_root=self.source,
            output_root=self.root / "failed-command-round",
            producer_mode="canonical",
            config=config,
            enable_real_provider=True,
            barrier_timeout_seconds=5,
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(2, summary["real_model_call_count"])
        self.assertEqual(7, summary["provider_request_count"])
        self.assertEqual(
            2,
            summary["matrix"]["math"]["result"]["real_model_call_count"],
        )

    def test_timeout_kills_command_process_group_and_reports_unknown_counts(
        self,
    ) -> None:
        config = self._config()
        command = self.source / "subjects/math/timeout_command.py"
        config["subjects"]["math"]["executor"] = {
            "adapter": "command",
            "argv": [sys.executable, str(command)],
            "command_source": str(command),
            "real_provider": True,
            "timeout_seconds": 0.2,
        }
        summary = acceptance.run_acceptance(
            source_root=self.source,
            output_root=self.root / "timeout-command-round",
            producer_mode="canonical",
            config=config,
            enable_real_provider=True,
            barrier_timeout_seconds=5,
        )
        self.assertEqual("failed", summary["status"])
        result = summary["matrix"]["math"]["result"]
        self.assertEqual("unknown", result["counter_observation_status"])
        self.assertIsNone(summary["real_model_call_count"])
        self.assertIsNone(summary["provider_request_count"])
        pid_path = (
            self.root
            / "timeout-command-round/subjects/math/evidence/descendant.pid"
        )
        descendant_pid = int(pid_path.read_text(encoding="utf-8"))
        time.sleep(0.1)
        observed = subprocess.run(
            ["ps", "-p", str(descendant_pid), "-o", "pid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        self.assertEqual("", observed.stdout.strip())

    def test_barrier_observation_timeout_cannot_pass_round(self) -> None:
        config = self._config()
        config["subjects"]["math"]["preflight_delay_seconds"] = 0.2
        summary = acceptance.run_acceptance(
            source_root=self.source,
            output_root=self.root / "barrier-timeout-round",
            producer_mode="canonical",
            config=config,
            barrier_timeout_seconds=0.05,
        )
        self.assertEqual("failed", summary["status"])
        self.assertFalse(summary["barrier_observation_complete"])
        self.assertEqual(
            "barrier_observation_timeout",
            summary["barrier"]["states"]["math"],
        )

    def test_output_root_cannot_overlap_production_path(self) -> None:
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError, "output_root_overlaps_protected_path"
        ):
            acceptance.run_acceptance(
                source_root=self.source,
                output_root=self.protected / "acceptance-output",
                producer_mode="canonical",
                config=self._config(),
            )

    def test_matrix_is_one_round_with_same_manifest_and_no_formal_writes(self) -> None:
        summary = self._accept(self._config(delay=0.15))
        self.assertTrue(summary["same_round_all_three"])
        self.assertTrue(summary["same_source_manifest_all_three"])
        self.assertTrue(summary["same_harness_all_three"])
        self.assertTrue(summary["intervals_overlapped"])
        round_ids = {row["round_id"] for row in summary["matrix"].values()}
        self.assertEqual({summary["round_id"]}, round_ids)
        acceptance.assert_recursive_formal_write_zero(summary)

    def test_actual_skill_requires_sealed_commands_and_explicit_provider_enable(self) -> None:
        config = self._config()
        config["producer_mode"] = "actual_skill"
        with self.assertRaisesRegex(
            acceptance.SourceAcceptanceError, "actual_skill_requires_declared_command"
        ):
            acceptance.build_source_manifest(
                source_root=self.source,
                producer_mode="actual_skill",
                config=config,
            )
        for subject in acceptance.SUBJECTS:
            command = self.source / "subjects" / subject / "command.py"
            config["subjects"][subject]["executor"] = {
                "adapter": "command",
                "argv": [sys.executable, str(command)],
                "command_source": str(command),
                "actual_skill_declared_command": True,
                "real_provider": subject == "math",
            }
        summary = acceptance.run_acceptance(
            source_root=self.source,
            output_root=self.root / "actual-skill-round",
            producer_mode="actual_skill",
            config=config,
            enable_real_provider=False,
            barrier_timeout_seconds=5,
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(
            "real_provider_not_explicitly_enabled",
            summary["matrix"]["math"]["error_code"],
        )
        self.assertEqual(0, summary["real_model_call_count"])
        self.assertEqual(0, summary["provider_request_count"])


if __name__ == "__main__":
    unittest.main()
