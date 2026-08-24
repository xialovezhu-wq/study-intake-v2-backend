#!/usr/bin/env python3
"""Source-bound, three-subject acceptance supervisor.

The default executor is a deterministic zero-model fixture.  A real Provider
can only be reached through an explicitly enabled command whose source file is
part of the sealed source manifest.  The supervisor never imports test code.
Protected regular files are read only as a non-semantic, streaming SHA-256
integrity operation; no protected body is parsed, decoded, recorded, displayed,
copied, or exposed to a model, Provider, or MCP process.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


SUBJECTS = ("math", "cs408", "english")
PRODUCER_MODES = ("canonical", "actual_skill")
CONFIG_SCHEMA = "study-intake-source-acceptance-config-v1"
MANIFEST_SCHEMA = "study-intake-source-acceptance-manifest-v1"
SUMMARY_SCHEMA = "study-intake-source-acceptance-summary-v1"
CHILD_SCHEMA = "study-intake-source-subject-terminal-v1"
_FORBIDDEN_SOURCE_PARTS = {"current", "releases", "site-packages"}
_SUSPENDED_BACKEND_NAMES = {"study-intake-v2-backend"}
_CANDIDATE_8_ID = "c5fa4bad38d1e60c95c857ec20be026dbb87df295ca409ffc16c23811fbced8d"
MINIMUM_PIPELINE_AUTHORITY_KEY = b"minimum-pipeline-authority-key-0"


class SourceAcceptanceError(RuntimeError):
    """A fail-closed source or safety error with a stable error code."""


class SourceCommandFailure(SourceAcceptanceError):
    def __init__(self, code: str, *, result: Mapping[str, Any], pid: int) -> None:
        super().__init__(code)
        self.result = dict(result)
        self.pid = pid


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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise SourceAcceptanceError("source_file_invalid")
    return sha256_bytes(candidate.read_bytes())


def sha256_file_stream(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    candidate = path.expanduser()
    try:
        expected = candidate.lstat()
    except OSError as exc:
        raise SourceAcceptanceError("protected_file_invalid") from exc
    if (
        stat.S_ISLNK(expected.st_mode)
        or not stat.S_ISREG(expected.st_mode)
        or chunk_size < 1
    ):
        raise SourceAcceptanceError("protected_file_invalid")
    digest = hashlib.sha256()
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            before = os.fstat(handle.fileno())
            if (
                before.st_dev != expected.st_dev
                or before.st_ino != expected.st_ino
                or not stat.S_ISREG(before.st_mode)
            ):
                raise SourceAcceptanceError("protected_file_invalid")
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise SourceAcceptanceError("protected_file_unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise SourceAcceptanceError("protected_snapshot_unstable")
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_json(path: Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise SourceAcceptanceError("json_input_invalid")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceAcceptanceError("json_input_invalid") from exc


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise SourceAcceptanceError("source_root_not_git_worktree")
    return completed.stdout.strip()


def _forbidden_source_path(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    rendered = str(path).lower()
    return bool(lowered & _FORBIDDEN_SOURCE_PARTS) or _CANDIDATE_8_ID in rendered


def git_identity(
    root: Path,
    *,
    expected_root: Path | None = None,
    expected_branch: str | None = None,
    expected_head: str | None = None,
    reject_suspended_backend: bool = True,
    require_clean: bool = True,
) -> dict[str, Any]:
    candidate = root.expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise SourceAcceptanceError("source_root_invalid")
    resolved = candidate.resolve(strict=True)
    if _forbidden_source_path(resolved):
        raise SourceAcceptanceError("forbidden_release_source_root")
    if reject_suspended_backend and resolved.name in _SUSPENDED_BACKEND_NAMES:
        raise SourceAcceptanceError("suspended_backend_source_root")
    if expected_root is not None and resolved != expected_root.expanduser().resolve():
        raise SourceAcceptanceError("backend_source_root_mismatch")
    top = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if top != resolved:
        raise SourceAcceptanceError("source_root_not_git_toplevel")
    branch = _git(resolved, "branch", "--show-current")
    head = _git(resolved, "rev-parse", "HEAD")
    tree = _git(resolved, "rev-parse", "HEAD^{tree}")
    status = _git(resolved, "status", "--porcelain=v1", "--untracked-files=all")
    if status and require_clean:
        raise SourceAcceptanceError("backend_source_root_dirty")
    if expected_branch is not None and branch != expected_branch:
        raise SourceAcceptanceError("backend_source_branch_mismatch")
    if expected_head is not None and head != expected_head:
        raise SourceAcceptanceError("backend_source_head_mismatch")
    return {
        "root": str(resolved),
        "branch": branch,
        "head": head,
        "tree": tree,
        "clean": not bool(status),
        "status_sha256": sha256_bytes(status.encode("utf-8")),
    }


def containing_git_identity(path: Path) -> dict[str, Any]:
    candidate = path.expanduser().resolve(strict=True)
    top = Path(_git(candidate, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not is_within(candidate, top):
        raise SourceAcceptanceError("subject_source_git_root_mismatch")
    return git_identity(
        top,
        reject_suspended_backend=False,
        require_clean=False,
    )


def _tracked_files(root: Path) -> list[dict[str, str]]:
    raw = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    rows: list[dict[str, str]] = []
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        relative = encoded.decode("utf-8")
        path = (root / relative).resolve(strict=True)
        if path.is_symlink() or not path.is_file() or not is_within(path, root):
            raise SourceAcceptanceError("tracked_source_file_invalid")
        rows.append({"path": relative, "sha256": sha256_file(path)})
    return sorted(rows, key=lambda row: row["path"])


def _file_identity(path: Path, *, allowed_roots: Sequence[Path]) -> dict[str, str]:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise SourceAcceptanceError("declared_source_file_invalid")
    resolved = candidate.resolve(strict=True)
    if _forbidden_source_path(resolved):
        raise SourceAcceptanceError("declared_source_file_forbidden")
    if not any(is_within(resolved, root) for root in allowed_roots):
        raise SourceAcceptanceError("declared_source_file_outside_source_roots")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def _validate_subject_specs(
    subjects: Mapping[str, Any], source_root: Path
) -> dict[str, dict[str, Any]]:
    if set(subjects) != set(SUBJECTS):
        raise SourceAcceptanceError("subject_matrix_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    for subject in SUBJECTS:
        raw = subjects.get(subject)
        if not isinstance(raw, Mapping):
            raise SourceAcceptanceError("subject_spec_invalid")
        subject_root_raw = raw.get("subject_root", str(source_root))
        subject_root = Path(str(subject_root_raw)).expanduser()
        if subject_root.is_symlink() or not subject_root.is_dir():
            raise SourceAcceptanceError("subject_source_root_invalid")
        subject_root = subject_root.resolve(strict=True)
        if _forbidden_source_path(subject_root):
            raise SourceAcceptanceError("subject_source_root_forbidden")
        producer_source_root = Path(
            str(raw.get("producer_source_root", subject_root))
        ).expanduser()
        if producer_source_root.is_symlink() or not producer_source_root.is_dir():
            raise SourceAcceptanceError("producer_source_root_invalid")
        producer_source_root = producer_source_root.resolve(strict=True)
        if _forbidden_source_path(producer_source_root):
            raise SourceAcceptanceError("producer_source_root_forbidden")
        skill_root_raw = raw.get("skill_root")
        skill_root: Path | None = None
        if skill_root_raw is not None:
            skill_root = Path(str(skill_root_raw)).expanduser()
            if skill_root.is_symlink() or not skill_root.is_dir():
                raise SourceAcceptanceError("skill_source_root_invalid")
            skill_root = skill_root.resolve(strict=True)
            if _forbidden_source_path(skill_root):
                raise SourceAcceptanceError("skill_source_root_forbidden")
        contract_root_raw = raw.get("contract_root")
        contract_root: Path | None = None
        if contract_root_raw is not None:
            contract_root = Path(str(contract_root_raw)).expanduser()
            if contract_root.is_symlink() or not contract_root.is_dir():
                raise SourceAcceptanceError("contract_source_root_invalid")
            contract_root = contract_root.resolve(strict=True)
            if _forbidden_source_path(contract_root):
                raise SourceAcceptanceError("contract_source_root_forbidden")
        executor = raw.get("executor")
        if not isinstance(executor, Mapping) or executor.get("adapter") not in {
            "fixture",
            "command",
            "backend",
        }:
            raise SourceAcceptanceError("subject_executor_invalid")
        row = dict(raw)
        row["subject_root"] = str(subject_root)
        row["producer_source_root"] = str(producer_source_root)
        if skill_root is not None:
            row["skill_root"] = str(skill_root)
        if contract_root is not None:
            row["contract_root"] = str(contract_root)
        normalized[subject] = row
    return normalized


def harness_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    paths = (
        Path(__file__).resolve(),
        root / "scripts/run_source_real_producer_acceptance.py",
        root / "scripts/run_source_subject.py",
        root / "scripts/run_source_subject_provider_acceptance.py",
        root / "schemas/source-acceptance-manifest-v1.json",
    )
    files = [
        {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in paths
    ]
    return {"files": files, "sha256": sha256_bytes(canonical_bytes(files))}


def build_source_manifest(
    *,
    source_root: Path,
    producer_mode: str,
    config: Mapping[str, Any],
    expected_source_root: Path | None = None,
    expected_branch: str | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    if producer_mode not in PRODUCER_MODES:
        raise SourceAcceptanceError("producer_mode_invalid")
    source_root = source_root.expanduser().resolve(strict=True)
    raw_subjects = config.get("subjects", {})
    direct_backend = bool(
        isinstance(raw_subjects, Mapping)
        and set(raw_subjects) == set(SUBJECTS)
        and all(
            isinstance(raw_subjects.get(subject), Mapping)
            and isinstance(raw_subjects[subject].get("executor"), Mapping)
            and raw_subjects[subject]["executor"].get("adapter") == "backend"
            for subject in SUBJECTS
        )
    )
    git = git_identity(
        source_root,
        expected_root=expected_source_root,
        expected_branch=expected_branch,
        expected_head=expected_head,
        require_clean=not direct_backend,
    )
    subjects = _validate_subject_specs(config.get("subjects", {}), source_root)
    shared_raw = config.get("shared_mcp")
    if not isinstance(shared_raw, Mapping):
        raise SourceAcceptanceError("shared_mcp_binding_missing")
    shared_root_raw = shared_raw.get("source_root")
    shared_module_raw = shared_raw.get("module_file")
    if not isinstance(shared_root_raw, str) or not isinstance(shared_module_raw, str):
        raise SourceAcceptanceError("shared_mcp_binding_invalid")
    shared_root = Path(shared_root_raw).expanduser().resolve(strict=True)
    shared_git = git_identity(
        shared_root,
        expected_root=Path(str(shared_raw["expected_source_root"]))
        if shared_raw.get("expected_source_root") is not None
        else shared_root,
        expected_branch=str(shared_raw["expected_branch"])
        if shared_raw.get("expected_branch") is not None
        else None,
        expected_head=str(shared_raw["expected_head"])
        if shared_raw.get("expected_head") is not None
        else None,
        reject_suspended_backend=False,
    )
    shared_module = _file_identity(
        Path(shared_module_raw), allowed_roots=(shared_root,)
    )
    declared: dict[str, Any] = {}
    for subject in SUBJECTS:
        spec = subjects[subject]
        subject_root = Path(spec["subject_root"])
        producer_source_root = Path(spec["producer_source_root"])
        allowed_values = [source_root, subject_root, producer_source_root]
        if spec.get("skill_root") is not None:
            allowed_values.append(Path(spec["skill_root"]))
        if spec.get("contract_root") is not None:
            allowed_values.append(Path(spec["contract_root"]))
        allowed = tuple(allowed_values)
        files: dict[str, Any] = {}
        for key in ("producer_module_file", "skill_file", "writer_file"):
            raw = spec.get(key)
            if not isinstance(raw, str):
                raise SourceAcceptanceError(f"{subject}_{key}_missing")
            files[key] = _file_identity(Path(raw), allowed_roots=allowed)
        contract_files = spec.get("contract_files", {})
        if not isinstance(contract_files, Mapping) or any(
            not isinstance(name, str)
            or not name
            or any(not (char.isalnum() or char in "_-") for char in name)
            or not isinstance(path, str)
            for name, path in contract_files.items()
        ):
            raise SourceAcceptanceError("subject_contract_files_invalid")
        for name, path in sorted(contract_files.items()):
            files[f"contract_{name}"] = _file_identity(
                Path(path), allowed_roots=allowed
            )
        executor = spec["executor"]
        if producer_mode == "actual_skill" and executor.get("adapter") not in {
            "command",
            "backend",
        }:
            raise SourceAcceptanceError("actual_skill_requires_declared_command")
        if executor.get("adapter") == "command":
            command_source = executor.get("command_source")
            argv = executor.get("argv")
            if not isinstance(command_source, str) or not isinstance(argv, list) or not argv:
                raise SourceAcceptanceError("command_executor_binding_invalid")
            files["command_source"] = _file_identity(
                Path(command_source), allowed_roots=allowed
            )
            if producer_mode == "actual_skill" and not executor.get(
                "actual_skill_declared_command"
            ):
                raise SourceAcceptanceError("actual_skill_command_not_declared")
        elif executor.get("adapter") == "backend":
            runtime_config_path = Path(
                str(executor.get("runtime_config_path") or "")
            ).expanduser()
            if (
                runtime_config_path.is_symlink()
                or not runtime_config_path.is_file()
            ):
                raise SourceAcceptanceError("backend_runtime_config_invalid")
            resolved_runtime_config = runtime_config_path.resolve(strict=True)
            files["runtime_config"] = {
                "path": str(resolved_runtime_config),
                "sha256": sha256_file(resolved_runtime_config),
            }
            writer_harness = (
                source_root / "tests/test_real_producer_backend_zero_model.py"
            )
            files["backend_writer_harness"] = _file_identity(
                writer_harness, allowed_roots=(source_root,)
            )
        declared[subject] = {
            "subject_root": str(subject_root),
            "producer_source_root": str(producer_source_root),
            "git": containing_git_identity(subject_root),
            "files": files,
        }
    backend_module = config.get("backend_module", "preprocessor_core")
    if not isinstance(backend_module, str) or not backend_module:
        raise SourceAcceptanceError("backend_module_invalid")
    core = {
        "schema_version": MANIFEST_SCHEMA,
        "producer_mode": producer_mode,
        "backend": {
            **git,
            "module": backend_module,
            "tracked_files": _tracked_files(source_root),
        },
        "shared_mcp": {
            **shared_git,
            "module_file": shared_module,
            "tracked_files": _tracked_files(shared_root),
        },
        "subjects": declared,
        "harness": harness_identity(),
        "config_sha256": sha256_bytes(canonical_bytes(config)),
        "formal_write_count": 0,
    }
    return {"core": core, "source_manifest_sha256": sha256_bytes(canonical_bytes(core))}


def _minimum_harness_identity(source_root: Path) -> dict[str, Any]:
    paths = (
        Path(__file__).resolve(),
        source_root / "scripts/run_source_real_producer_acceptance.py",
        source_root / "scripts/run_source_subject.py",
        source_root / "bin/preprocess_dispatcher.py",
        source_root / "bin/preprocess_task_runner.py",
        source_root / "lib/analysis_package_v1.py",
        source_root / "lib/concurrent_dispatch.py",
        source_root / "lib/core_dispatch_bridge.py",
        source_root / "tests/test_real_producer_backend_zero_model.py",
    )
    files = [
        {"path": str(path.resolve(strict=True)), "sha256": sha256_file(path)}
        for path in paths
    ]
    return {"files": files, "sha256": sha256_bytes(canonical_bytes(files))}


def build_minimum_source_record(
    *,
    source_root: Path,
    producer_mode: str,
    config: Mapping[str, Any],
    expected_source_root: Path | None = None,
    expected_branch: str | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve(strict=True)
    specs = _validate_subject_specs(config.get("subjects", {}), source_root)
    if producer_mode != "actual_skill" or any(
        specs[subject]["executor"].get("adapter") != "backend"
        for subject in SUBJECTS
    ):
        raise SourceAcceptanceError("minimum_pipeline_backend_executor_required")
    backend = git_identity(
        source_root,
        expected_root=expected_source_root,
        expected_branch=expected_branch,
        expected_head=expected_head,
        require_clean=False,
    )
    shared = config.get("shared_mcp")
    if not isinstance(shared, Mapping):
        raise SourceAcceptanceError("shared_mcp_binding_missing")
    shared_root = Path(str(shared.get("source_root") or "")).resolve(strict=True)
    shared_git = git_identity(
        shared_root,
        expected_root=Path(str(shared.get("expected_source_root") or shared_root)),
        expected_branch=(
            str(shared["expected_branch"])
            if shared.get("expected_branch") is not None
            else None
        ),
        expected_head=(
            str(shared["expected_head"])
            if shared.get("expected_head") is not None
            else None
        ),
        reject_suspended_backend=False,
    )
    shared_module = _file_identity(
        Path(str(shared.get("module_file") or "")),
        allowed_roots=(shared_root,),
    )
    subject_rows: dict[str, Any] = {}
    for subject in SUBJECTS:
        spec = specs[subject]
        allowed_roots = (
            source_root,
            Path(spec["subject_root"]),
            Path(spec["producer_source_root"]),
            *(
                (Path(spec["skill_root"]),)
                if spec.get("skill_root") is not None
                else ()
            ),
            *(
                (Path(spec["contract_root"]),)
                if spec.get("contract_root") is not None
                else ()
            ),
        )
        files: dict[str, Any] = {}
        for key in ("producer_module_file", "skill_file", "writer_file"):
            files[key] = _file_identity(
                Path(str(spec.get(key) or "")), allowed_roots=allowed_roots
            )
        contracts = spec.get("contract_files", {})
        if not isinstance(contracts, Mapping):
            raise SourceAcceptanceError("subject_contract_files_invalid")
        for name, raw_path in sorted(contracts.items()):
            files[f"contract_{name}"] = _file_identity(
                Path(str(raw_path)), allowed_roots=allowed_roots
            )
        runtime_config = Path(
            str(spec["executor"].get("runtime_config_path") or "")
        ).expanduser()
        if runtime_config.is_symlink() or not runtime_config.is_file():
            raise SourceAcceptanceError("backend_runtime_config_invalid")
        resolved_runtime_config = runtime_config.resolve(strict=True)
        files["runtime_config"] = {
            "path": str(resolved_runtime_config),
            "sha256": sha256_file(resolved_runtime_config),
        }
        subject_rows[subject] = {
            "subject_root": spec["subject_root"],
            "producer_source_root": spec["producer_source_root"],
            "git": containing_git_identity(Path(spec["subject_root"])),
            "files": files,
        }
    core = {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_kind": "minimum_config_and_source",
        "producer_mode": producer_mode,
        "backend": {**backend, "module": str(config.get("backend_module", "preprocessor_core"))},
        "shared_mcp": {**shared_git, "module_file": shared_module},
        "subjects": subject_rows,
        "harness": _minimum_harness_identity(source_root),
        "config_sha256": sha256_bytes(canonical_bytes(config)),
        "formal_write_count": 0,
    }
    return {
        "core": core,
        "source_manifest_sha256": sha256_bytes(canonical_bytes(core)),
    }


def _verify_minimum_source_record(
    manifest: Mapping[str, Any], *, verify_git: bool
) -> None:
    core = manifest.get("core")
    digest = manifest.get("source_manifest_sha256")
    if (
        not isinstance(core, Mapping)
        or core.get("schema_version") != MANIFEST_SCHEMA
        or core.get("manifest_kind") != "minimum_config_and_source"
        or core.get("formal_write_count") != 0
        or digest != sha256_bytes(canonical_bytes(core))
    ):
        raise SourceAcceptanceError("minimum_source_record_invalid")
    harness = core.get("harness")
    if not isinstance(harness, Mapping) or not isinstance(harness.get("files"), list):
        raise SourceAcceptanceError("minimum_source_record_invalid")
    observed_harness = [
        {"path": str(Path(row["path"]).resolve()), "sha256": sha256_file(Path(row["path"]))}
        for row in harness["files"]
    ]
    if (
        observed_harness != harness["files"]
        or sha256_bytes(canonical_bytes(observed_harness)) != harness.get("sha256")
    ):
        raise SourceAcceptanceError("minimum_source_record_harness_drift")
    for section in ("shared_mcp",):
        row = core.get(section)
        if not isinstance(row, Mapping):
            raise SourceAcceptanceError("minimum_source_record_invalid")
        module = row.get("module_file")
        if (
            not isinstance(module, Mapping)
            or sha256_file(Path(str(module.get("path")))) != module.get("sha256")
        ):
            raise SourceAcceptanceError("minimum_source_record_file_drift")
    subjects = core.get("subjects")
    if not isinstance(subjects, Mapping) or set(subjects) != set(SUBJECTS):
        raise SourceAcceptanceError("minimum_source_record_invalid")
    for subject in SUBJECTS:
        files = subjects[subject].get("files")
        if not isinstance(files, Mapping):
            raise SourceAcceptanceError("minimum_source_record_invalid")
        for row in files.values():
            if (
                not isinstance(row, Mapping)
                or sha256_file(Path(str(row.get("path")))) != row.get("sha256")
            ):
                raise SourceAcceptanceError("minimum_source_record_file_drift")
    if verify_git:
        for row in [core.get("backend"), core.get("shared_mcp"), *[
            subjects[subject].get("git") for subject in SUBJECTS
        ]]:
            if not isinstance(row, Mapping):
                raise SourceAcceptanceError("minimum_source_record_invalid")
            observed = git_identity(
                Path(str(row.get("root"))),
                expected_root=Path(str(row.get("root"))),
                expected_branch=str(row.get("branch")),
                expected_head=str(row.get("head")),
                reject_suspended_backend=False,
                require_clean=False,
            )
            if observed.get("tree") != row.get("tree"):
                raise SourceAcceptanceError("minimum_source_record_git_drift")


def verify_source_manifest(
    manifest: Mapping[str, Any], *, verify_git: bool = True
) -> None:
    core = manifest.get("core")
    if isinstance(core, Mapping) and core.get("manifest_kind") == "minimum_config_and_source":
        _verify_minimum_source_record(manifest, verify_git=verify_git)
        return
    digest = manifest.get("source_manifest_sha256")
    if (
        not isinstance(core, Mapping)
        or core.get("schema_version") != MANIFEST_SCHEMA
        or core.get("formal_write_count") != 0
        or not isinstance(digest, str)
        or digest != sha256_bytes(canonical_bytes(core))
    ):
        raise SourceAcceptanceError("source_manifest_invalid")
    backend = core.get("backend")
    subjects = core.get("subjects")
    shared_mcp = core.get("shared_mcp")
    harness = core.get("harness")
    if (
        not isinstance(backend, Mapping)
        or not isinstance(shared_mcp, Mapping)
        or not isinstance(subjects, Mapping)
    ):
        raise SourceAcceptanceError("source_manifest_invalid")
    git_cache: dict[str, dict[str, Any]] = {}
    tracked_cache: dict[str, list[dict[str, str]]] = {}

    def observe_git(row: Mapping[str, Any]) -> dict[str, Any]:
        observed_root = Path(str(row.get("root"))).resolve(strict=True)
        key = str(observed_root)
        if key not in git_cache:
            git_cache[key] = git_identity(
                observed_root,
                expected_root=observed_root,
                expected_branch=str(row.get("branch")),
                expected_head=str(row.get("head")),
                reject_suspended_backend=False,
                require_clean=False,
            )
        return git_cache[key]

    def observe_tracked(root: Path) -> list[dict[str, str]]:
        key = str(root)
        if key not in tracked_cache:
            tracked_cache[key] = _tracked_files(root)
        return tracked_cache[key]

    def verify_stored_files(
        root: Path, rows: Any, error_code: str
    ) -> None:
        if not isinstance(rows, list):
            raise SourceAcceptanceError(error_code)
        for row in rows:
            if (
                not isinstance(row, Mapping)
                or not isinstance(row.get("path"), str)
                or not isinstance(row.get("sha256"), str)
            ):
                raise SourceAcceptanceError(error_code)
            relative = Path(str(row["path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise SourceAcceptanceError(error_code)
            path = (root / relative).resolve(strict=True)
            if not is_within(path, root) or sha256_file(path) != row["sha256"]:
                raise SourceAcceptanceError(error_code)

    source_root = Path(str(backend.get("root"))).resolve(strict=True)
    if verify_git:
        observed_backend = observe_git(backend)
        if (
            observed_backend.get("tree") != backend.get("tree")
            or observed_backend.get("clean") != backend.get("clean")
            or observed_backend.get("status_sha256")
            != backend.get("status_sha256")
        ):
            raise SourceAcceptanceError("source_manifest_git_tree_drift")
        if observe_tracked(source_root) != backend.get("tracked_files"):
            raise SourceAcceptanceError("source_manifest_backend_file_drift")
    else:
        verify_stored_files(
            source_root,
            backend.get("tracked_files"),
            "source_manifest_backend_file_drift",
        )
    shared_root = Path(str(shared_mcp.get("root"))).resolve(strict=True)
    if verify_git:
        observed_shared = observe_git(shared_mcp)
        if (
            observed_shared.get("tree") != shared_mcp.get("tree")
            or observed_shared.get("clean") != shared_mcp.get("clean")
            or observed_shared.get("status_sha256")
            != shared_mcp.get("status_sha256")
        ):
            raise SourceAcceptanceError("source_manifest_shared_mcp_git_drift")
        if observe_tracked(shared_root) != shared_mcp.get("tracked_files"):
            raise SourceAcceptanceError("source_manifest_shared_mcp_file_drift")
    else:
        verify_stored_files(
            shared_root,
            shared_mcp.get("tracked_files"),
            "source_manifest_shared_mcp_file_drift",
        )
    shared_module = shared_mcp.get("module_file")
    if (
        not isinstance(shared_module, Mapping)
        or sha256_file(Path(str(shared_module.get("path"))))
        != shared_module.get("sha256")
    ):
        raise SourceAcceptanceError("source_manifest_shared_mcp_module_drift")
    if not isinstance(harness, Mapping):
        raise SourceAcceptanceError("source_manifest_harness_invalid")
    harness_files = harness.get("files")
    if not isinstance(harness_files, list):
        raise SourceAcceptanceError("source_manifest_harness_invalid")
    observed_harness: list[dict[str, str]] = []
    for row in harness_files:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise SourceAcceptanceError("source_manifest_harness_invalid")
        path = Path(row["path"])
        observed_harness.append(
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
        )
    if observed_harness != harness_files or sha256_bytes(
        canonical_bytes(observed_harness)
    ) != harness.get("sha256"):
        raise SourceAcceptanceError("source_manifest_harness_drift")
    if set(subjects) != set(SUBJECTS):
        raise SourceAcceptanceError("source_manifest_subjects_invalid")
    for subject in SUBJECTS:
        row = subjects[subject]
        if not isinstance(row, Mapping) or not isinstance(row.get("files"), Mapping):
            raise SourceAcceptanceError("source_manifest_subjects_invalid")
        git = row.get("git")
        if not isinstance(git, Mapping):
            raise SourceAcceptanceError("source_manifest_subject_git_invalid")
        if verify_git:
            observed_git = observe_git(git)
            if (
                observed_git.get("tree") != git.get("tree")
                or observed_git.get("clean") != git.get("clean")
                or observed_git.get("status_sha256") != git.get("status_sha256")
            ):
                raise SourceAcceptanceError("source_manifest_subject_git_drift")
        for identity in row["files"].values():
            if (
                not isinstance(identity, Mapping)
                or not isinstance(identity.get("path"), str)
                or sha256_file(Path(identity["path"])) != identity.get("sha256")
            ):
                raise SourceAcceptanceError("source_manifest_subject_file_drift")


def path_signature(path: Path) -> dict[str, Any]:
    """Return a content-bound recursive signature without exposing file bodies."""

    candidate = path.expanduser().absolute()
    if not candidate.exists() and not candidate.is_symlink():
        return {"path": str(candidate), "exists": False, "entries": []}
    entries: list[dict[str, Any]] = []
    pending = [candidate]
    while pending:
        current = pending.pop()
        info = current.lstat()
        relative = "." if current == candidate else str(current.relative_to(candidate))
        kind = (
            "symlink"
            if stat.S_ISLNK(info.st_mode)
            else "directory"
            if stat.S_ISDIR(info.st_mode)
            else "file"
            if stat.S_ISREG(info.st_mode)
            else "other"
        )
        row: dict[str, Any] = {
            "relative_path": relative,
            "kind": kind,
            "mode": stat.S_IMODE(info.st_mode),
            "size": info.st_size,
        }
        if kind == "symlink":
            row["target"] = os.readlink(current)
        elif kind == "file":
            row["sha256"] = sha256_file_stream(current)
        entries.append(row)
        if kind == "directory":
            pending.extend(sorted(current.iterdir(), reverse=True))
    entries.sort(key=lambda row: row["relative_path"])
    return {"path": str(candidate), "exists": True, "entries": entries}


def protected_snapshot(paths: Sequence[Path]) -> dict[str, Any]:
    rows = [path_signature(path) for path in sorted(paths, key=lambda value: str(value))]
    return {"paths": rows, "signature_sha256": sha256_bytes(canonical_bytes(rows))}


def assert_recursive_formal_write_zero(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key == "formal_write_count" and nested != 0:
                raise SourceAcceptanceError("formal_write_count_nonzero")
            assert_recursive_formal_write_zero(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            assert_recursive_formal_write_zero(nested)


def _safe_new_output_root(path: Path, *, forbidden: Sequence[Path]) -> Path:
    candidate = path.expanduser().absolute()
    if candidate.exists() or candidate.is_symlink():
        raise SourceAcceptanceError("output_root_must_be_new")
    resolved_parent = candidate.parent.resolve(strict=True)
    resolved = resolved_parent / candidate.name
    if resolved in {Path("/"), Path.home()}:
        raise SourceAcceptanceError("output_root_invalid")
    for protected in forbidden:
        protected_resolved = protected.expanduser().absolute().resolve(strict=False)
        if is_within(resolved, protected_resolved) or is_within(protected_resolved, resolved):
            raise SourceAcceptanceError("output_root_overlaps_protected_path")
    resolved.mkdir(mode=0o700)
    return resolved


def _validate_config(config: Mapping[str, Any], producer_mode: str) -> None:
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise SourceAcceptanceError("source_acceptance_config_invalid")
    configured_mode = config.get("producer_mode")
    if configured_mode is not None and configured_mode != producer_mode:
        raise SourceAcceptanceError("producer_mode_config_mismatch")
    protected = config.get("protected_paths", [])
    if not isinstance(protected, list) or not all(isinstance(value, str) for value in protected):
        raise SourceAcceptanceError("protected_paths_invalid")


def _wait_for_barrier(
    processes: Mapping[str, subprocess.Popen[bytes]],
    child_roots: Mapping[str, Path],
    timeout_seconds: float,
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    observed: dict[str, str] = {}
    while len(observed) != len(SUBJECTS):
        for subject in SUBJECTS:
            if subject in observed:
                continue
            root = child_roots[subject]
            if (root / "barrier-ready.json").is_file():
                observed[subject] = "ready"
            elif (root / "terminal.json").is_file() or processes[subject].poll() is not None:
                observed[subject] = "preflight_terminal"
        if len(observed) == len(SUBJECTS):
            break
        if time.monotonic() >= deadline:
            for subject in SUBJECTS:
                observed.setdefault(subject, "barrier_observation_timeout")
            break
        time.sleep(0.01)
    return observed


def _child_command(
    *,
    source_root: Path,
    manifest_path: Path,
    config_path: Path,
    subject: str,
    isolation_root: Path,
    evidence_root: Path,
    runtime_root: Path,
    producer_root: Path,
    scratch_root: Path,
    release_path: Path,
    enable_real_provider: bool,
) -> list[str]:
    runner = Path(__file__).resolve().parents[1] / "scripts/run_source_subject.py"
    command = [
        sys.executable,
        "-B",
        str(runner),
        "--source-root",
        str(source_root),
        "--manifest",
        str(manifest_path),
        "--config",
        str(config_path),
        "--subject",
        subject,
        "--isolation-root",
        str(isolation_root),
        "--evidence-root",
        str(evidence_root),
        "--runtime-root",
        str(runtime_root),
        "--producer-root",
        str(producer_root),
        "--scratch-root",
        str(scratch_root),
        "--release-file",
        str(release_path),
    ]
    if enable_real_provider:
        command.append("--enable-real-provider")
    return command


def run_acceptance(
    *,
    source_root: Path,
    output_root: Path,
    producer_mode: str,
    config: Mapping[str, Any],
    expected_source_root: Path | None = None,
    expected_branch: str | None = None,
    expected_head: str | None = None,
    enable_real_provider: bool = False,
    barrier_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run one three-subject round and return its same-round matrix."""

    _validate_config(config, producer_mode)
    source_root = source_root.expanduser().resolve(strict=True)
    minimum_pipeline = config.get("pipeline_mode") == "minimum"
    protected_paths = [Path(value) for value in config.get("protected_paths", [])]
    forbidden = [source_root, *protected_paths]
    manifest_builder = (
        build_minimum_source_record if minimum_pipeline else build_source_manifest
    )
    manifest = manifest_builder(
        source_root=source_root,
        producer_mode=producer_mode,
        config=config,
        expected_source_root=expected_source_root,
        expected_branch=expected_branch,
        expected_head=expected_head,
    )
    root = _safe_new_output_root(output_root, forbidden=forbidden)
    started_at = utc_now()
    round_id = f"source-round-{uuid.uuid4().hex}"
    working_root = root / ".working" if minimum_pipeline else root
    working_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = working_root / "sealed-config.json"
    atomic_json(config_path, config)
    manifest_path = root / (
        "config-and-source.json" if minimum_pipeline else "source-manifest.json"
    )
    atomic_json(manifest_path, manifest)
    verify_source_manifest(manifest)
    before = (
        {"signature_sha256": "minimum_pipeline_not_collected"}
        if minimum_pipeline
        else protected_snapshot(protected_paths)
    )
    if not minimum_pipeline:
        atomic_json(root / "protected-before.json", before)

    release_path = working_root / "barrier-release.json"
    child_roots: dict[str, Path] = {}
    processes: dict[str, subprocess.Popen[bytes]] = {}
    handles: list[Any] = []
    try:
        for subject in SUBJECTS:
            child_root = root / "subjects" / subject
            evidence = child_root / "evidence"
            runtime = child_root / "runtime"
            producer = child_root / "synthetic-producer"
            scratch = child_root / "scratch"
            for writable in (evidence, runtime, producer, scratch):
                writable.mkdir(parents=True, mode=0o700)
                if not is_within(writable, root):
                    raise SourceAcceptanceError("writable_root_not_isolated")
            stdout_handle = (evidence / "outer.stdout").open("wb")
            stderr_handle = (evidence / "outer.stderr").open("wb")
            handles.extend((stdout_handle, stderr_handle))
            command = _child_command(
                source_root=source_root,
                manifest_path=manifest_path,
                config_path=config_path,
                subject=subject,
                isolation_root=root,
                evidence_root=evidence,
                runtime_root=runtime,
                producer_root=producer,
                scratch_root=scratch,
                release_path=release_path,
                enable_real_provider=enable_real_provider,
            )
            environment = {
                **os.environ,
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": "",
                "STUDY_SOURCE_ACCEPTANCE_ROUND_ID": round_id,
            }
            process = subprocess.Popen(
                command,
                cwd=source_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
            )
            child_roots[subject] = evidence
            processes[subject] = process

        barrier_states = _wait_for_barrier(
            processes, child_roots, barrier_timeout_seconds
        )
        released_at = utc_now()
        with release_path.open("xb") as handle:
            handle.write(
                canonical_bytes(
                    {
                        "schema_version": "study-intake-source-barrier-release-v1",
                        "round_id": round_id,
                        "released_at": released_at,
                        "barrier_states": barrier_states,
                        "formal_write_count": 0,
                    }
                )
            )
        return_codes = {subject: processes[subject].wait() for subject in SUBJECTS}
    finally:
        for handle in handles:
            handle.close()

    terminals: dict[str, Any] = {}
    for subject in SUBJECTS:
        terminal_path = child_roots[subject] / "terminal.json"
        if terminal_path.is_file():
            terminal = load_json(terminal_path)
        else:
            terminal = {
                "schema_version": CHILD_SCHEMA,
                "subject": subject,
                "status": "failed",
                "error_code": "child_terminal_missing",
                "outer_return_code": return_codes[subject],
                "formal_write_count": 0,
            }
        terminals[subject] = terminal

    after = (
        {"signature_sha256": "minimum_pipeline_not_collected"}
        if minimum_pipeline
        else protected_snapshot(protected_paths)
    )
    if not minimum_pipeline:
        atomic_json(root / "protected-after.json", after)
    tripwire_pass = before["signature_sha256"] == after["signature_sha256"]
    all_passed = all(
        return_codes[subject] == 0 and terminals[subject].get("status") == "passed"
        for subject in SUBJECTS
    )
    same_manifest = {
        terminals[subject].get("source_manifest_sha256") for subject in SUBJECTS
    } == {manifest["source_manifest_sha256"]}
    same_harness = {
        terminals[subject].get("harness_sha256") for subject in SUBJECTS
    } == {manifest["core"]["harness"]["sha256"]}
    same_round = all(
        terminal.get("round_id") == round_id for terminal in terminals.values()
    )
    run_starts = [
        terminal.get("run_started_at")
        for terminal in terminals.values()
        if isinstance(terminal.get("run_started_at"), str)
    ]
    terminal_times = [
        terminal.get("terminal_at")
        for terminal in terminals.values()
        if isinstance(terminal.get("terminal_at"), str)
    ]
    intervals_overlapped = (
        len(run_starts) == len(SUBJECTS)
        and len(terminal_times) == len(SUBJECTS)
        and max(run_starts) <= min(terminal_times)
    )
    barrier_observation_complete = all(
        state in {"ready", "preflight_terminal"}
        for state in barrier_states.values()
    )

    def round_counter(key: str) -> int | None:
        values = [
            terminals[subject].get("result", {}).get(key, 0)
            for subject in SUBJECTS
        ]
        if any(value is None for value in values):
            return None
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            return None
        return sum(values)
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA,
        "round_id": round_id,
        "producer_mode": producer_mode,
        "status": "passed"
        if all_passed
        and tripwire_pass
        and same_manifest
        and same_harness
        and same_round
        and intervals_overlapped
        and barrier_observation_complete
        else "failed",
        "started_at": started_at,
        "terminal_at": utc_now(),
        "supervisor_pid": os.getpid(),
        "python_executable": sys.executable,
        "sys_path": list(sys.path),
        "source_manifest_path": str(manifest_path),
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "config_sha256": manifest["core"]["config_sha256"],
        "harness_sha256": manifest["core"]["harness"]["sha256"],
        "barrier": {
            "states": barrier_states,
            "released_at": released_at,
            "released_once": True,
        },
        "barrier_observation_complete": barrier_observation_complete,
        "matrix": terminals,
        "outer_return_codes": return_codes,
        "same_round_all_three": same_round,
        "same_source_manifest_all_three": same_manifest,
        "same_harness_all_three": same_harness,
        "intervals_overlapped": intervals_overlapped,
        "sibling_cancelled": False,
        "protected_tripwire_pass": tripwire_pass,
        "protected_before_sha256": before["signature_sha256"],
        "protected_after_sha256": after["signature_sha256"],
        "real_provider_enabled": enable_real_provider,
        "real_model_call_count": round_counter("real_model_call_count"),
        "provider_request_count": round_counter("provider_request_count"),
        "production_write_count": 0 if tripwire_pass else 1,
        "formal_write_count": 0,
    }
    try:
        assert_recursive_formal_write_zero(summary)
    except SourceAcceptanceError as exc:
        summary["status"] = "failed"
        summary["error_code"] = str(exc)
    if minimum_pipeline:
        task_rows = [
            task
            for subject in SUBJECTS
            for task in terminals[subject].get("result", {}).get("tasks", [])
            if isinstance(task, Mapping)
        ]
        task_subject_counts = {
            subject: sum(row.get("subject") == subject for row in task_rows)
            for subject in SUBJECTS
        }

        def interval_overlap(rows: Sequence[Mapping[str, Any]]) -> bool:
            if len(rows) < 2:
                return False
            try:
                starts = [
                    dt.datetime.fromisoformat(
                        str(row["started_at"]).replace("Z", "+00:00")
                    )
                    for row in rows
                ]
                finishes = [
                    dt.datetime.fromisoformat(
                        str(row["finished_at"]).replace("Z", "+00:00")
                    )
                    for row in rows
                ]
            except (KeyError, TypeError, ValueError):
                return False
            return max(starts) < min(finishes)

        technical_completed = sum(
            row.get("technical_status") == "completed" for row in task_rows
        )
        intra_overlap = {
            subject: interval_overlap(
                [row for row in task_rows if row.get("subject") == subject]
            )
            for subject in SUBJECTS
        }
        subject_windows = []
        for subject in SUBJECTS:
            rows = [row for row in task_rows if row.get("subject") == subject]
            if rows:
                subject_windows.append(
                    {
                        "started_at": min(str(row["started_at"]) for row in rows),
                        "finished_at": max(str(row["finished_at"]) for row in rows),
                    }
                )
        cross_subject_overlap = interval_overlap(subject_windows)
        real_round = any(
            config["subjects"][subject]["executor"].get("real_provider") is True
            for subject in SUBJECTS
        )
        minimum_pass = bool(
            summary["status"] == "passed"
            and len(task_rows) == 6
            and technical_completed == 6
            and task_subject_counts == {subject: 2 for subject in SUBJECTS}
            and len({row.get("unit_sha256") for row in task_rows}) == 6
            and len({row.get("capture_id") for row in task_rows}) == 6
            and (
                not real_round
                or cross_subject_overlap
                and all(intra_overlap.values())
            )
        )
        summary.update(
            {
                "status": "passed" if minimum_pass else "failed",
                "task_count": len(task_rows),
                "task_count_by_subject": task_subject_counts,
                "technical_completed_tasks": technical_completed,
                "warning_reports": sum(
                    row.get("report_review_status") == "warning"
                    for row in task_rows
                ),
                "needs_review_reports": sum(
                    row.get("report_review_status") == "needs_review"
                    for row in task_rows
                ),
                "cross_subject_overlap": cross_subject_overlap,
                "intra_subject_overlap": intra_overlap,
                "subject_failures": {
                    subject: {
                        "status": terminals[subject].get("status"),
                        "error_code": terminals[subject].get("error_code"),
                        "exception_type": terminals[subject].get(
                            "exception_type"
                        ),
                    }
                    for subject in SUBJECTS
                    if terminals[subject].get("status") != "passed"
                },
            }
        )
        target_task_root = root / "tasks"
        target_task_root.mkdir(mode=0o700)
        for subject in SUBJECTS:
            source_task_root = child_roots[subject] / "tasks"
            moved = False
            if source_task_root.is_dir():
                for task_root in source_task_root.iterdir():
                    target = target_task_root / task_root.name
                    if target.exists():
                        raise SourceAcceptanceError("minimum_task_evidence_conflict")
                    os.replace(task_root, target)
                    moved = True
            if not moved and terminals[subject].get("status") != "passed":
                failure_root = target_task_root / f"preflight-{subject}"
                failure_root.mkdir(mode=0o700)
                atomic_json(
                    failure_root / "terminal.json",
                    {
                        "task_id": f"preflight-{subject}",
                        "subject": subject,
                        "technical_status": "technical_failed",
                        "error_code": terminals[subject].get("error_code"),
                        "formal_write_count": 0,
                    },
                )
                resolved_config_path = (
                    child_roots[subject].parent / "scratch/backend-config.json"
                )
                atomic_json(
                    failure_root / "report.json",
                    {
                        "final_report_readable": False,
                        "terminal": terminals[subject],
                        "resolved_config": (
                            load_json(resolved_config_path)
                            if resolved_config_path.is_file()
                            else None
                        ),
                        "formal_write_count": 0,
                    },
                )
                for stream_name in ("stdout", "stderr"):
                    outer = child_roots[subject] / f"outer.{stream_name}"
                    (failure_root / f"{stream_name}.log").write_text(
                        outer.read_text(encoding="utf-8", errors="replace")
                        if outer.is_file()
                        else "",
                        encoding="utf-8",
                    )
        timeline = {
            "round_id": round_id,
            "barrier_released_at": released_at,
            "tasks": sorted(
                (
                    {
                        "task_id": row.get("unit_sha256"),
                        "subject": row.get("subject"),
                        "capture_id": row.get("capture_id"),
                        "started_at": row.get("started_at"),
                        "finished_at": row.get("finished_at"),
                    }
                    for row in task_rows
                ),
                key=lambda row: str(row.get("started_at")),
            ),
            "formal_write_count": 0,
        }
        atomic_json(root / "timeline.json", timeline)
        summary.pop("matrix", None)
        summary.pop("outer_return_codes", None)
        summary["source_manifest_path"] = str(manifest_path)
        shutil.rmtree(root / "subjects")
        shutil.rmtree(working_root)
        atomic_json(root / "run-summary.json", summary)
    else:
        atomic_json(root / "summary.json", summary)
    return summary


