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
import datetime as dt
import hashlib
import importlib
import json
import os
import signal
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
    git = git_identity(
        source_root,
        expected_root=expected_source_root,
        expected_branch=expected_branch,
        expected_head=expected_head,
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
        if producer_mode == "actual_skill" and executor.get("adapter") != "command":
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


def verify_source_manifest(
    manifest: Mapping[str, Any], *, verify_git: bool = True
) -> None:
    core = manifest.get("core")
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
    protected_paths = [Path(value) for value in config.get("protected_paths", [])]
    forbidden = [source_root, *protected_paths]
    manifest = build_source_manifest(
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
    config_path = root / "sealed-config.json"
    atomic_json(config_path, config)
    manifest_path = root / "source-manifest.json"
    atomic_json(manifest_path, manifest)
    verify_source_manifest(manifest)
    before = protected_snapshot(protected_paths)
    atomic_json(root / "protected-before.json", before)

    release_path = root / "barrier-release.json"
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

    after = protected_snapshot(protected_paths)
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
        atomic_json(evidence_root / "lifecycle.json", terminal)
        atomic_json(
            evidence_root / "barrier-ready.json",
            {
                "subject": subject,
                "round_id": round_id,
                "ready_at": terminal["barrier_ready_at"],
                "outer_pid": os.getpid(),
                "source_manifest_sha256": manifest["source_manifest_sha256"],
                "formal_write_count": 0,
            },
        )
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
        if executor.get("adapter") == "fixture":
            result = _run_fixture_executor(executor, subject)
        else:
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
