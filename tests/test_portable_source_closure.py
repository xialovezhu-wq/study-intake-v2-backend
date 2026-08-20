from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RELEASE_MANAGER = ROOT / "scripts" / "release_manager.py"
SPEC = importlib.util.spec_from_file_location(
    "portable_release_manager", RELEASE_MANAGER
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

GENERATOR_PATH = (
    ROOT
    / "plugin"
    / "kaoyan-study-intake"
    / "scripts"
    / "generate_manifests.py"
)
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "portable_component_generator", GENERATOR_PATH
)
assert GENERATOR_SPEC and GENERATOR_SPEC.loader
generator = importlib.util.module_from_spec(GENERATOR_SPEC)
GENERATOR_SPEC.loader.exec_module(generator)


class PortableSourceClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ("math", "cs408", "english", "mcp"):
            (self.base / name).mkdir()
        for name in ("python", "mcp-python", "codex"):
            executable = self.base / "bin" / name
            executable.parent.mkdir(exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)

    def bindings(self) -> dict[str, Path]:
        return {
            "math_root": self.base / "math",
            "cs408_root": self.base / "cs408",
            "english_root": self.base / "english",
            "mcp_root": self.base / "mcp",
            "python_executable": self.base / "bin" / "python",
            "mcp_python_executable": self.base / "bin" / "mcp-python",
            "codex_executable": self.base / "bin" / "codex",
        }

    def test_template_is_portable_and_complete(self) -> None:
        raw = (ROOT / "config.example.json").read_text(encoding="utf-8")
        template = json.loads(raw)
        self.assertNotIn("/Users/", raw)
        self.assertNotIn("/home/oai/" + "share", raw)
        for marker in release.CONFIG_BINDING_MARKERS.values():
            self.assertIn(marker, raw)
        self.assertTrue(release._contains_config_marker(template))

    def test_bindings_fail_closed_and_resolve_all_markers(self) -> None:
        with self.assertRaisesRegex(
            release.ReleaseError, "release_config_bindings_incomplete"
        ):
            release.normalize_config_bindings({})
        normalized = release.normalize_config_bindings(self.bindings())
        template = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )
        rendered = release.substitute(
            template,
            release.config_binding_replacements(
                release_root=self.base / "release",
                runtime_data_root=str(self.base / "runtime"),
                bindings=normalized,
            ),
        )
        self.assertFalse(release._contains_config_marker(rendered))
        self.assertEqual(
            release.config_bindings_from_rendered(rendered), normalized
        )
        self.assertRegex(
            release.config_binding_sha256(normalized), r"^[0-9a-f]{64}$"
        )

    def test_build_cli_accepts_only_explicit_or_environment_bindings(self) -> None:
        environment = {
            release.CONFIG_BINDING_ENV[key]: str(value)
            for key, value in self.bindings().items()
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            args = release.parser().parse_args(["build", "--skip-tests"])
        for key, value in self.bindings().items():
            self.assertEqual(getattr(args, key), value)

    def test_release_manager_source_has_no_personal_literal(self) -> None:
        raw = RELEASE_MANAGER.read_text(encoding="utf-8")
        self.assertNotIn("/Users/" + "xiazhibin", raw)
        self.assertNotIn("/home/oai/" + "share", raw)

    def test_source_tree_has_no_machine_specific_home_literal(self) -> None:
        excluded = {ROOT / "handoff" / "REPOSITORY_BOOTSTRAP.md"}
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or path in excluded
                or ".git" in path.parts
                or "__pycache__" in path.parts
                or path.suffix
                not in {".py", ".md", ".json", ".toml", ".plist", ".yaml"}
            ):
                continue
            text = path.read_text(encoding="utf-8")
            if (
                "/Users/" + "xiazhibin" in text
                or "/home/oai/" + "share" in text
            ):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_component_registry_renders_from_explicit_synthetic_bindings(self) -> None:
        source_relatives = {
            "math": Path("数学一回滚复习系统/scripts/quick_intake.py"),
            "cs408": Path("scripts/intake_fact_capture_408.py"),
            "english": Path("english_pipeline/events.py"),
        }
        descriptor_relatives = {
            "math": Path("数学一回滚复习系统/schema/producer-binding-v1.json"),
            "cs408": Path("schema/producer-binding-v1.json"),
            "english": Path("schema/english_pipeline/producer-binding-v1.json"),
        }
        for subject in source_relatives:
            source = self.base / subject / source_relatives[subject]
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"synthetic {subject}\n", encoding="utf-8")
            descriptor = self.base / subject / descriptor_relatives[subject]
            descriptor.parent.mkdir(parents=True, exist_ok=True)
            descriptor.write_text(
                json.dumps(
                    {
                        "schema_version": "producer_binding_descriptor_v1",
                        "subject": subject,
                        "attestation_required_after": "2026-01-01T00:00:00+00:00",
                        "foreground_skill": {
                            "authoritative_sha256": "a" * 64
                        },
                        "producer": {"source_closure_sha256": "b" * 64},
                        "formal_write_count": 0,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        release_id = "c" * 64
        mcp_root = self.base / "mcp-releases" / release_id
        launcher = mcp_root / "scripts" / "sealed_launcher.py"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("# synthetic sealed launcher\n", encoding="utf-8")
        launcher_sha = generator.sha256(launcher)
        (mcp_root / "release.json").write_text(
            json.dumps(
                {
                    "release_id": release_id,
                    "server_release": f"0.0.0+sha256.{release_id}",
                    "source_files": {
                        "scripts/sealed_launcher.py": launcher_sha
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        rendered = generator.render_portable_registry(
            math_root=self.base / "math",
            cs408_root=self.base / "cs408",
            english_root=self.base / "english",
            mcp_root=mcp_root,
            mcp_python_executable=self.base / "bin" / "mcp-python",
        )
        raw = json.dumps(rendered, ensure_ascii=False, sort_keys=True)
        self.assertIsNone(generator.PORTABLE_MARKER_RE.search(raw))
        self.assertEqual(rendered["mcp"]["release_id"], release_id)
        self.assertEqual(
            rendered["external_runtime_sources"]["math_status_script"][
                "sha256"
            ],
            generator.sha256(self.base / "math" / source_relatives["math"]),
        )


if __name__ == "__main__":
    unittest.main()