def _subject_file_row(manifest: Mapping[str, Any], subject: str, key: str) -> Mapping[str, Any]:
    try:
        row = manifest["core"]["subjects"][subject]["files"][key]
    except (KeyError, TypeError) as exc:
        raise SourceAcceptanceError("subject_manifest_binding_missing") from exc
    if not isinstance(row, Mapping):
        raise SourceAcceptanceError("subject_manifest_binding_missing")
    return row


def _import_backend_module(source_root: Path, module_name: str) -> Any:
    lib = source_root / "lib"
    for value in (str(lib), str(source_root)):
        if value not in sys.path:
            sys.path.insert(0, value)
    module = importlib.import_module(module_name)
    raw = getattr(module, "__file__", None)
    if not isinstance(raw, str):
        raise SourceAcceptanceError("backend_module_file_missing")
    path = Path(raw).resolve(strict=True)
    if not is_within(path, source_root) or _forbidden_source_path(path):
        raise SourceAcceptanceError("backend_module_source_leak")
    return module


def _validate_child_roots(
    isolation_root: Path, roots: Sequence[Path]
) -> list[Path]:
    resolved: list[Path] = []
    for root in roots:
        candidate = root.resolve(strict=True)
        if not candidate.is_dir() or not is_within(candidate, isolation_root):
            raise SourceAcceptanceError("child_writable_root_not_isolated")
        resolved.append(candidate)
    if len(set(resolved)) != len(resolved):
        raise SourceAcceptanceError("child_writable_roots_not_distinct")
    return resolved


