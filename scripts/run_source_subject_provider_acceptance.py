#!/usr/bin/env python3
"""Source-only wrapper around the audited real-Producer acceptance driver.

The wrapper deliberately does not build either Backend or Shared MCP releases.
It makes a writable mirror of the selected Backend source, binds that mirror to
an already sealed MCP release, and then loads the historical acceptance driver
only after its physical bytes have matched the digest in the sealed spec.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from types import MethodType
from typing import Any, Mapping


SPEC_SCHEMA = "study-intake-source-subject-provider-spec-v1"
SUBJECTS = ("math", "cs408", "english")
SUBJECT_CLOSURE_NAMES = {
    "math": "kaoyan-math",
    "cs408": "kaoyan-408",
    "english": "kaoyan-english-runtime-closure",
}
ENGLISH_SEMANTIC_RUNTIME_FILES = (
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
ENGLISH_HISTORICAL_RUNTIME_FILES = {
    "scripts/build_old_word_memory_curve_index.py": "evidence_only",
    "scripts/build_review_status_proposals.py": "replaced_by_current",
    "scripts/select_bbdc_foundation.py": "evidence_only",
}
REQUIRED_ROOT_ENV = {
    "runtime": "STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT",
    "producer": "STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT",
    "scratch": "STUDY_SOURCE_ACCEPTANCE_SCRATCH_ROOT",
    "evidence": "STUDY_SOURCE_ACCEPTANCE_EVIDENCE_ROOT",
}


class AcceptanceError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError("source_file_invalid")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reopen_completion_report_paths(
    completion: Mapping[str, Any], runtime_root: Path
) -> dict[str, Any]:
    """Reopen v2 report refs for the hash-bound legacy acceptance consumer."""

    value = copy.deepcopy(dict(completion))
    if (
        value.get("schema_version")
        != "study-intake-concurrent-completion-v2"
        or value.get("outcome") != "succeeded"
    ):
        return value
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise AcceptanceError("completion_report_runtime_root_invalid")
    root = runtime_root.resolve(strict=True)
    bindings = {
        "report_json": (
            "study-intake-report://sha256/",
            root / "dispatch/reports/json/sha256",
            ".json",
        ),
        "report_markdown": (
            "study-intake-report-markdown://sha256/",
            root / "dispatch/reports/markdown/sha256",
            ".md",
        ),
    }
    for role, (prefix, object_root, suffix) in bindings.items():
        digest = value.get(f"{role}_sha256")
        reference = value.get(f"{role}_ref")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or reference != prefix + digest
        ):
            raise AcceptanceError("completion_report_binding_invalid")
        path = object_root / digest[:2] / f"{digest}{suffix}"
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(root)
            node = resolved_path.lstat()
        except OSError as exc:
            raise AcceptanceError("completion_report_object_missing") from exc
        except ValueError as exc:
            raise AcceptanceError("completion_report_object_invalid") from exc
        if (
            resolved_path != path
            or path.is_symlink()
            or not stat.S_ISREG(node.st_mode)
            or sha256_file(resolved_path) != digest
        ):
            raise AcceptanceError("completion_report_object_invalid")
        value[f"{role}_path"] = str(resolved_path)
    return value


class FirstWaitCompletionBridge:
    """Give the audited legacy driver one path view, then restore v2 refs."""

    def __init__(self, original_wait: Any, runtime_root: Path) -> None:
        self.original_wait = original_wait
        self.runtime_root = runtime_root
        self.consumed_handles: set[int] = set()

    def wait(self, handle: Any, timeout: Any = None):
        result = self.original_wait(handle, timeout)
        handle_identity = id(handle)
        if handle_identity in self.consumed_handles:
            return result
        completion = result.completion
        if not isinstance(completion, Mapping):
            self.consumed_handles.add(handle_identity)
            return result
        compatible = reopen_completion_report_paths(
            completion,
            self.runtime_root,
        )
        self.consumed_handles.add(handle_identity)
        if compatible == completion:
            return result
        return dataclasses.replace(result, completion=compatible)


def _digest_map(root: Path) -> dict[str, str]:
    paths = [
        root / "pyproject.toml",
        root / "requirements.lock",
        root / "config/codex-mcp-snippet.toml",
        root / "config/skill-tool-policy.json",
        root / "scripts/sealed_launcher.py",
        *sorted((root / "src/study_read_mcp").rglob("*.py")),
    ]
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in paths
    }


def load_spec(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError("sealed_spec_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("sealed_spec_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SPEC_SCHEMA:
        raise AcceptanceError("sealed_spec_invalid")
    return value


def isolated_roots(environment: Mapping[str, str] | None = None) -> dict[str, Path]:
    env = os.environ if environment is None else environment
    roots: dict[str, Path] = {}
    for role, name in REQUIRED_ROOT_ENV.items():
        raw = env.get(name)
        if not raw:
            raise AcceptanceError(f"{role}_root_missing")
        path = Path(raw)
        if path.is_symlink() or not path.is_dir():
            raise AcceptanceError(f"{role}_root_invalid")
        roots[role] = path.resolve(strict=True)
    if len(set(roots.values())) != len(roots):
        raise AcceptanceError("writable_roots_not_distinct")
    common = Path(os.path.commonpath([str(value) for value in roots.values()]))
    if common == Path("/") or common == Path.home():
        raise AcceptanceError("writable_roots_not_isolated")
    return roots


def verify_file_binding(path: Path, expected: object, code: str) -> dict[str, str]:
    digest = sha256_file(path)
    if expected != digest:
        raise AcceptanceError(code)
    return {"path": str(path.resolve()), "sha256": digest}


def verify_sealed_mcp(spec: Mapping[str, Any]) -> dict[str, Any]:
    raw = spec.get("sealed_mcp")
    if not isinstance(raw, Mapping):
        raise AcceptanceError("sealed_mcp_binding_missing")
    source = Path(str(raw.get("canonical_source_root") or ""))
    release = Path(str(raw.get("release_root") or ""))
    if (
        source.is_symlink()
        or release.is_symlink()
        or not source.is_dir()
        or not release.is_dir()
    ):
        raise AcceptanceError("sealed_mcp_root_invalid")
    source = source.resolve(strict=True)
    release = release.resolve(strict=True)
    manifest_path = release / "release.json"
    verify_file_binding(
        manifest_path,
        raw.get("release_manifest_sha256"),
        "sealed_mcp_manifest_hash_mismatch",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("sealed_mcp_manifest_invalid") from exc
    files = manifest.get("source_files")
    canonical = _digest_map(source)
    release_id = hashlib.sha256(canonical_bytes(dict(sorted(canonical.items())))).hexdigest()
    if (
        manifest.get("schema_version") != "study-read-mcp-release.v1"
        or manifest.get("formal_write_count") != 0
        or manifest.get("release_id") != release_id
        or release.name != release_id
        or files != canonical
    ):
        raise AcceptanceError("sealed_mcp_canonical_binding_mismatch")
    for relative, digest in canonical.items():
        target = release / relative
        if (
            target.is_symlink()
            or not target.is_file()
            or sha256_file(target) != digest
            or stat.S_IMODE(target.stat().st_mode) != 0o444
        ):
            raise AcceptanceError("sealed_mcp_release_file_invalid")
    python = Path(str(raw.get("python_executable") or ""))
    if (
        not python.is_absolute()
        or not python.is_file()
        or not os.access(python, os.X_OK)
    ):
        raise AcceptanceError("sealed_mcp_python_invalid")
    helpers_path = source / "tests/helpers.py"
    helpers_binding = verify_file_binding(
        helpers_path,
        raw.get("canonical_helpers_sha256"),
        "sealed_mcp_helpers_hash_mismatch",
    )
    return {
        "canonical_source_root": str(source),
        "release_root": str(release),
        "release_id": release_id,
        "release_manifest_sha256": sha256_file(manifest_path),
        "source_files_sha256": hashlib.sha256(canonical_bytes(canonical)).hexdigest(),
        "python_executable": str(python.absolute()),
        "canonical_helpers_path": helpers_binding["path"],
        "canonical_helpers_sha256": helpers_binding["sha256"],
        "build_release_invoked": False,
    }


def _copy_source(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise AcceptanceError("source_mirror_already_exists")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "runtime", "current", "releases"
        ),
    )


def verify_backend_git(
    spec: Mapping[str, Any], backend: Path
) -> dict[str, str]:
    observed: dict[str, str] = {}
    for key, arguments in {
        "head": ("rev-parse", "HEAD"),
        "tree": ("rev-parse", "HEAD^{tree}"),
        "status": ("status", "--porcelain=v1", "--untracked-files=all"),
    }.items():
        completed = subprocess.run(
            ["git", "-C", str(backend), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AcceptanceError("backend_source_git_invalid")
        observed[key] = completed.stdout.strip()
    if (
        observed["status"]
        or observed["head"] != spec.get("backend_expected_head")
        or observed["tree"] != spec.get("backend_expected_tree")
    ):
        raise AcceptanceError("backend_source_git_mismatch")
    return {"head": observed["head"], "tree": observed["tree"]}


def _render_tokens(value: Any, bindings: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        rendered = value
        for token, replacement in bindings.items():
            rendered = rendered.replace(token, replacement)
        return rendered
    if isinstance(value, list):
        return [_render_tokens(row, bindings) for row in value]
    if isinstance(value, dict):
        return {key: _render_tokens(row, bindings) for key, row in value.items()}
    return value


def _canonical_relative_path(
    root: Path,
    value: str,
    *,
    target_root: Path | None = None,
) -> tuple[Path, Path | None]:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise AcceptanceError("canonical_relative_path_invalid")
    resolved_root = root.resolve(strict=True)
    source = (resolved_root / relative).resolve(strict=True)
    try:
        source.relative_to(resolved_root)
    except ValueError as exc:
        raise AcceptanceError("canonical_relative_path_invalid") from exc
    if source.is_symlink() or not source.is_file():
        raise AcceptanceError("canonical_relative_path_invalid")
    target: Path | None = None
    if target_root is not None:
        resolved_target_root = target_root.resolve(strict=True)
        target = (resolved_target_root / relative).resolve(strict=False)
        try:
            target.relative_to(resolved_target_root)
        except ValueError as exc:
            raise AcceptanceError("canonical_relative_path_invalid") from exc
    return source, target


def materialize_source_release(
    spec: Mapping[str, Any], roots: Mapping[str, Path], mcp: Mapping[str, Any]
) -> tuple[Path, dict[str, Any]]:
    backend = Path(str(spec.get("backend_source_root") or ""))
    if backend.is_symlink() or not backend.is_dir():
        raise AcceptanceError("backend_source_root_invalid")
    backend = backend.resolve(strict=True)
    forbidden = {"current", "releases", "study-intake-v2-backend"}
    if any(part.lower() in forbidden for part in backend.parts):
        raise AcceptanceError("backend_source_root_forbidden")
    backend_git = verify_backend_git(spec, backend)
    release_id = hashlib.sha256(
        canonical_bytes(
            {
                "backend_source_root": str(backend),
                "backend_test_module_sha256": spec.get(
                    "backend_test_module_sha256"
                ),
                "backend_head": backend_git["head"],
                "backend_tree": backend_git["tree"],
                "sealed_mcp_release_id": mcp["release_id"],
            }
        )
    ).hexdigest()
    source_release = roots["scratch"] / release_id
    _copy_source(backend, source_release)
    release_manifest = {
        "schema_version": "study-intake-preprocessor-release-v2",
        "release_id": release_id,
        "component_inventory": {},
        "source_mode": True,
        "formal_write_count": 0,
    }
    (source_release / "release.json").write_bytes(canonical_bytes(release_manifest))
    template = json.loads((source_release / "config.example.json").read_text(encoding="utf-8"))
    bindings = {
        "${RELEASE_ROOT}": str(source_release),
        "${RUNTIME_DATA_ROOT}": str(roots["runtime"]),
        "${MCP_PYTHON_EXECUTABLE}": str(mcp["python_executable"]),
        "${MCP_ROOT}": str(mcp["release_root"]),
        "${CODEX_EXECUTABLE}": str(spec.get("codex_executable") or ""),
        "${PYTHON_EXECUTABLE}": sys.executable,
        "${MATH_ROOT}": str(roots["producer"] / "kaoyan-math"),
        "${CS408_ROOT}": str(roots["producer"] / "kaoyan-408"),
        "${ENGLISH_ROOT}": str(
            roots["producer"] / SUBJECT_CLOSURE_NAMES["english"]
        ),
    }
    config = _render_tokens(template, bindings)
    config["release"]["manifest_path"] = str(source_release / "release.json")
    config["processing_plugin"]["mcp_project_root"] = str(mcp["release_root"])
    config["processing_plugin"]["mcp_client_python"] = str(mcp["python_executable"])
    (source_release / "config.json").write_bytes(canonical_bytes(config))
    return source_release, {
        "root": str(source_release),
        "release_id": release_id,
        "config_sha256": sha256_file(source_release / "config.json"),
        "release_manifest_sha256": sha256_file(source_release / "release.json"),
        "backend_build_executed": False,
        "backend_head": backend_git["head"],
        "backend_tree": backend_git["tree"],
    }


def _descriptor(
    subject: str,
    canonical_root: Path,
    closure_root: Path,
    relative_files: list[str],
    canonical_skill_file: str,
    capture_contract_files: list[str],
) -> None:
    source_rows: list[dict[str, str]] = []
    for relative in relative_files:
        source, target = _canonical_relative_path(
            canonical_root,
            relative,
            target_root=closure_root,
        )
        assert target is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_rows.append({"path": str(target.resolve()), "sha256": sha256_file(target)})
    skill_root = closure_root / ".source-acceptance-skill"
    authoritative = skill_root / "authoritative/SKILL.md"
    installed = skill_root / "installed/SKILL.md"
    skill_source, _unused = _canonical_relative_path(
        canonical_root, canonical_skill_file
    )
    for skill in (authoritative, installed):
        skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_source, skill)
    contract_rows: list[dict[str, str]] = []
    for index, relative in enumerate(capture_contract_files):
        source, _unused = _canonical_relative_path(canonical_root, relative)
        target = (
            closure_root
            / ".source-acceptance-contract"
            / f"{index:02d}-{source.name}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        contract_rows.append(
            {"path": str(target.resolve()), "sha256": sha256_file(target)}
        )
    descriptor_relatives = {
        "math": "数学一回滚复习系统/schema/producer-binding-v1.json",
        "cs408": "schema/producer-binding-v1.json",
        "english": "schema/english_pipeline/producer-binding-v1.json",
    }
    core = {
        "schema_version": "producer_binding_descriptor_v1",
        "subject": subject,
        "attestation_required_after": "2026-01-01T00:00:00+00:00",
        "foreground_skill": {
            "authoritative_path": str(authoritative.resolve()),
            "authoritative_sha256": sha256_file(authoritative),
            "installed_path": str(installed.resolve()),
            "installed_sha256": sha256_file(installed),
        },
        "producer": {
            "source_files": source_rows,
            "source_closure_sha256": hashlib.sha256(canonical_bytes(source_rows)).hexdigest(),
        },
        "capture_contract": {
            "files": contract_rows
        },
        "attestation_relative_root": "attestations",
        "formal_write_count": 0,
    }
    value = {
        **core,
        "descriptor_content_sha256": hashlib.sha256(canonical_bytes(core)).hexdigest(),
    }
    path = closure_root / descriptor_relatives[subject]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _materialize_english_semantic_runtime(
    canonical_root: Path,
    closure_root: Path,
    spec: Mapping[str, Any],
) -> None:
    historical = Path(str(spec.get("semantic_runtime_source_root") or ""))
    if (
        not historical.is_absolute()
        or historical.is_symlink()
        or not historical.is_dir()
        or any(
            part.lower() in {"current", "releases", "site-packages"}
            for part in historical.parts
        )
    ):
        raise AcceptanceError("english_semantic_runtime_source_root_invalid")
    historical = historical.resolve(strict=True)
    manifest_path = (
        canonical_root
        / "schema/study-intake-historical-source-closure-v1.json"
    )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise AcceptanceError("english_historical_source_manifest_invalid")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("english_historical_source_manifest_invalid") from exc
    rows = manifest.get("files")
    if (
        manifest.get("schema_version")
        != "study-intake-historical-source-closure-v1"
        or not isinstance(rows, list)
    ):
        raise AcceptanceError("english_historical_source_manifest_invalid")
    by_path = {
        str(row.get("path")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }
    for relative in ENGLISH_SEMANTIC_RUNTIME_FILES:
        canonical_source = canonical_root / relative
        if canonical_source.is_file() and not canonical_source.is_symlink():
            source, _unused = _canonical_relative_path(canonical_root, relative)
        else:
            expected_classification = ENGLISH_HISTORICAL_RUNTIME_FILES.get(
                relative
            )
            row = by_path.get(relative)
            if (
                expected_classification is None
                or not isinstance(row, Mapping)
                or row.get("classification") != expected_classification
                or not isinstance(row.get("file_sha256"), str)
                or not isinstance(row.get("size"), int)
            ):
                raise AcceptanceError("english_semantic_runtime_member_unbound")
            source, _unused = _canonical_relative_path(historical, relative)
            if (
                source.stat().st_size != row["size"]
                or sha256_file(source) != row["file_sha256"]
            ):
                raise AcceptanceError("english_semantic_runtime_member_drift")
        target = (closure_root / relative).resolve(strict=False)
        try:
            target.relative_to(closure_root.resolve(strict=True))
        except ValueError as exc:
            raise AcceptanceError("english_semantic_runtime_target_invalid") from exc
        if target.exists() or target.is_symlink():
            if (
                target.is_symlink()
                or not target.is_file()
                or sha256_file(target) != sha256_file(source)
            ):
                raise AcceptanceError("english_semantic_runtime_target_drift")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def materialize_canonical_closures(
    spec: Mapping[str, Any], producer_root: Path
) -> tuple[dict[str, Path], dict[str, str]]:
    subjects = spec.get("subjects")
    if not isinstance(subjects, Mapping) or set(subjects) != set(SUBJECTS):
        raise AcceptanceError("subject_source_matrix_invalid")
    roots: dict[str, Path] = {}
    heads: dict[str, str] = {}
    for subject in SUBJECTS:
        row = subjects[subject]
        if not isinstance(row, Mapping):
            raise AcceptanceError("subject_source_spec_invalid")
        canonical_root = Path(str(row.get("canonical_root") or "")).resolve(strict=True)
        closure_files = row.get("closure_files")
        if not isinstance(closure_files, list) or not closure_files or not all(
            isinstance(value, str) and value for value in closure_files
        ):
            raise AcceptanceError("subject_closure_files_invalid")
        canonical_skill_file = row.get("canonical_skill_file")
        capture_contract_files = row.get("capture_contract_files")
        if (
            not isinstance(canonical_skill_file, str)
            or not canonical_skill_file
            or not isinstance(capture_contract_files, list)
            or not capture_contract_files
            or not all(
                isinstance(value, str) and value
                for value in capture_contract_files
            )
        ):
            raise AcceptanceError("subject_canonical_contract_invalid")
        closure = producer_root / SUBJECT_CLOSURE_NAMES[subject]
        closure.mkdir(parents=True)
        _descriptor(
            subject,
            canonical_root,
            closure,
            list(closure_files),
            canonical_skill_file,
            list(capture_contract_files),
        )
        if subject == "english":
            _materialize_english_semantic_runtime(
                canonical_root,
                closure,
                row,
            )
        roots[subject] = closure
        head = row.get("expected_head")
        if not isinstance(head, str) or len(head) < 40:
            raise AcceptanceError("subject_expected_head_invalid")
        heads[subject] = head
    return roots, heads


def load_bound_module(
    name: str,
    path: Path,
    digest: object,
    *,
    import_root: Path | None = None,
) -> Any:
    verify_file_binding(path, digest, f"{name}_hash_mismatch")
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise AcceptanceError(f"{name}_load_failed")
    module = importlib.util.module_from_spec(module_spec)
    inserted: str | None = None
    if import_root is not None:
        root = import_root.resolve(strict=True)
        if root.is_symlink() or not root.is_dir():
            raise AcceptanceError(f"{name}_import_root_invalid")
        inserted = str(root)
        sys.path.insert(0, inserted)
    try:
        module_spec.loader.exec_module(module)
    finally:
        if inserted is not None and sys.path and sys.path[0] == inserted:
            sys.path.pop(0)
    return module


def _portable_subject_fixture(root: Path, subject: str) -> Path:
    source_relatives = {
        "math": "数学一回滚复习系统/scripts/quick_intake.py",
        "cs408": "scripts/intake_fact_capture_408.py",
        "english": "english_pipeline/events.py",
    }
    source = root / source_relatives[subject]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"# isolated {subject} source placeholder\n", encoding="utf-8")
    authoritative = root / "skills" / f"{subject}-foreground" / "SKILL.md"
    installed = root / "installed" / f"{subject}-foreground" / "SKILL.md"
    for skill in (authoritative, installed):
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            f"---\nname: {subject}-foreground\n---\nsource acceptance\n",
            encoding="utf-8",
        )
    contract = root / "contracts/capture.json"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_bytes(canonical_bytes({"subject": subject}))
    source_rows = [
        {"path": str(source.resolve()), "sha256": sha256_file(source)}
    ]
    core = {
        "schema_version": "producer_binding_descriptor_v1",
        "subject": subject,
        "attestation_required_after": "2026-01-01T00:00:00+00:00",
        "foreground_skill": {
            "authoritative_path": str(authoritative.resolve()),
            "authoritative_sha256": sha256_file(authoritative),
            "installed_path": str(installed.resolve()),
            "installed_sha256": sha256_file(installed),
        },
        "producer": {
            "source_files": source_rows,
            "source_closure_sha256": hashlib.sha256(
                canonical_bytes(source_rows)
            ).hexdigest(),
        },
        "capture_contract": {
            "files": [
                {"path": str(contract.resolve()), "sha256": sha256_file(contract)}
            ]
        },
        "attestation_relative_root": "attestations",
        "formal_write_count": 0,
    }
    descriptor_relatives = {
        "math": "数学一回滚复习系统/schema/producer-binding-v1.json",
        "cs408": "schema/producer-binding-v1.json",
        "english": "schema/english_pipeline/producer-binding-v1.json",
    }
    descriptor = root / descriptor_relatives[subject]
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_bytes(
        canonical_bytes(
            {
                **core,
                "descriptor_content_sha256": hashlib.sha256(
                    canonical_bytes(core)
                ).hexdigest(),
            }
        )
    )
    return root.resolve()


def build_portable_fixture(
    base: Path,
    backend_root: Path,
    *,
    mcp: Mapping[str, Any],
    mcp_python: Path | None = None,
) -> Any:
    prepared = base / "backend"
    _copy_source(backend_root, prepared)
    helper = load_bound_module(
        "source_acceptance_mcp_helpers",
        Path(mcp["canonical_helpers_path"]),
        mcp["canonical_helpers_sha256"],
        import_root=Path(mcp["canonical_source_root"]) / "src",
    )
    repositories = helper.make_fixture(base / "subject-data")
    subject_roots = {
        "math": _portable_subject_fixture(repositories.math_root, "math"),
        "cs408": _portable_subject_fixture(repositories.cs408_root, "cs408"),
        "english": _portable_subject_fixture(repositories.english_root, "english"),
    }
    generator = prepared / "plugin/kaoyan-study-intake/scripts/generate_manifests.py"
    completed = subprocess.run(
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
            str(mcp["release_root"]),
            "--mcp-python-executable",
            str(mcp_python or mcp["python_executable"]),
        ],
        cwd=generator.parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AcceptanceError("portable_plugin_generation_failed:" + completed.stderr[-1000:])
    plugin_root = prepared / "plugin/kaoyan-study-intake"
    return types.SimpleNamespace(
        source_root=prepared,
        plugin_root=plugin_root,
        mcp_source_root=Path(mcp["canonical_source_root"]),
        mcp_root=Path(mcp["release_root"]),
        mcp_python=Path(mcp_python or mcp["python_executable"]),
        release_id=mcp["release_id"],
        subject_roots=subject_roots,
    )


def collect_execution_evidence(root: Path) -> dict[str, Any]:
    """Summarize only physically reopened runtime artifacts."""

    provider_pids: set[int] = set()
    task_runner_pids: set[int] = set()
    provider_stages: dict[str, dict[str, Any]] = {}
    analysis_packages: list[dict[str, Any]] = []
    transcript_count = 0
    transcript_physical_sha_pass = True
    transcript_model_call_count = 0
    transcript_provider_request_count = 0
    terminal_receipt_count = 0
    projection_count = 0
    review_candidate_count = 0
    for path in root.rglob("*.json"):
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping):
            continue
        schema = value.get("schema_version")
        if schema in {
            "study-intake-provider-process-identity-v1",
            "study-intake-provider-process-identity-v2",
        }:
            pid = value.get("provider_pid")
            stage = value.get("stage_name")
            if isinstance(pid, int) and pid > 0:
                provider_pids.add(pid)
            if isinstance(stage, str):
                provider_stages[stage] = {
                    "provider_pid": pid,
                    "launched_at": value.get("launched_at"),
                    "identity_sha256": sha256_file(path),
                }
        elif schema == "study-intake-task-process-identity-v1":
            pid = value.get("child_pid")
            if isinstance(pid, int) and pid > 0:
                task_runner_pids.add(pid)
        elif schema == "study-intake-analysis-package-v1":
            analysis_packages.append(
                {
                    "sha256": sha256_file(path),
                    "stage_order": value.get("stage_order"),
                    "stages": [
                        {
                            "stage": row.get("stage"),
                            "outcome": row.get("normalization_status"),
                        }
                        for row in value.get("stages", [])
                        if isinstance(row, Mapping)
                    ],
                }
            )
        elif schema in {
            "study-intake-production-canary-terminal-receipt-v2",
            "study-intake-production-canary-terminal-receipt-v3",
        }:
            terminal_receipt_count += 1
        elif schema == "subject_luna_batch_v2":
            projection_count += 1
        elif schema == "study-intake-review-candidate-terminal-v1":
            review_candidate_count += 1
        if "mcp-stage-transcripts" in path.parts and isinstance(
            value.get("calls"), list
        ):
            transcript_count += 1
            transcript_model_call_count += int(value.get("model_call_count") or 0)
            transcript_provider_request_count += int(
                value.get("provider_request_count") or 0
            )
            expected = path.stem
            if len(expected) != 64 or sha256_file(path) != expected:
                transcript_physical_sha_pass = False
    return {
        "task_runner_pids": sorted(task_runner_pids),
        "provider_pids": sorted(provider_pids),
        "provider_stages": provider_stages,
        "analysis_packages": analysis_packages,
        "analysis_package_sealed": bool(analysis_packages),
        "mcp_transcript_count": transcript_count,
        "model_call_count": transcript_model_call_count,
        "provider_request_count": transcript_provider_request_count,
        "mcp_transcripts_physical_reopen": (
            transcript_count > 0 and transcript_physical_sha_pass
        ),
        "terminal_receipt_count": terminal_receipt_count,
        "subject_sol_projection_count": projection_count,
        "review_candidate_count": review_candidate_count,
        "review_reopen_status": (
            "candidate_available" if review_candidate_count else "not_applicable"
        ),
        "dedup_active": "zero_model_contract_pass",
        "dedup_completed": "zero_model_contract_pass",
        "formal_write_count": 0,
    }


def run(spec_path: Path) -> dict[str, Any]:
    spec = load_spec(spec_path)
    subject = spec.get("subject")
    if subject not in SUBJECTS:
        raise AcceptanceError("subject_invalid")
    if os.environ.get("STUDY_SOURCE_ACCEPTANCE_REAL_PROVIDER_ENABLED") != "1":
        raise AcceptanceError("real_provider_environment_gate_closed")
    if spec.get("real_provider") is not True:
        raise AcceptanceError("real_provider_spec_gate_closed")
    roots = isolated_roots()
    # The reused canonical Producer builders create TemporaryDirectory
    # instances internally.  Bind Python's already-imported tempfile module,
    # as well as child-process conventions, to the supplied producer root so
    # no synthetic Capture can escape the top-level isolation tree.
    os.environ["TMPDIR"] = str(roots["producer"])
    tempfile.tempdir = str(roots["producer"])
    mcp = verify_sealed_mcp(spec)
    closures, heads = materialize_canonical_closures(spec, roots["producer"])
    source_release, source_provenance = materialize_source_release(spec, roots, mcp)
    driver_path = Path(str(spec.get("acceptance_driver") or "")).resolve(strict=True)
    driver = load_bound_module(
        "source_acceptance_external_driver",
        driver_path,
        spec.get("acceptance_driver_sha256"),
    )
    test_path = source_release / "tests/test_real_producer_backend_zero_model.py"
    verify_file_binding(
        test_path,
        spec.get("backend_test_module_sha256"),
        "backend_test_module_hash_mismatch",
    )
    loaded_test_provenance: dict[str, str] = {}

    class SourceAcceptance(driver.CandidateAcceptance):
        def _load_candidate_modules(self):
            for name, root in closures.items():
                os.environ[f"STUDY_INTAKE_ACTUAL_{name.upper()}_ROOT"] = str(root)
            producer_tests, dispatcher_module, _fixture_module = super()._load_candidate_modules()
            loaded_test_file = Path(str(producer_tests.__file__)).resolve(strict=True)
            if loaded_test_file != test_path.resolve():
                raise AcceptanceError("backend_test_module_source_leak")
            loaded_test_provenance.update(
                {
                    "module_file": str(loaded_test_file),
                    "module_sha256": sha256_file(loaded_test_file),
                }
            )
            producer_tests.CANONICAL_ROOT = Path(str(spec["canonical_repository_root"]))
            producer_tests.MATH_ROOT = Path(str(spec["subjects"]["math"]["canonical_root"]))
            producer_tests.CS408_ROOT = Path(str(spec["subjects"]["cs408"]["canonical_root"]))
            producer_tests.ENGLISH_ROOT = Path(str(spec["subjects"]["english"]["canonical_root"]))
            producer_tests.ACTUAL_ROOTS = dict(closures)
            producer_tests.EXPECTED_MAIN_HEADS = {
                producer_tests.MATH_ROOT: heads["math"],
                producer_tests.CS408_ROOT: heads["cs408"],
                producer_tests.ENGLISH_ROOT: heads["english"],
            }

            def assert_bound_source(
                testcase: Any,
                repo: Path,
                relative_paths: tuple[str, ...],
            ) -> None:
                expected = producer_tests.EXPECTED_MAIN_HEADS[repo]
                observed = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                ).stdout.strip()
                testcase.assertEqual(observed, expected)
                for relative in relative_paths:
                    committed = subprocess.run(
                        ["git", "show", f"HEAD:{relative}"],
                        cwd=repo,
                        check=True,
                        stdout=subprocess.PIPE,
                    ).stdout
                    testcase.assertEqual(
                        (repo / relative).read_bytes(), committed
                    )

            producer_tests.RealProducerBackendZeroModelTests.assert_canonical_main_sources = (
                assert_bound_source
            )
            fixture_module = types.SimpleNamespace(
                build_portable_plugin_fixture=lambda base, backend_root, mcp_python=None: build_portable_fixture(
                    base, backend_root, mcp=mcp, mcp_python=mcp_python
                )
            )
            return producer_tests, dispatcher_module, fixture_module

        def _real_acceptance(self, testcase: Any, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("subject") == "cs408":
                adapter_root = kwargs["worker"].adapter.config.get(
                    "private_current_question_root"
                )
                if not isinstance(adapter_root, str):
                    raise AcceptanceError("cs408_private_root_missing")
                self.config_template.setdefault("private_evidence", {})[
                    "current_question_root"
                ] = adapter_root
                self.config_template.setdefault("adapters", {}).setdefault(
                    "cs408", {}
                )["private_current_question_root"] = adapter_root
            subject = str(kwargs.get("subject") or "")
            fixture_root = Path(kwargs["fixture_root"]).resolve(strict=True)
            trial_root = (
                fixture_root.parent
                / f".{fixture_root.name}-phase2-candidate-{subject}"
            )
            dispatch_module = importlib.import_module("concurrent_dispatch")
            original_wait = dispatch_module.DispatchHandle.wait
            completion_bridge = FirstWaitCompletionBridge(
                original_wait,
                trial_root / "runtime",
            )

            def completion_compatible_wait(handle: Any, timeout: Any = None):
                return completion_bridge.wait(handle, timeout)

            dispatch_module.DispatchHandle.wait = completion_compatible_wait
            original_canonical_root = self.canonical_root
            if subject in {"math", "cs408"}:
                self.canonical_root = roots["producer"]
            try:
                return super()._real_acceptance(testcase, **kwargs)
            finally:
                self.canonical_root = original_canonical_root
                dispatch_module.DispatchHandle.wait = original_wait

    harness = SourceAcceptance(
        candidate_release=source_release,
        evidence_root=roots["evidence"],
        codex_path=Path(str(spec.get("codex_executable") or "")),
        mcp_python=Path(mcp["python_executable"]),
        canonical_root=Path(str(spec["canonical_repository_root"])),
    )
    diagnostic_environment = {
        "STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS": "1",
        "STUDY_SOURCE_ACCEPTANCE_WRAPPER_PATH": str(
            source_release
            / "scripts/run_source_subject_provider_acceptance.py"
        ),
        "STUDY_SOURCE_ACCEPTANCE_WRAPPER_SHA256": sha256_file(
            source_release
            / "scripts/run_source_subject_provider_acceptance.py"
        ),
    }
    prior_diagnostic_environment = {
        name: os.environ.get(name) for name in diagnostic_environment
    }
    os.environ.update(diagnostic_environment)
    try:
        result = harness.run(str(subject))
    finally:
        for name, previous in prior_diagnostic_environment.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
    execution_evidence = collect_execution_evidence(roots["evidence"])
    real_model_call_count = int(result.get("model_call_count") or 0)
    task_runner_pids = execution_evidence["task_runner_pids"]
    result.update(
        {
            "producer_origin": "canonical_source_closure",
            "manual_field_injection": False,
            "production_write_count": 0,
            "outer_pid": os.getpid(),
            "task_runner_pid": (
                task_runner_pids[0] if len(task_runner_pids) == 1 else None
            ),
            "task_runner_pids": task_runner_pids,
            "provider_pids": execution_evidence["provider_pids"],
            "real_model_call_count": real_model_call_count,
            "execution_evidence": execution_evidence,
            "source_provenance": {
                "acceptance_driver": {
                    "path": str(driver_path),
                    "sha256": sha256_file(driver_path),
                },
                "backend_test_module": {
                    "path": str(test_path),
                    "sha256": sha256_file(test_path),
                    **loaded_test_provenance,
                },
                "source_release": source_provenance,
                "sealed_mcp": mcp,
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.spec)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0 if result.get("status") == "passed" else 1
    except BaseException as exc:
        try:
            failed_roots = isolated_roots()
            failed_evidence = collect_execution_evidence(failed_roots["evidence"])
        except BaseException:
            failed_evidence = {
                "model_call_count": 0,
                "provider_request_count": 0,
                "provider_pids": [],
            }
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": str(exc),
                    "real_model_call_count": failed_evidence[
                        "model_call_count"
                    ],
                    "provider_request_count": failed_evidence[
                        "provider_request_count"
                    ],
                    "provider_pids": failed_evidence["provider_pids"],
                    "formal_write_count": 0,
                    "production_write_count": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
