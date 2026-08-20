"""Hermetic generated plugin fixture shared by backend tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PortablePluginFixture:
    source_root: Path
    plugin_root: Path
    mcp_source_root: Path
    mcp_root: Path
    mcp_python: Path
    release_id: str
    subject_roots: dict[str, Path]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _subject_fixture(root: Path, subject: str) -> Path:
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
    source = root / source_relatives[subject]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        f"# synthetic portable {subject} Producer\n", encoding="utf-8"
    )
    authoritative = root / "skills" / f"{subject}-foreground" / "SKILL.md"
    installed = root / "installed" / f"{subject}-foreground" / "SKILL.md"
    for skill in (authoritative, installed):
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            f"---\nname: {subject}-foreground\n---\nsynthetic\n",
            encoding="utf-8",
        )
    contract = root / "contracts" / "capture.json"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text('{"synthetic":true}\n', encoding="utf-8")
    source = source.resolve()
    authoritative = authoritative.resolve()
    installed = installed.resolve()
    contract = contract.resolve()
    source_rows = [{"path": str(source), "sha256": _sha(source)}]
    core = {
        "schema_version": "producer_binding_descriptor_v1",
        "subject": subject,
        "attestation_required_after": "2026-01-01T00:00:00+00:00",
        "foreground_skill": {
            "authoritative_path": str(authoritative),
            "authoritative_sha256": _sha(authoritative),
            "installed_path": str(installed),
            "installed_sha256": _sha(installed),
        },
        "producer": {
            "source_files": source_rows,
            "source_closure_sha256": hashlib.sha256(
                _canonical(source_rows)
            ).hexdigest(),
        },
        "capture_contract": {
            "files": [{"path": str(contract), "sha256": _sha(contract)}]
        },
        "attestation_relative_root": "attestations",
        "formal_write_count": 0,
    }
    descriptor = {
        **core,
        "descriptor_content_sha256": hashlib.sha256(
            _canonical(core)
        ).hexdigest(),
    }
    descriptor_path = root / descriptor_relatives[subject]
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_bytes(_canonical(descriptor))
    return root.resolve()


def build_portable_plugin_fixture(
    base: Path,
    backend_root: Path,
    *,
    mcp_python: Path | None = None,
) -> PortablePluginFixture:
    prepared = base / "backend"
    shutil.copytree(
        backend_root,
        prepared,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            "components.json",
            "component-lock.json",
            ".mcp.json",
            "kaoyan-read",
        ),
    )
    mcp_source = Path(
        os.environ.get(
            "STUDY_READ_MCP_SOURCE_ROOT",
            str(backend_root.parent / "local-study-read-mcp"),
        )
    ).resolve()
    builder = mcp_source / "scripts" / "build_release.py"
    if not builder.is_file():
        raise AssertionError("portable shared MCP source is required")
    mcp_base = base / "mcp-releases"
    built = subprocess.run(
        [sys.executable, str(builder), "--release-base", str(mcp_base)],
        cwd=mcp_source,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        raise AssertionError("portable shared MCP build failed: " + built.stderr)
    result = json.loads(built.stdout)
    mcp_root = Path(result["release_dir"]).resolve()
    bound_python = (mcp_python or Path(sys.executable)).expanduser().absolute()
    helper_path = mcp_source / "tests" / "helpers.py"
    helper_spec = importlib.util.spec_from_file_location(
        "portable_mcp_test_helpers", helper_path
    )
    if helper_spec is None or helper_spec.loader is None:
        raise AssertionError("portable MCP synthetic helper is required")
    source_entry = str(mcp_source / "src")
    sys.path.insert(0, source_entry)
    try:
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        repositories = helper.make_fixture(base / "subject-data")
    finally:
        if sys.path[0] == source_entry:
            sys.path.pop(0)
    subject_roots = {
        "math": _subject_fixture(repositories.math_root, "math"),
        "cs408": _subject_fixture(repositories.cs408_root, "cs408"),
        "english": _subject_fixture(repositories.english_root, "english"),
    }
    generator = (
        prepared / "plugin/kaoyan-study-intake/scripts/generate_manifests.py"
    )
    generated = subprocess.run(
        [
            sys.executable,
            str(generator),
            "--math-root",
            str(subject_roots["math"]),
            "--cs408-root",
            str(subject_roots["cs408"]),
            "--english-root",
            str(subject_roots["english"]),
            "--mcp-root",
            str(mcp_root),
            "--mcp-python-executable",
            str(bound_python),
        ],
        cwd=generator.parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if generated.returncode != 0:
        raise AssertionError(
            "portable plugin generation failed: " + generated.stderr
        )
    return PortablePluginFixture(
        source_root=prepared,
        plugin_root=prepared / "plugin/kaoyan-study-intake",
        mcp_source_root=mcp_source,
        mcp_root=mcp_root,
        mcp_python=bound_python,
        release_id=str(result["release_id"]),
        subject_roots=subject_roots,
    )