def _run_fixture_executor(executor: Mapping[str, Any], subject: str) -> dict[str, Any]:
    delay = executor.get("delay_seconds", 0)
    if not isinstance(delay, (int, float)) or delay < 0 or delay > 30:
        raise SourceAcceptanceError("fixture_delay_invalid")
    if delay:
        time.sleep(float(delay))
    if executor.get("fail"):
        raise SourceAcceptanceError(str(executor.get("error_code", "fixture_failure")))
    result = executor.get("result", {})
    if not isinstance(result, Mapping):
        raise SourceAcceptanceError("fixture_result_invalid")
    return {
        **dict(result),
        "subject": subject,
        "producer_origin": "zero_model_fixture_adapter",
        "real_model_call_count": 0,
        "provider_request_count": 0,
        "formal_write_count": 0,
    }


def _failure_counters_from_evidence(evidence_root: Path) -> dict[str, Any]:
    provider_pids: set[int] = set()
    model_call_count = 0
    provider_request_count = 0
    transcript_count = 0
    for path in evidence_root.rglob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping):
            continue
        if value.get("schema_version") in {
            "study-intake-provider-process-identity-v1",
            "study-intake-provider-process-identity-v2",
        }:
            pid = value.get("provider_pid")
            if isinstance(pid, int) and pid > 0:
                provider_pids.add(pid)
        if (
            "mcp-stage-transcripts" in path.parts
            and isinstance(value.get("calls"), list)
        ):
            transcript_count += 1
            model_call_count += int(value.get("model_call_count") or 0)
            provider_request_count += int(
                value.get("provider_request_count") or 0
            )
    observed = transcript_count > 0
    return {
        "status": "failed",
        "error_code": "subject_command_timeout",
        "real_model_call_count": model_call_count if observed else None,
        "provider_request_count": provider_request_count if observed else None,
        "provider_pids": sorted(provider_pids),
        "counter_observation_status": "observed" if observed else "unknown",
        "formal_write_count": 0,
    }


def _run_command_executor(
    executor: Mapping[str, Any],
    *,
    source_root: Path,
    subject_root: Path,
    writable_roots: Sequence[Path],
    enable_real_provider: bool,
) -> tuple[dict[str, Any], int]:
    argv = executor.get("argv")
    command_source = executor.get("command_source")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise SourceAcceptanceError("command_executor_argv_invalid")
    if not isinstance(command_source, str):
        raise SourceAcceptanceError("command_executor_source_invalid")
    source_path = Path(command_source).resolve(strict=True)
    if not (is_within(source_path, source_root) or is_within(source_path, subject_root)):
        raise SourceAcceptanceError("command_executor_source_unbound")
    resolved_argv_paths = {
        Path(value).expanduser().resolve(strict=False)
        for value in argv
        if os.sep in value or value.startswith(".")
    }
    if source_path not in resolved_argv_paths:
        raise SourceAcceptanceError("command_executor_does_not_invoke_declared_source")
    real_provider = bool(executor.get("real_provider"))
    if real_provider and not enable_real_provider:
        raise SourceAcceptanceError("real_provider_not_explicitly_enabled")
    environment = {
        **os.environ,
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT": str(writable_roots[0]),
        "STUDY_SOURCE_ACCEPTANCE_PRODUCER_ROOT": str(writable_roots[1]),
        "STUDY_SOURCE_ACCEPTANCE_SCRATCH_ROOT": str(writable_roots[2]),
        "STUDY_SOURCE_ACCEPTANCE_EVIDENCE_ROOT": str(writable_roots[3]),
        "STUDY_SOURCE_ACCEPTANCE_REAL_PROVIDER_ENABLED": "1" if real_provider else "0",
    }
    timeout = executor.get("timeout_seconds", 60)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise SourceAcceptanceError("command_executor_timeout_invalid")
    process = subprocess.Popen(
        argv,
        cwd=subject_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=float(timeout))
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired as cleanup_exc:
            raise SourceAcceptanceError(
                "subject_command_process_group_not_terminated"
            ) from cleanup_exc
        raise SourceCommandFailure(
            "subject_command_timeout",
            result=_failure_counters_from_evidence(writable_roots[3]),
            pid=process.pid,
        ) from exc
    completed_returncode = process.returncode
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SourceAcceptanceError("subject_command_output_invalid") from exc
    if not isinstance(result, Mapping):
        raise SourceAcceptanceError("subject_command_output_invalid")
    assert_recursive_formal_write_zero(result)
    if (
        result.get("formal_write_count") != 0
        or not isinstance(result.get("real_model_call_count"), int)
        or int(result["real_model_call_count"]) < 0
        or not isinstance(result.get("provider_request_count"), int)
        or int(result["provider_request_count"]) < 0
    ):
        raise SourceAcceptanceError("subject_command_counter_contract_invalid")
    provider_pids = result.get("provider_pids", [])
    if not isinstance(provider_pids, list) or not all(
        isinstance(value, int) and value > 0 for value in provider_pids
    ):
        raise SourceAcceptanceError("subject_command_provider_pids_invalid")
    if not real_provider and (
        result.get("real_model_call_count", 0) != 0
        or result.get("provider_request_count", 0) != 0
    ):
        raise SourceAcceptanceError("zero_model_command_invoked_provider")
    if completed_returncode != 0:
        raise SourceCommandFailure(
            f"subject_command_failed:{completed_returncode}",
            result=result,
            pid=process.pid,
        )
    return dict(result), process.pid


_BACKEND_TEST_METHODS = {
    "math": "test_math_actual_quick_intake_to_backend_runner_boundary",
    "cs408": "test_cs408_actual_managed_and_ordinary_producers_to_backend",
    "english": "test_english_actual_immutable_closed_and_quick_flush_to_backend",
}


class _BackendFixtureComplete(RuntimeError):
    """Internal control transfer after a real writer fixture has been consumed."""


def _backend_runtime_config(
    template: Mapping[str, Any],
    *,
    subject: str,
    runtime_root: Path,
    fixture_root: Path,
    subject_roots: Mapping[str, Path],
    mcp_subject_roots: Mapping[str, Path] | None = None,
    worker: Any,
    authority_key_path: Path,
) -> dict[str, Any]:
    config = copy.deepcopy(dict(template))
    config["execution_mode"] = "live_authorized"
    config["runtime_root"] = str(runtime_root)
    config["timezone"] = "Asia/Shanghai"
    dispatch_config = config.setdefault("dispatch", {})
    if isinstance(dispatch_config, dict):
        dispatch_config.pop("production_canary", None)
    worker_config = config.setdefault("worker", {})
    worker_config["lock_path"] = str(runtime_root / "state/worker.lock")
    worker_config["log_path"] = str(runtime_root / "logs/worker.log")
    worker_config["model_timeout_seconds"] = max(
        900, int(worker_config.get("model_timeout_seconds") or 0)
    )
    config.setdefault("dashboard", {})["projection_path"] = str(
        runtime_root / "state/dashboard_projection.json"
    )
    config.setdefault("live_execution_gate", {})[
        "authorization_state_path"
    ] = str(runtime_root / "dispatch/manual-live-authorization-v1/state.json")
    config.setdefault("private_evidence", {})[
        "current_question_root"
    ] = str(runtime_root / "private/current-question-evidence")

    source_relatives = {
        "math": {
            "status_script": "数学一回滚复习系统/scripts/quick_intake.py",
        },
        "cs408": {
            "status_script": "scripts/intake_fact_capture_408.py",
        },
        "english": {
            "status_script": "scripts/english_learning_pipeline.py",
            "foundation_script": "scripts/select_bbdc_foundation.py",
            "review_status_script": "scripts/build_review_status_proposals.py",
            "candidate_schema": "schema/english_pipeline/luna-candidate-v2.schema.json",
        },
    }
    adapters: dict[str, dict[str, Any]] = {}
    template_adapters = config.get("adapters")
    if not isinstance(template_adapters, Mapping):
        raise SourceAcceptanceError("backend_runtime_adapters_missing")
    for name in SUBJECTS:
        root = fixture_root if name == subject else subject_roots[name]
        if name == subject:
            row = copy.deepcopy(dict(worker.adapter.config))
            row["enabled"] = True
            row["repo_root"] = str(root)
        else:
            raw = template_adapters.get(name)
            if not isinstance(raw, Mapping):
                raise SourceAcceptanceError("backend_runtime_adapter_missing")
            row = copy.deepcopy(dict(raw))
            row["enabled"] = False
            row["repo_root"] = str(root)
            for key, relative in source_relatives[name].items():
                row[key] = str(root / relative)
            if name == "english":
                row["state_dir"] = str(root / "intake")
                row["candidate_root"] = str(root / "intake/candidates")
        adapters[name] = row
    config["adapters"] = adapters
    if subject == "cs408":
        cs408_private_root = adapters["cs408"].get(
            "private_current_question_root"
        )
        if isinstance(cs408_private_root, str) and cs408_private_root.strip():
            config["private_evidence"]["current_question_root"] = (
                cs408_private_root
            )
    effective_mcp_roots = mcp_subject_roots or {
        name: fixture_root if name == subject else subject_roots[name]
        for name in SUBJECTS
    }
    config["subject_repo_roots"] = {
        name: str(effective_mcp_roots[name]) for name in SUBJECTS
    }
    processing_plugin = config.get("processing_plugin")
    if not isinstance(processing_plugin, Mapping):
        raise SourceAcceptanceError("backend_processing_plugin_missing")
    config["processing_plugin"] = copy.deepcopy(dict(processing_plugin))
    config["processing_plugin"]["authority_key_path"] = str(authority_key_path)
    if subject != "math" and isinstance(config.get("math_deep_v2"), Mapping):
        config["math_deep_v2"]["enabled"] = False
    if subject != "cs408" and isinstance(config.get("cs408_deep_v2"), Mapping):
        config["cs408_deep_v2"]["enabled"] = False
    return config


def _backend_report_review_status(authority: Mapping[str, Any]) -> str:
    disposition = authority.get("report_disposition")
    if disposition == "needs_sol_review":
        return "needs_review"
    if authority.get("quality_status") == "issues_found":
        return "warning"
    return "clean"


def _prepare_isolated_preprocessor_authority(
    *,
    runtime_root: Path,
    release_manifest_path: Path,
    release_id: str,
) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise SourceAcceptanceError("backend_release_identity_invalid")
    if release_manifest_path.is_symlink() or not release_manifest_path.is_file():
        raise SourceAcceptanceError("backend_release_manifest_invalid")
    raw = release_manifest_path.read_bytes()
    try:
        manifest = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceAcceptanceError("backend_release_manifest_invalid") from exc
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("release_id") != release_id
        or not isinstance(manifest.get("component_inventory"), Mapping)
    ):
        raise SourceAcceptanceError("backend_release_manifest_invalid")
    releases_root = runtime_root / "releases"
    objects_root = runtime_root / "packages/objects"
    release_root = releases_root / release_id
    release_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    objects_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_manifest = release_root / "release.json"
    if target_manifest.exists():
        if target_manifest.is_symlink() or target_manifest.read_bytes() != raw:
            raise SourceAcceptanceError("backend_release_manifest_conflict")
    else:
        target_manifest.write_bytes(raw)
    current = runtime_root / "current"
    expected_target = Path("releases") / release_id
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or Path(os.readlink(current)) != expected_target:
            raise SourceAcceptanceError("backend_runtime_current_conflict")
    else:
        current.symlink_to(expected_target, target_is_directory=True)


def _write_minimum_task_evidence(
    evidence_root: Path,
    *,
    task: Any,
    result: Any,
    completion: Mapping[str, Any],
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    expected_stage_order: Sequence[str],
) -> dict[str, Any]:
    unit = str(result.unit_sha256)
    task_root = evidence_root / "tasks" / unit
    task_root.mkdir(parents=True, mode=0o700)
    stages = authority.get("stages")
    if (
        not isinstance(stages, list)
        or [row.get("stage") for row in stages] != list(expected_stage_order)
    ):
        raise SourceAcceptanceError("backend_stage_order_invalid")
    process_execution = completion.get("process_execution")
    process_interval = (
        process_execution if isinstance(process_execution, Mapping) else {}
    )
    terminal = {
        "task_id": unit,
        "unit_sha256": unit,
        "subject": completion.get("subject"),
        "capture_id": completion.get("capture_id"),
        "technical_status": "completed",
        "report_review_status": _backend_report_review_status(authority),
        "warning_codes": sorted(
            {
                str(warning)
                for stage in stages
                for warning in stage.get("normalization_warnings", [])
                if isinstance(warning, str) and warning
            }
        ),
        "started_at": (
            completion.get("started_at")
            or process_interval.get("launched_at")
        ),
        "finished_at": (
            completion.get("finished_at")
            or process_interval.get("finished_at")
        ),
        "stage_order": [row["stage"] for row in stages],
        "requested_models": [row.get("requested_model") for row in stages],
        "model_call_count": sum(int(row.get("model_call_count") or 0) for row in stages),
        "provider_request_count": sum(
            int(row.get("provider_request_count") or 0) for row in stages
        ),
        "mcp_tool_call_count": sum(
            int(row.get("mcp_tool_call_count") or 0) for row in stages
        ),
        "formal_write_count": 0,
    }
    atomic_json(task_root / "terminal.json", terminal)
    atomic_json(task_root / "report.json", report)
    (task_root / "stdout.log").write_text(
        json.dumps(
            {
                "status": result.status,
                "outcome": result.outcome,
                "capture_id": completion.get("capture_id"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (task_root / "stderr.log").write_text(
        (str(result.error_code) + "\n") if result.error_code else "",
        encoding="utf-8",
    )
    return terminal


def _write_minimum_task_failure(
    evidence_root: Path,
    *,
    runtime_root: Path,
    config_path: Path,
    task: Any,
    result: Any,
) -> dict[str, Any]:
    unit = str(result.unit_sha256)
    task_root = evidence_root / "tasks" / unit
    task_root.mkdir(parents=True, mode=0o700)
    diagnostics: list[dict[str, Any]] = []
    context_parent = runtime_root / "dispatch/contexts" / unit
    if context_parent.is_dir():
        for path in sorted(
            context_parent.glob("fence-*/source-acceptance-diagnostics/*.json")
        ):
            try:
                value = load_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(value, Mapping):
                diagnostics.append(copy.deepcopy(dict(value)))
    completion = (
        copy.deepcopy(dict(result.completion))
        if isinstance(result.completion, Mapping)
        else None
    )
    process_execution = (
        completion.get("process_execution")
        if isinstance(completion, Mapping)
        else None
    )
    process_interval = (
        process_execution if isinstance(process_execution, Mapping) else {}
    )
    terminal = {
        "task_id": unit,
        "unit_sha256": unit,
        "subject": task.frozen_payload.get("subject"),
        "capture_id": task.frozen_payload.get("capture_id"),
        "technical_status": "technical_failed",
        "report_review_status": "partial",
        "warning_codes": [],
        "started_at": (
            completion.get("started_at")
            if completion and completion.get("started_at")
            else process_interval.get("launched_at")
        ),
        "finished_at": (
            completion.get("finished_at")
            if completion and completion.get("finished_at")
            else process_interval.get("finished_at") or utc_now()
        ),
        "stage_order": [],
        "model_call_count": 0,
        "provider_request_count": 0,
        "mcp_tool_call_count": 0,
        "error_code": result.error_code,
        "formal_write_count": 0,
    }
    atomic_json(task_root / "terminal.json", terminal)
    atomic_json(
        task_root / "report.json",
        {
            "final_report_readable": False,
            "task": task.as_dict(),
            "completion": completion,
            "resolved_config_path": str(config_path),
            "resolved_config_sha256": sha256_file(config_path),
            "resolved_config": load_json(config_path),
            "diagnostics": diagnostics,
            "formal_write_count": 0,
        },
    )
    (task_root / "stdout.log").write_text(
        json.dumps(
            {
                "status": result.status,
                "outcome": result.outcome,
                "error_code": result.error_code,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (task_root / "stderr.log").write_text(
        "\n".join(
            str(row.get("traceback") or row.get("stderr_utf8") or "")
            for row in diagnostics
        )
        or str(result.error_code or "unknown_task_failure")
        + "\n",
        encoding="utf-8",
    )
    return terminal


def _run_backend_executor(
    executor: Mapping[str, Any],
    *,
    source_root: Path,
    subject: str,
    spec: Mapping[str, Any],
    subject_specs: Mapping[str, Mapping[str, Any]],
    runtime_root: Path,
    producer_root: Path,
    scratch_root: Path,
    evidence_root: Path,
    release_file: Path,
    barrier_ready: Any,
    enable_real_provider: bool,
) -> dict[str, Any]:
    real_provider = executor.get("real_provider") is True
    if real_provider and not enable_real_provider:
        raise SourceAcceptanceError("real_provider_not_explicitly_enabled")
    runtime_config_path = Path(
        str(executor.get("runtime_config_path") or "")
    ).expanduser()
    if runtime_config_path.is_symlink() or not runtime_config_path.is_file():
        raise SourceAcceptanceError("backend_runtime_config_invalid")
    template = load_json(runtime_config_path)
    if not isinstance(template, Mapping):
        raise SourceAcceptanceError("backend_runtime_config_invalid")

    for value in (source_root, source_root / "lib", source_root / "bin"):
        if str(value) not in sys.path:
            sys.path.insert(0, str(value))
    producer_tests = importlib.import_module(
        "tests.test_real_producer_backend_zero_model"
    )
    module_path = Path(str(producer_tests.__file__)).resolve(strict=True)
    if not is_within(module_path, source_root):
        raise SourceAcceptanceError("backend_writer_harness_source_invalid")
    dispatcher_module = importlib.import_module("preprocess_dispatcher")
    core_module = importlib.import_module("preprocessor_core")

    subject_roots = {
        name: Path(str(row["subject_root"])).resolve(strict=True)
        for name, row in subject_specs.items()
    }
    producer_tests.MATH_ROOT = subject_roots["math"]
    producer_tests.CS408_ROOT = subject_roots["cs408"]
    producer_tests.ENGLISH_ROOT = subject_roots["english"]
    producer_tests.ACTUAL_ROOTS = {
        name: Path(
            str(subject_specs[name].get("actual_writer_root") or subject_roots[name])
        ).resolve(strict=True)
        for name in SUBJECTS
    }
    producer_tests.SUBJECT_REPOS_AVAILABLE = True
    producer_tests.ACTUAL_PRODUCERS_AVAILABLE = True
    runtime_subject_roots = dict(subject_roots)
    if subject != "english":
        runtime_subject_roots["english"] = producer_tests.ACTUAL_ROOTS[
            "english"
        ]

    method_name = _BACKEND_TEST_METHODS[subject]
    testcase = producer_tests.RealProducerBackendZeroModelTests(
        methodName=method_name
    )
    testcase.assert_canonical_main_sources = lambda *_args, **_kwargs: None
    captured_summary: dict[str, Any] | None = None
    prior_tempdir = tempfile.tempdir
    prior_capture_mode = os.environ.get("STUDY_MINIMUM_PIPELINE_TWO_CAPTURES")
    prior_diagnostic_mode = os.environ.get(
        "STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS"
    )
    prior_diagnostic_root = os.environ.get(
        "STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT"
    )
    tempfile.tempdir = str(producer_root)
    os.environ["STUDY_MINIMUM_PIPELINE_TWO_CAPTURES"] = "1"
    os.environ["STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS"] = "minimum"
    os.environ["STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT"] = str(runtime_root)

    def consume_fixture(**kwargs: Any) -> dict[str, Any]:
        nonlocal captured_summary
        fixture_root = Path(kwargs["fixture_root"]).resolve(strict=True)
        worker = kwargs["worker"]
        overlay = kwargs["overlay"]
        raw_ids = kwargs.get("target_capture_ids")
        target_ids = (
            [str(value) for value in raw_ids]
            if isinstance(raw_ids, (list, tuple))
            else [str(kwargs["target_capture_id"])]
        )
        if len(target_ids) != 2 or len(set(target_ids)) != 2:
            raise SourceAcceptanceError("backend_writer_capture_count_invalid")

        authority_key_path = scratch_root / "authority.key"
        authority_key_path.write_bytes(MINIMUM_PIPELINE_AUTHORITY_KEY)
        authority_key_path.chmod(0o600)
        config = _backend_runtime_config(
            template,
            subject=subject,
            runtime_root=runtime_root,
            fixture_root=fixture_root,
            subject_roots=runtime_subject_roots,
            mcp_subject_roots=producer_tests.ACTUAL_ROOTS,
            worker=worker,
            authority_key_path=authority_key_path,
        )
        config_path = scratch_root / "backend-config.json"
        atomic_json(config_path, config)
        loaded_config = (
            core_module.load_config(config_path) if real_provider else config
        )
        release_id, _ = dispatcher_module.release_identity(loaded_config)
        worker.release_id = release_id
        if real_provider:
            _prepare_isolated_preprocessor_authority(
                runtime_root=runtime_root,
                release_manifest_path=Path(
                    str(loaded_config["release"]["manifest_path"])
                ),
                release_id=release_id,
            )
        runtime = dispatcher_module.ProductionDispatchRuntime(
            loaded_config,
            subject,
            config_path,
            scan_worker_factory=lambda _config: worker,
            ordinary_local_capture=False,
        )
        if real_provider:
            original_runner_factory = runtime.dispatcher.runner_factory

            def diagnostic_runner_factory(task: Any, context: Any) -> Any:
                runner = original_runner_factory(task, context)

                def wrap(method: Any, stage_name: str) -> Any:
                    def guarded(*args: Any, **method_kwargs: Any) -> Any:
                        try:
                            return method(*args, **method_kwargs)
                        except BaseException as exc:
                            chain: list[dict[str, Any]] = []
                            current: BaseException | None = exc
                            while current is not None:
                                diagnostic = getattr(current, "diagnostic", None)
                                chain.append(
                                    {
                                        "type": type(current).__name__,
                                        "code": getattr(current, "code", None),
                                        "message": str(current),
                                        "diagnostic": (
                                            copy.deepcopy(dict(diagnostic))
                                            if isinstance(diagnostic, Mapping)
                                            else None
                                        ),
                                    }
                                )
                                current = current.__cause__
                            atomic_json(
                                context.root
                                / "source-acceptance-diagnostics"
                                / f"direct-{stage_name}.json",
                                {
                                    "subject": subject,
                                    "unit_sha256": task.unit_sha256,
                                    "stage": stage_name,
                                    "traceback": traceback.format_exc(),
                                    "error_chain": chain,
                                    "formal_write_count": 0,
                                },
                            )
                            raise

                    return guarded

                runner.run_analysis = wrap(runner.run_analysis, "analysis")
                runner.run_critical_review = wrap(
                    runner.run_critical_review, "critical_review"
                )
                return runner

            runtime.dispatcher.runner_factory = diagnostic_runner_factory
        else:
            runtime.processing_host = None
            runtime.dispatcher.runner_factory = (
                lambda _task, _context: producer_tests.ImmediateZeroModelRunner()
            )

        available = worker.eligible_candidates(
            subject,
            "2026-08-22",
            capture_allowlist=None,
            controlled_replay=False,
        )
        by_id = {row.capture_id: row for row, _reason in available}
        if not set(target_ids).issubset(by_id):
            raise SourceAcceptanceError("backend_writer_capture_missing")
        targets = [by_id[capture_id] for capture_id in target_ids]
        processing_contracts = {
            candidate.input_binding.get("processing_contract_sha256")
            for candidate in targets
        }
        if len(processing_contracts) != 1:
            raise SourceAcceptanceError("backend_processing_contract_mismatch")
        processing_contract_sha256 = next(iter(processing_contracts))
        if not isinstance(processing_contract_sha256, str):
            raise SourceAcceptanceError("backend_processing_contract_missing")
        recorded_ats = [
            dt.datetime.fromisoformat(
                str(candidate.recorded_at).replace("Z", "+00:00")
            )
            for candidate in targets
        ]
        cutoff_at = (min(recorded_ats) - dt.timedelta(seconds=1)).isoformat()
        exact_values: set[str] = set()
        for candidate in targets:
            event_ids = candidate.input_binding.get("capture_event_ids")
            if subject == "english" and isinstance(event_ids, list):
                exact_values.update(str(value) for value in event_ids)
            else:
                exact_values.add(candidate.capture_id)
        exact_allowlist = frozenset(exact_values)

        barrier_ready(
            {
                "capture_ids": target_ids,
                "writer_fixture_root": str(fixture_root),
                "runtime_config_sha256": sha256_file(runtime_config_path),
            }
        )

        scan_config = copy.deepcopy(dict(loaded_config))
        scan_config["processing_plugin"] = {
            "component_lock_path": str(overlay["component_lock_path"])
        }
        frozen, decisions = producer_tests.scan_eligible_candidates(
            scan_config,
            subject,
            worker_factory=lambda _config: worker,
            capture_allowlist=exact_allowlist,
            producer_recorded_after=cutoff_at,
            publish_evidence_readiness=False,
        )
        handles = [runtime.dispatcher.submit(row.task) for row in frozen]
        if len(handles) != 2:
            raise SourceAcceptanceError(
                "backend_task_count_invalid:" + repr(decisions)
            )
        timeout = float(executor.get("timeout_seconds", 2700))
        if not runtime.dispatcher.drain(timeout=timeout):
            raise SourceAcceptanceError("backend_dispatch_drain_timeout")
        results = [handle.wait(1) for handle in handles]
        task_rows: list[dict[str, Any]] = []
        technical_failures: list[str] = []
        for result in results:
            task = next(
                handle.task for handle in handles
                if handle.task.unit_sha256 == result.unit_sha256
            )
            completion = result.completion
            if (
                result.status != "completed"
                or result.outcome != "succeeded"
                or not isinstance(completion, Mapping)
            ):
                technical_failures.append(
                    str(result.error_code or result.outcome or "unknown")
                )
                task_rows.append(
                    _write_minimum_task_failure(
                        evidence_root,
                        runtime_root=runtime_root,
                        config_path=config_path,
                        task=task,
                        result=result,
                    )
                )
                continue
            report_sha256 = str(completion.get("report_json_sha256") or "")
            report_path = (
                Path(str(completion["report_json_path"]))
                if completion.get("report_json_path")
                else runtime_root
                / "dispatch/reports/json/sha256"
                / report_sha256[:2]
                / f"{report_sha256}.json"
            )
            package_path = Path(str(completion.get("package_path") or ""))
            if not report_path.is_file() or not package_path.is_file():
                raise SourceAcceptanceError("backend_final_report_missing")
            report = load_json(report_path)
            package = load_json(package_path)
            authority = package.get("analysis", {}).get(
                "analysis_package_authority"
            )
            expected_stage_order: tuple[str, ...]
            if not isinstance(authority, Mapping) and not real_provider:
                authority = {
                    "stages": [
                        {
                            "stage": stage,
                            "requested_model": None,
                            "model_call_count": 0,
                            "provider_request_count": 0,
                            "mcp_tool_call_count": 0,
                            "normalization_warnings": [],
                        }
                        for stage in ("analysis", "critical_review")
                    ],
                    "quality_status": "passed",
                    "report_disposition": None,
                }
                expected_stage_order = ("analysis", "critical_review")
            else:
                expected_stage_order = (
                    "terra_analysis",
                    "luna_analysis",
                    "terra_final",
                )
            if not isinstance(report, Mapping) or not isinstance(authority, Mapping):
                raise SourceAcceptanceError("backend_final_report_invalid")
            task_rows.append(
                _write_minimum_task_evidence(
                    evidence_root,
                    task=task,
                    result=result,
                    completion=completion,
                    report=report,
                    authority=authority,
                    expected_stage_order=expected_stage_order,
                )
            )
        if {row["capture_id"] for row in task_rows} != set(target_ids):
            raise SourceAcceptanceError("backend_task_identity_mismatch")
        if technical_failures:
            captured_summary = {
                "status": "failed",
                "subject": subject,
                "capture_ids": target_ids,
                "unit_sha256s": [row["unit_sha256"] for row in task_rows],
                "tasks": task_rows,
                "task_count": len(task_rows),
                "technical_completed_tasks": sum(
                    row["technical_status"] == "completed"
                    for row in task_rows
                ),
                "technical_failure_codes": technical_failures,
                "real_model_call_count": sum(
                    int(row["model_call_count"]) for row in task_rows
                ),
                "provider_request_count": sum(
                    int(row["provider_request_count"]) for row in task_rows
                ),
                "mcp_tool_call_count": sum(
                    int(row["mcp_tool_call_count"]) for row in task_rows
                ),
                "formal_write_count": 0,
            }
            raise SourceCommandFailure(
                "backend_task_technical_failed:"
                + technical_failures[0],
                result=captured_summary,
                pid=os.getpid(),
            )
        captured_summary = {
            "status": "passed",
            "subject": subject,
            "capture_ids": target_ids,
            "unit_sha256s": [row["unit_sha256"] for row in task_rows],
            "tasks": task_rows,
            "task_count": 2,
            "technical_completed_tasks": 2,
            "real_model_call_count": sum(
                int(row["model_call_count"]) for row in task_rows
            ),
            "provider_request_count": sum(
                int(row["provider_request_count"]) for row in task_rows
            ),
            "mcp_tool_call_count": sum(
                int(row["mcp_tool_call_count"]) for row in task_rows
            ),
            "formal_write_count": 0,
        }
        raise _BackendFixtureComplete()

    testcase.assert_actual_production_runtime_once = consume_fixture
    try:
        getattr(testcase, method_name)()
    except _BackendFixtureComplete:
        pass
    finally:
        tempfile.tempdir = prior_tempdir
        if prior_capture_mode is None:
            os.environ.pop("STUDY_MINIMUM_PIPELINE_TWO_CAPTURES", None)
        else:
            os.environ["STUDY_MINIMUM_PIPELINE_TWO_CAPTURES"] = prior_capture_mode
        if prior_diagnostic_mode is None:
            os.environ.pop("STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS", None)
        else:
            os.environ["STUDY_SOURCE_ACCEPTANCE_TASK_DIAGNOSTICS"] = (
                prior_diagnostic_mode
            )
        if prior_diagnostic_root is None:
            os.environ.pop("STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT", None)
        else:
            os.environ["STUDY_SOURCE_ACCEPTANCE_RUNTIME_ROOT"] = (
                prior_diagnostic_root
            )
    if captured_summary is None:
        raise SourceAcceptanceError("backend_writer_fixture_not_reached")
    assert_recursive_formal_write_zero(captured_summary)
    return captured_summary


def run_subject(
    *,
    source_root: Path,
    manifest_path: Path,
    config_path: Path,
    subject: str,
    isolation_root: Path,
    evidence_root: Path,
    runtime_root: Path,
    producer_root: Path,
    scratch_root: Path,
    release_file: Path,
    enable_real_provider: bool = False,
) -> int:
    if subject not in SUBJECTS:
        raise SourceAcceptanceError("subject_invalid")
    evidence_root.mkdir(parents=True, exist_ok=True)
    terminal_path = evidence_root / "terminal.json"
    started_at = utc_now()
    round_id = os.environ.get("STUDY_SOURCE_ACCEPTANCE_ROUND_ID")
    terminal: dict[str, Any] = {
        "schema_version": CHILD_SCHEMA,
        "round_id": round_id,
        "subject": subject,
        "outer_pid": os.getpid(),
        "started_at": started_at,
        "status": "preflight",
        "model_run_started": False,
        "formal_write_count": 0,
    }
    atomic_json(evidence_root / "lifecycle.json", terminal)
    try:
        source_root = source_root.resolve(strict=True)
        isolation_root = isolation_root.resolve(strict=True)
        writable = _validate_child_roots(
            isolation_root,
            (runtime_root, producer_root, scratch_root, evidence_root),
        )
        manifest = load_json(manifest_path)
        config = load_json(config_path)
        if not isinstance(manifest, Mapping) or not isinstance(config, Mapping):
            raise SourceAcceptanceError("child_input_invalid")
        verify_source_manifest(manifest, verify_git=False)
        if manifest["core"].get("config_sha256") != sha256_bytes(canonical_bytes(config)):
            raise SourceAcceptanceError("child_config_sha_mismatch")
        if Path(str(manifest["core"]["backend"]["root"])).resolve() != source_root:
            raise SourceAcceptanceError("child_backend_root_mismatch")
        specs = _validate_subject_specs(config.get("subjects", {}), source_root)
        spec = specs[subject]
        preflight_delay = spec.get("preflight_delay_seconds", 0)
        if (
            isinstance(preflight_delay, bool)
            or not isinstance(preflight_delay, (int, float))
            or not 0 <= float(preflight_delay) <= 5
        ):
            raise SourceAcceptanceError("preflight_delay_invalid")
        if preflight_delay:
            time.sleep(float(preflight_delay))
        subject_root = Path(spec["subject_root"])
        if subject == "cs408" and spec.get(
            "private_root_contract"
        ) != "isolated_producer_root":
            raise SourceAcceptanceError("cs408_private_root_mismatch")
        backend_module = _import_backend_module(
            source_root, str(manifest["core"]["backend"]["module"])
        )
        backend_file = Path(str(backend_module.__file__)).resolve(strict=True)
        for key in ("producer_module_file", "skill_file", "writer_file"):
            row = _subject_file_row(manifest, subject, key)
            if sha256_file(Path(str(row["path"]))) != row.get("sha256"):
                raise SourceAcceptanceError("child_subject_source_drift")
        provenance = {
            "python_executable": sys.executable,
            "sys_path": list(sys.path),
            "backend_module": {
                "name": manifest["core"]["backend"]["module"],
                "file": str(backend_file),
                "sha256": sha256_file(backend_file),
            },
            "shared_mcp_module": manifest["core"]["shared_mcp"]["module_file"],
            "subject_producer_module": {
                "file": _subject_file_row(
                    manifest, subject, "producer_module_file"
                )["path"],
                "sha256": _subject_file_row(
                    manifest, subject, "producer_module_file"
                )["sha256"],
            },
            "skill": _subject_file_row(manifest, subject, "skill_file"),
            "writer": _subject_file_row(manifest, subject, "writer_file"),
            "git": {
                "backend": manifest["core"]["backend"],
                "shared_mcp": manifest["core"]["shared_mcp"],
                "subject": manifest["core"]["subjects"][subject]["git"],
            },
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "config_sha256": manifest["core"]["config_sha256"],
            "harness_sha256": manifest["core"]["harness"]["sha256"],
            "outer_pid": os.getpid(),
        }
        def barrier_gate(extra: Mapping[str, Any] | None = None) -> None:
            terminal.update(
                {
                    "status": "barrier_ready",
                    "barrier_ready_at": utc_now(),
                    "source_manifest_sha256": manifest["source_manifest_sha256"],
                    "config_sha256": manifest["core"]["config_sha256"],
                    "harness_sha256": manifest["core"]["harness"]["sha256"],
                    "source_identity": provenance,
                    "source_identity_pass": True,
                }
            )
            ready = {
                "subject": subject,
                "round_id": round_id,
                "ready_at": terminal["barrier_ready_at"],
                "outer_pid": os.getpid(),
                "source_manifest_sha256": manifest["source_manifest_sha256"],
                "formal_write_count": 0,
            }
            if extra is not None:
                ready["prepared"] = copy.deepcopy(dict(extra))
            atomic_json(evidence_root / "lifecycle.json", terminal)
            atomic_json(evidence_root / "barrier-ready.json", ready)
            while not release_file.is_file():
                time.sleep(0.01)
            release = load_json(release_file)
            terminal.update(
                {
                    "status": "running",
                    "barrier_released_at": release.get("released_at"),
                    "run_started_at": utc_now(),
                    "model_run_started": True,
                }
            )
            atomic_json(evidence_root / "lifecycle.json", terminal)

        executor = spec["executor"]
        executor_pid: int | None = None
        if executor.get("adapter") == "backend":
            result = _run_backend_executor(
                executor,
                source_root=source_root,
                subject=subject,
                spec=spec,
                subject_specs=specs,
                runtime_root=writable[0],
                producer_root=writable[1],
                scratch_root=writable[2],
                evidence_root=writable[3],
                release_file=release_file,
                barrier_ready=barrier_gate,
                enable_real_provider=enable_real_provider,
            )
        elif executor.get("adapter") == "fixture":
            barrier_gate()
            result = _run_fixture_executor(executor, subject)
        else:
            barrier_gate()
            result, executor_pid = _run_command_executor(
                executor,
                source_root=source_root,
                subject_root=Path(spec["producer_source_root"]),
                writable_roots=writable,
                enable_real_provider=enable_real_provider,
            )
        verify_source_manifest(manifest, verify_git=False)
        assert_recursive_formal_write_zero(result)
        if manifest["core"].get("producer_mode") == "actual_skill":
            result.setdefault("producer_origin", "actual_skill_declared_command")
            result["actual_skill_sources_hash_sealed"] = True
        terminal.update(
            {
                "status": "passed",
                "terminal_at": utc_now(),
                "executor_pid": executor_pid,
                "task_runner_pid": executor_pid,
                "provider_pids": result.get("provider_pids", []),
                "result": result,
            }
        )
        assert_recursive_formal_write_zero(terminal)
        atomic_json(terminal_path, terminal)
        return 0
    except SourceCommandFailure as exc:
        terminal.update(
            {
                "status": "failed",
                "terminal_at": utc_now(),
                "error_code": str(exc),
                "exception_type": type(exc).__name__,
                "executor_pid": exc.pid,
                "task_runner_pid": exc.pid,
                "provider_pids": exc.result.get("provider_pids", []),
                "result": exc.result,
            }
        )
        assert_recursive_formal_write_zero(terminal)
        atomic_json(terminal_path, terminal)
        atomic_json(evidence_root / "lifecycle.json", terminal)
        return 1
    except BaseException as exc:
        terminal.update(
            {
                "status": "failed",
                "terminal_at": utc_now(),
                "error_code": str(exc),
                "exception_type": type(exc).__name__,
            }
        )
        atomic_json(terminal_path, terminal)
        atomic_json(evidence_root / "lifecycle.json", terminal)
        traceback.print_exc()
        return 1


def subject_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one source acceptance subject")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--subject", choices=SUBJECTS, required=True)
    parser.add_argument("--isolation-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--producer-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--enable-real-provider", action="store_true")
    return parser


def subject_cli(argv: Sequence[str] | None = None) -> int:
    args = subject_argument_parser().parse_args(argv)
    return run_subject(
        source_root=args.source_root,
        manifest_path=args.manifest,
        config_path=args.config,
        subject=args.subject,
        isolation_root=args.isolation_root,
        evidence_root=args.evidence_root,
        runtime_root=args.runtime_root,
        producer_root=args.producer_root,
        scratch_root=args.scratch_root,
        release_file=args.release_file,
        enable_real_provider=args.enable_real_provider,
    )
