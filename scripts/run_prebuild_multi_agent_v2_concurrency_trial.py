#!/usr/bin/env python3
"""Run the Build-before Multi-Agent V2 nine-Capture concurrency trial.

The public CLI always creates three synthetic Captures per subject through the
real foreground Producer entrypoints, then releases the nine resulting tasks
against one isolated runtime.  The test seam is deliberately below the
Producer boundary: unit tests may replace model/MCP execution, but they cannot
provide already-built background Candidates.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "lib", ROOT / "bin", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from preprocessor_core import Candidate, CodexRunner  # noqa: E402


SUBJECTS = ("math", "cs408", "english")
CANONICAL_SOURCE_ROOTS = {
    "math": ROOT.parent / "kaoyan-math",
    "cs408": ROOT.parent / "kaoyan-408",
    "english": ROOT.parent / "kaoyan-english",
}
PRODUCTION_SUBJECT_ROOTS = {
    "math": Path.home() / "Documents/kaoyan-math",
    "cs408": Path.home() / "Documents/kaoyan-408",
    "english": Path.home() / "Documents/kaoyan-english",
}
EXPECTED_MCP_SERVERS = {
    "math": "kaoyan_math_read",
    "cs408": "kaoyan_cs408_read",
    "english": "kaoyan_english_read",
}
ALLOWED_COLLECTIONS = {
    "math": {
        "formal_card_catalog", "formal_card_records", "knowledge_catalog",
        "math_taxonomy_items", "activity", "search", "relations",
    },
    "cs408": {
        "formal_wrong_item_catalog", "formal_nodes",
        "formal_knowledge_catalog", "knowledge_nodes",
        "knowledge_safe_notes", "curation_inventory", "morning_sessions",
        "review_events", "search", "relations",
    },
    "english": {
        "article_catalog", "articles", "sentences", "vocabulary",
        "mastered_items", "patterns", "events", "raw_events",
        "effective_events", "article_learning_catalog",
        "article_learning_pages", "search", "relations",
    },
}


def _call_collections(call: Mapping[str, Any]) -> set[str]:
    arguments = call.get("arguments")
    values: set[str] = set()
    if isinstance(arguments, Mapping):
        if isinstance(arguments.get("collection"), str):
            values.add(str(arguments["collection"]))
        if isinstance(arguments.get("collections"), list):
            values.update(
                str(value) for value in arguments["collections"]
                if isinstance(value, str)
            )
    if not values and call.get("tool") == "search_records":
        values.add("search")
    if not values and call.get("tool") == "query_relations":
        values.add("relations")
    return values


class TrialError(RuntimeError):
    """A stable fail-closed code for the pre-Build gate."""

    def __init__(self, code: str, diagnostic: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostic = copy.deepcopy(dict(diagnostic or {}))


@dataclasses.dataclass(frozen=True)
class CaptureEnvelope:
    subject: str
    capture_id: str
    candidate: Candidate
    producer_entrypoint: str
    producer_receipt: Mapping[str, Any]


class CaptureProducer(Protocol):
    def __call__(
        self, isolated_roots: Mapping[str, Path], trial_root: Path
    ) -> Sequence[CaptureEnvelope]: ...


class TaskExecutor(Protocol):
    def __call__(
        self, capture: CaptureEnvelope, runtime_root: Path
    ) -> Mapping[str, Any]: ...


class TaskExecutorFactory(Protocol):
    def __call__(
        self, isolated_roots: Mapping[str, Path], trial_root: Path,
        captures: Sequence[CaptureEnvelope],
    ) -> TaskExecutor: ...


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _seconds(value: str) -> float:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TrialError("trial_interval_timestamp_invalid")
    return parsed.timestamp()


def _nonzero_formal_writes(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if key == "formal_write_count" and nested != 0:
                found.append(nested_path)
            found.extend(_nonzero_formal_writes(nested, nested_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(_nonzero_formal_writes(nested, f"{path}[{index}]"))
    return found


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        digest.update(b"missing\0")
        digest.update(str(root).encode("utf-8"))
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(b"\0symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(b"\0file\0")
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _active_output_schema_binding(
    *, config: Mapping[str, Any], source_release: Path,
) -> dict[str, Any]:
    profile = config.get("analysis_package_v2")
    keys = (
        "terra_initial_output_schema",
        "luna_investigation_output_schema",
        "terra_final_output_schema",
    )
    if not isinstance(profile, Mapping):
        raise TrialError("trial_output_schema_binding_missing")
    files: dict[str, dict[str, Any]] = {}
    release_root = source_release.resolve()
    for key in keys:
        path = Path(str(profile.get(key) or "")).resolve()
        try:
            relative = path.relative_to(release_root)
        except ValueError as exc:
            raise TrialError(
                "trial_output_schema_outside_source_release", {"key": key}
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise TrialError("trial_output_schema_binding_missing", {"key": key})
        files[key] = {
            "path": str(relative),
            "sha256": _file_sha(path),
            "size": path.stat().st_size,
        }
    return {
        "files": files,
        "closure_sha256": _sha(files),
        "formal_write_count": 0,
    }


def _source_closure_binding(
    *, isolated_roots: Mapping[str, Path], config: Mapping[str, Any]
) -> dict[str, Any]:
    backend_relatives = (
        "scripts/run_prebuild_multi_agent_v2_concurrency_trial.py",
        "tests/fixtures/prebuild_multi_agent_v2_producers.py",
        "tests/fixtures/prebuild_multi_agent_v2_zero_model_codex.py",
        "lib/preprocessor_core.py",
        "lib/analysis_package_v2.py",
        "lib/historical_compatibility/__init__.py",
        "lib/historical_compatibility/analysis_package_v1.py",
        "lib/analysis_package_store.py",
        "lib/processing_plugin.py",
        "lib/read_fanout.py",
        "lib/read_branch.py",
        "lib/read_bundle.py",
        "lib/orchestration_plan.py",
        "lib/concurrent_dispatch.py",
        "lib/core_dispatch_bridge.py",
        "lib/model_role_contract.py",
        "lib/process_identity.py",
        "lib/skill_binding_contract.py",
        "lib/semantic_contract_v3.py",
        "lib/multi_agent_model_drafts.py",
        "lib/multi_agent_report_contract.py",
        "schemas/terra-initial-draft-v1.json",
        "schemas/luna-investigation-draft-v1.json",
        "schemas/terra-final-draft-v1.json",
        "schemas/luna-investigation-report-v2.json",
        "schemas/terra-final-report-v2.json",
        "schemas/terra-initial-analysis-v1.json",
        "schemas/orchestration-read-plan-v1.json",
        "schemas/read-branch-result-v1.json",
        "schemas/read-bundle-v1.json",
        "schemas/analysis-package-v2.json",
        "schemas/sol-handoff-envelope-v3.json",
    )
    backend_files = []
    for relative in backend_relatives:
        path = ROOT / relative
        if not path.is_file():
            raise TrialError("trial_backend_source_closure_missing", {"path": relative})
        backend_files.append(
            {"path": relative, "sha256": _file_sha(path), "size": path.stat().st_size}
        )
    listed = subprocess.run(
        [
            "git", "ls-files", "--cached", "--others",
            "--exclude-standard", "-z",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if listed.returncode != 0:
        raise TrialError("trial_backend_worktree_manifest_failed")
    trial_root = Path(str(config.get("_trial_root") or "")).resolve()
    worktree_files = []
    for raw_relative in listed.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = raw_relative.decode("utf-8")
        path = (ROOT / relative).resolve()
        try:
            path.relative_to(trial_root)
        except ValueError:
            pass
        else:
            continue
        if path.is_file() and not path.is_symlink():
            worktree_files.append(
                {
                    "path": relative,
                    "sha256": _file_sha(path),
                    "size": path.stat().st_size,
                }
            )
    worktree_files.sort(key=lambda row: row["path"])
    worktree_manifest_sha256 = _sha(worktree_files)
    descriptor_relatives = {
        "math": Path("数学一回滚复习系统/schema/producer-binding-v1.json"),
        "cs408": Path("schema/producer-binding-v1.json"),
        "english": Path("schema/english_pipeline/producer-binding-v1.json"),
    }
    producers: dict[str, Any] = {}
    for subject, relative in descriptor_relatives.items():
        path = isolated_roots[subject] / relative
        if not path.is_file() and subject == "english":
            candidates = sorted(
                (isolated_roots[subject] / "intake/producer-binding").glob(
                    "producer-binding-*.json"
                )
            )
            path = candidates[-1] if candidates else path
        if not path.is_file():
            raise TrialError(
                "trial_producer_descriptor_missing", {"subject": subject}
            )
        descriptor = json.loads(path.read_text(encoding="utf-8"))
        descriptor_core = {
            key: copy.deepcopy(value)
            for key, value in descriptor.items()
            if key != "descriptor_content_sha256"
        }
        if descriptor.get("descriptor_content_sha256") != _sha(descriptor_core):
            raise TrialError(
                "trial_producer_descriptor_content_hash_invalid",
                {"subject": subject},
            )
        normalized_sources = []
        for row in descriptor.get("producer", {}).get("source_files") or []:
            source_path = Path(str(row.get("path") or ""))
            try:
                portable_path = str(source_path.relative_to(isolated_roots[subject]))
            except ValueError:
                raise TrialError(
                    "trial_producer_source_outside_isolated_root",
                    {"subject": subject, "path": str(source_path)},
                )
            if (
                not source_path.is_file()
                or source_path.is_symlink()
                or _file_sha(source_path) != row.get("sha256")
            ):
                raise TrialError(
                    "trial_producer_source_hash_invalid",
                    {"subject": subject, "path": str(source_path)},
                )
            normalized_sources.append(
                {"path": portable_path, "sha256": row.get("sha256")}
            )
        if descriptor.get("producer", {}).get(
            "source_closure_sha256"
        ) != _sha(descriptor.get("producer", {}).get("source_files") or []):
            raise TrialError(
                "trial_producer_source_closure_invalid",
                {"subject": subject},
            )
        normalized_contracts = []
        for row in descriptor.get("capture_contract", {}).get("files") or []:
            contract_path = Path(str(row.get("path") or ""))
            try:
                portable_path = str(contract_path.relative_to(isolated_roots[subject]))
            except ValueError:
                raise TrialError(
                    "trial_capture_contract_outside_isolated_root",
                    {"subject": subject, "path": str(contract_path)},
                )
            if (
                not contract_path.is_file()
                or contract_path.is_symlink()
                or _file_sha(contract_path) != row.get("sha256")
            ):
                raise TrialError(
                    "trial_capture_contract_hash_invalid",
                    {"subject": subject, "path": str(contract_path)},
                )
            normalized_contracts.append(
                {"path": portable_path, "sha256": row.get("sha256")}
            )
        skill = descriptor.get("foreground_skill") or {}
        skill_bytes = []
        for role in ("authoritative", "installed"):
            skill_path = Path(str(skill.get(f"{role}_path") or ""))
            try:
                skill_path.relative_to(isolated_roots[subject])
            except ValueError:
                raise TrialError(
                    "trial_foreground_skill_outside_isolated_root",
                    {"subject": subject, "role": role},
                )
            if (
                not skill_path.is_file()
                or skill_path.is_symlink()
                or _file_sha(skill_path) != skill.get(f"{role}_sha256")
            ):
                raise TrialError(
                    "trial_foreground_skill_hash_invalid",
                    {"subject": subject, "role": role},
                )
            skill_bytes.append(skill_path.read_bytes())
        if skill_bytes[0] != skill_bytes[1]:
            raise TrialError(
                "trial_foreground_skill_parity_invalid", {"subject": subject}
            )
        reproducible_descriptor = {
            "schema_version": descriptor.get("schema_version"),
            "subject": subject,
            "attestation_required_after": descriptor.get(
                "attestation_required_after"
            ),
            "foreground_skill_sha256": descriptor.get(
                "foreground_skill", {}
            ).get("installed_sha256"),
            "producer_files": normalized_sources,
            "capture_contract_files": normalized_contracts,
            "attestation_relative_root": descriptor.get(
                "attestation_relative_root"
            ),
            "formal_write_count": 0,
        }
        producers[subject] = {
            "descriptor_path": str(path),
            "descriptor_sha256": _file_sha(path),
            "source_closure_sha256": descriptor.get("producer", {}).get(
                "source_closure_sha256"
            ),
            "foreground_skill_sha256": descriptor.get(
                "foreground_skill", {}
            ).get("installed_sha256"),
            "reproducible_descriptor_sha256": _sha(reproducible_descriptor),
            "reproducible_descriptor": reproducible_descriptor,
            "formal_write_count": 0,
        }
    mcp_root = Path(str(config.get("_trial_mcp_root") or ""))
    manifest = mcp_root / "release.json"
    codex = Path(str(config.get("codex_path") or ""))
    processing_plugin = config.get("processing_plugin")
    component_lock = Path(
        str(
            processing_plugin.get("component_lock_path")
            if isinstance(processing_plugin, Mapping) else ""
        )
    )
    source_release = Path(str(config.get("_trial_source_release") or ""))
    source_release_manifest = source_release / "release.json"
    plugin_root = component_lock.parent
    generated_plugin_files = {
        name: plugin_root / name
        for name in ("component-lock.json", "components.json", "plugin.json", "mcp.json")
    }
    mcp_python = Path(
        str(
            processing_plugin.get("mcp_client_python")
            if isinstance(processing_plugin, Mapping) else ""
        )
    )
    if (
        not manifest.is_file()
        or not codex.is_file()
        or not component_lock.is_file()
        or not source_release_manifest.is_file()
        or not mcp_python.is_file()
        or any(not path.is_file() for path in generated_plugin_files.values())
    ):
        raise TrialError("trial_runtime_source_binding_missing")
    version = subprocess.run(
        [str(codex), "--version"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, check=False,
    )
    mcp_python_version = subprocess.run(
        [str(mcp_python), "--version"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, check=False,
    )

    replacements = {
        str(source_release): "${SOURCE_RELEASE}",
        str(mcp_root): "${MCP_ROOT}",
        str(codex): "${CODEX_EXECUTABLE}",
        str(mcp_python): "${MCP_PYTHON}",
        **{
            str(isolated_roots[subject]): f"${{{subject.upper()}_ROOT}}"
            for subject in SUBJECTS
        },
    }

    def portable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): portable(nested)
                for key, nested in value.items()
                if not str(key).startswith("_trial_")
            }
        if isinstance(value, list):
            return [portable(nested) for nested in value]
        if isinstance(value, str):
            result = value
            for actual, token in sorted(
                replacements.items(), key=lambda row: len(row[0]), reverse=True
            ):
                result = result.replace(actual, token)
            return result
        return copy.deepcopy(value)

    portable_config = portable(config)
    output_schemas = _active_output_schema_binding(
        config=config, source_release=source_release
    )
    observed = {
        "schema_version": "study-intake-prebuild-active-source-closure-v1",
        "backend": {
            "files": backend_files,
            "closure_sha256": _sha(backend_files),
            "worktree_files": worktree_files,
            "worktree_manifest_sha256": worktree_manifest_sha256,
        },
        "producers": producers,
        "mcp": {
            "release_root": str(mcp_root),
            "release_id": config.get("_trial_mcp_release_id"),
            "manifest_sha256": _file_sha(manifest),
            "canonical_source": copy.deepcopy(
                config.get("_trial_mcp_source_binding")
            ),
        },
        "processing_plugin": {
            "component_lock_path": str(component_lock),
            "component_lock_sha256": _file_sha(component_lock),
            "generated_manifest_sha256s": {
                name: _file_sha(path)
                for name, path in generated_plugin_files.items()
            },
        },
        "source_release": {
            "path": str(source_release),
            "manifest_sha256": _file_sha(source_release_manifest),
        },
        "analysis_package_v2_output_schemas": output_schemas,
        "effective_config": {
            "sha256": _sha(config),
            "portable_sha256": _sha(portable_config),
            "portable": portable_config,
        },
        "mcp_python": {
            "path": str(mcp_python),
            "sha256": _file_sha(mcp_python),
            "version": (
                mcp_python_version.stdout or mcp_python_version.stderr
            ).strip(),
            "version_returncode": mcp_python_version.returncode,
        },
        "codex": {
            "path": str(codex),
            "sha256": _file_sha(codex),
            "version": (version.stdout or version.stderr).strip(),
            "version_returncode": version.returncode,
        },
        "formal_write_count": 0,
    }
    closure_basis = {
        "backend_closure_sha256": observed["backend"]["closure_sha256"],
        "backend_worktree_manifest_sha256": observed["backend"][
            "worktree_manifest_sha256"
        ],
        "producer_descriptor_sha256s": {
            subject: producers[subject]["reproducible_descriptor_sha256"]
            for subject in SUBJECTS
        },
        "mcp_release_id": observed["mcp"]["release_id"],
        "mcp_manifest_sha256": observed["mcp"]["manifest_sha256"],
        "mcp_source_git_head": (
            observed["mcp"].get("canonical_source") or {}
        ).get("git_head"),
        "mcp_source_git_origin": (
            observed["mcp"].get("canonical_source") or {}
        ).get("git_origin"),
        "codex_sha256": observed["codex"]["sha256"],
        "codex_version": observed["codex"]["version"],
        "source_release_manifest_sha256": observed["source_release"][
            "manifest_sha256"
        ],
        "analysis_package_v2_output_schema_sha256s": {
            key: value["sha256"]
            for key, value in observed[
                "analysis_package_v2_output_schemas"
            ]["files"].items()
        },
        "analysis_package_v2_output_schema_closure_sha256": observed[
            "analysis_package_v2_output_schemas"
        ]["closure_sha256"],
        "plugin_component_lock_sha256": observed["processing_plugin"][
            "component_lock_sha256"
        ],
        "plugin_generated_manifest_sha256s": observed[
            "processing_plugin"
        ]["generated_manifest_sha256s"],
        "effective_config_portable_sha256": observed["effective_config"][
            "portable_sha256"
        ],
        "mcp_python_sha256": observed["mcp_python"]["sha256"],
        "mcp_python_version": observed["mcp_python"]["version"],
    }
    return {
        **observed,
        "closure_basis": closure_basis,
        "closure_sha256": _sha(closure_basis),
    }


def production_surface_snapshot(
    *, production_runtime: Path | None = None,
    production_roots: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Hash production current, formal surfaces, and live service topology."""

    runtime = (
        production_runtime
        or Path.home() / ".codex" / "study-intake-preprocessor"
    ).expanduser().resolve()
    roots = dict(
        production_roots
        or {
            "math": Path.home() / "Documents" / "kaoyan-math",
            "cs408": Path.home() / "Documents" / "kaoyan-408",
            "english": Path.home() / "Documents" / "kaoyan-english",
        }
    )
    current = runtime / "current"
    current_value = None
    if current.is_symlink():
        current_value = os.readlink(current)
    elif current.exists():
        current_value = str(current.resolve())
    formal_relatives = {
        "math": ("错题知识网络/错题卡", "数学一回滚复习系统/复习单元.json"),
        "cs408": ("复习单元总表.md", "复习单元节点映射.md", "原题复做轨总表.md"),
        "english": ("bank/master_bank.csv", "bank/mastered_items.csv", "bank/sentence_patterns.md"),
    }
    formal = {
        subject: {
            relative: _tree_fingerprint(root / relative)
            for relative in formal_relatives[subject]
        }
        for subject, root in roots.items()
    }
    config_path = current / "config.json"
    full_formal_manifest: dict[str, Any] | None = None
    formal_manifest_status = "unavailable"
    if config_path.is_file():
        try:
            spec = importlib.util.spec_from_file_location(
                "prebuild_formal_surface_manifest",
                ROOT / "bin/formal_surface_manifest.py",
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("formal surface loader missing")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            production_config = json.loads(
                config_path.read_text(encoding="utf-8")
            )
            captured = module.capture(
                production_config, subjects=SUBJECTS
            )
            full_formal_manifest = {
                key: copy.deepcopy(value)
                for key, value in captured.items()
                if key != "captured_at"
            }
            formal_manifest_status = "captured"
        except Exception as exc:
            full_formal_manifest = {
                "error_type": type(exc).__name__,
                "error_code": str(exc),
            }
            formal_manifest_status = "failed"
    processes = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,command="],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    relevant = sorted(
        line.strip()
        for line in processes.stdout.splitlines()
        if "study-intake" in line.lower() or "kaoyan-read" in line.lower()
    )
    return {
        "schema_version": "study-intake-prebuild-production-surface-v1",
        "current": current_value,
        "current_entry_sha256": _tree_fingerprint(current),
        "production_roots": {
            subject: str(root.resolve()) for subject, root in roots.items()
        },
        "formal_surfaces": formal,
        "formal_surface_manifest_status": formal_manifest_status,
        "formal_surface_manifest": full_formal_manifest,
        "service_process_summary": relevant,
        "formal_write_count": 0,
    }


def _copy_isolated_roots(
    source_roots: Mapping[str, Path], trial_root: Path
) -> dict[str, Path]:
    isolated: dict[str, Path] = {}
    for subject in SUBJECTS:
        source = Path(source_roots[subject]).expanduser().resolve(strict=True)
        target = trial_root / "subjects" / subject
        shutil.copytree(
            source,
            target,
            symlinks=False,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        isolated[subject] = target.resolve()
    if len(set(isolated.values())) != 3:
        raise TrialError("trial_subject_isolation_invalid")
    return isolated


def _canonical_source_root_binding(
    source_roots: Mapping[str, Path], *, require_exact: bool,
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    required_markers = {
        "math": Path("tests/test_quick_intake.py"),
        "cs408": Path(
            "tests/test_morning_review_prepared_pack_managed_hot_408.py"
        ),
        "english": Path("english_pipeline/cli.py"),
    }
    for subject in SUBJECTS:
        root = Path(source_roots[subject]).expanduser().resolve(strict=True)
        if (
            root == PRODUCTION_SUBJECT_ROOTS[subject].resolve()
            or require_exact
            and root != CANONICAL_SOURCE_ROOTS[subject].resolve()
            or not (root / ".git").exists()
            or not (root / required_markers[subject]).is_file()
        ):
            raise TrialError(
                "trial_canonical_source_root_invalid",
                {"subject": subject, "root": str(root)},
            )
        if subject == "cs408":
            text_value = (root / required_markers[subject]).read_text(
                encoding="utf-8"
            )
            if "def setUpModule" not in text_value or "def tearDownModule" not in text_value:
                raise TrialError("trial_cs408_canonical_producer_fixture_missing")
        if subject == "english" and "capture-raw-turn" not in (
            root / required_markers[subject]
        ).read_text(encoding="utf-8"):
            raise TrialError("trial_english_canonical_producer_entrypoint_missing")
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        origin = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        if (
            head.returncode != 0
            or status.returncode != 0
            or status.stdout.strip()
            or origin.returncode != 0
            or not origin.stdout.strip()
        ):
            raise TrialError(
                "trial_canonical_source_worktree_not_clean",
                {"subject": subject},
            )
        rows[subject] = {
            "path": str(root),
            "git_head": head.stdout.strip(),
            "git_origin": origin.stdout.strip(),
            "git_status_porcelain": "",
            "producer_marker_path": str(required_markers[subject]),
        }
    return {
        "schema_version": "study-intake-prebuild-source-root-binding-v1",
        "canonical_source_roots": rows,
        "production_roots": {
            subject: str(path.resolve())
            for subject, path in PRODUCTION_SUBJECT_ROOTS.items()
        },
        "formal_write_count": 0,
    }


def _validate_capture_set(captures: Sequence[CaptureEnvelope]) -> None:
    if len(captures) != 9:
        raise TrialError("trial_capture_count_invalid", {"count": len(captures)})
    ids: set[str] = set()
    counts = {subject: 0 for subject in SUBJECTS}
    for row in captures:
        if row.subject not in SUBJECTS or row.candidate.subject != row.subject:
            raise TrialError("trial_capture_subject_invalid")
        if row.capture_id != row.candidate.capture_id:
            raise TrialError("trial_capture_candidate_binding_invalid")
        if not row.producer_entrypoint or not row.producer_receipt:
            raise TrialError("trial_real_producer_entrypoint_missing")
        if row.capture_id in ids:
            raise TrialError("trial_capture_identity_reused")
        ids.add(row.capture_id)
        counts[row.subject] += 1
        if _nonzero_formal_writes(row.producer_receipt):
            raise TrialError("trial_producer_formal_write_detected")
    if counts != {subject: 3 for subject in SUBJECTS}:
        raise TrialError("trial_subject_capture_count_invalid", counts)


def _intervals_overlap(rows: Sequence[Mapping[str, Any]]) -> bool:
    if len(rows) < 2:
        return False
    return max(_seconds(str(row["started_at"])) for row in rows) < min(
        _seconds(str(row["ended_at"])) for row in rows
    )


def _maximum_interval_overlap(rows: Sequence[Mapping[str, Any]]) -> int:
    events = []
    for row in rows:
        events.append((_seconds(str(row["started_at"])), 1))
        events.append((_seconds(str(row["ended_at"])), -1))
    # End before start at an identical timestamp: touching is not overlap.
    active = maximum = 0
    for _at, delta in sorted(events, key=lambda value: (value[0], value[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def _cross_subject_overlap(rows: Sequence[Mapping[str, Any]]) -> bool:
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            if left.get("subject") == right.get("subject"):
                continue
            if max(
                _seconds(str(left["started_at"])),
                _seconds(str(right["started_at"])),
            ) < min(
                _seconds(str(left["ended_at"])),
                _seconds(str(right["ended_at"])),
            ):
                return True
    return False


def _three_subject_overlap(rows: Sequence[Mapping[str, Any]]) -> bool:
    boundaries = sorted(
        {
            _seconds(str(row[key]))
            for row in rows
            for key in ("started_at", "ended_at")
        }
    )
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        probe = (left + right) / 2
        active_subjects = {
            str(row.get("subject"))
            for row in rows
            if _seconds(str(row["started_at"])) < probe
            < _seconds(str(row["ended_at"]))
        }
        if active_subjects == set(SUBJECTS):
            return True
    return False


def _trial_processes(trial_root: Path) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,command="],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    marker = str(trial_root.resolve())
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if marker not in line:
            continue
        parts = line.strip().split(None, 3)
        if len(parts) == 4:
            rows.append(
                {
                    "pid": int(parts[0]), "ppid": int(parts[1]),
                    "pgid": int(parts[2]), "command": parts[3],
                }
            )
    return rows


def _transport_proof(
    *, transport: Mapping[str, Any], subject: str, capture_id: str,
    marker: str, read_session_id: str,
) -> dict[str, Any]:
    context_hashes: list[str] = []
    artifact_hashes: list[str] = []
    library_hashes: list[str] = []
    library_no_match_hashes: list[str] = []
    expected_artifact_ids: list[str] | None = None
    observed_artifact_ids: set[str] = set()
    completed_artifact_ids: set[str] = set()
    marker_artifact_ids: set[str] = set()
    library_collections: set[str] = set()
    validated_mcp_tool_call_count = 0
    library_tools = {"list_records", "get_records", "search_records", "query_relations"}
    for row in transport.get("mcp_items") or []:
        item = row.get("item") if isinstance(row, Mapping) else None
        if not isinstance(item, Mapping):
            continue
        tool = str(
            item.get("tool") or item.get("tool_name")
            or item.get("tool-name") or ""
        )
        server = str(
            item.get("server") or item.get("server_name")
            or item.get("server-name") or ""
        )
        result = item.get("result")
        try:
            structured = CodexRunner._validated_mcp_result_envelope(
                result, stage_name=f"{subject}_luna_analysis"
            )
        except Exception:
            continue
        if structured.get("ok") is not True:
            error = structured.get("error")
            requested = _call_collections(item)
            compact_keys = {
                "error", "formal_write_count", "mcp_tool_call_count",
                "model_call_count", "ok", "request_id",
                "schema_version", "server_release", "tool",
            }
            if (
                transport.get("subject") == subject
                and transport.get("read_session_id") == read_session_id
                and server == EXPECTED_MCP_SERVERS[subject]
                and tool in library_tools
                and set(structured) == compact_keys
                and structured.get("schema_version")
                == "study-read-mcp.v3"
                and isinstance(structured.get("server_release"), str)
                and bool(structured.get("server_release"))
                and structured.get("tool") == tool
                and structured.get("formal_write_count") == 0
                and structured.get("model_call_count") == 0
                and structured.get("mcp_tool_call_count") == 0
                and isinstance(structured.get("request_id"), str)
                and re.fullmatch(
                    r"[0-9a-f]{16}", str(structured.get("request_id"))
                )
                is not None
                and isinstance(error, Mapping)
                and set(error) == {"code", "message", "retryable"}
                and error.get("code") == "NOT_FOUND"
                and isinstance(error.get("message"), str)
                and bool(error.get("message"))
                and isinstance(error.get("retryable"), bool)
                and requested
                and requested.issubset(ALLOWED_COLLECTIONS[subject])
            ):
                library_collections.update(requested)
                library_no_match_hashes.append(_sha(structured))
            continue
        session = structured.get("read_session")
        if (
            structured.get("subject") != subject
            or server != EXPECTED_MCP_SERVERS[subject]
            or not isinstance(session, Mapping)
            or session.get("subject") != subject
            or session.get("capture_id") != capture_id
            or session.get("read_session_id") != read_session_id
        ):
            continue
        validated_mcp_tool_call_count += 1
        digest = _sha(structured)
        if tool == "get_task_context" and (
            any(
                isinstance(value, Mapping)
                and value.get("capture_id") == capture_id
                and value.get("subject") == subject
                for value in structured.get("items") or []
            )
        ):
            context_hashes.append(digest)
            artifact_ids = session.get("artifact_ids")
            if (
                isinstance(artifact_ids, list)
                and artifact_ids
                and all(isinstance(value, str) and value for value in artifact_ids)
                and len(set(artifact_ids)) == len(artifact_ids)
            ):
                expected_artifact_ids = list(artifact_ids)
        elif tool == "read_task_artifact":
            arguments = item.get("arguments")
            artifact_id = (
                str(arguments.get("artifact_id"))
                if isinstance(arguments, Mapping)
                and isinstance(arguments.get("artifact_id"), str)
                else ""
            )
            if not artifact_id:
                continue
            observed_artifact_ids.add(artifact_id)
            artifact_hashes.append(digest)
            if structured.get("complete") is True:
                completed_artifact_ids.add(artifact_id)
            if marker in json.dumps(
                structured, ensure_ascii=False, sort_keys=True
            ):
                marker_artifact_ids.add(artifact_id)
        elif tool in library_tools and (
            server == EXPECTED_MCP_SERVERS[subject]
        ):
            requested = _call_collections(item)
            returned = {
                str(value.get("collection"))
                for value in structured.get("items") or []
                if isinstance(value, Mapping)
                and isinstance(value.get("collection"), str)
            }
            collections = requested or returned
            if collections and collections.issubset(ALLOWED_COLLECTIONS[subject]):
                library_collections.update(collections)
                library_hashes.append(digest)
    expected_set = set(expected_artifact_ids or [])
    task_artifacts_verified = bool(expected_set) and (
        observed_artifact_ids == expected_set
        and completed_artifact_ids == expected_set
    )
    return {
        "task_context_verified": len(context_hashes) == 1,
        "task_context_result_sha256s": context_hashes,
        "task_artifacts_verified": task_artifacts_verified,
        "task_artifact_ids": sorted(expected_set),
        "completed_task_artifact_ids": sorted(completed_artifact_ids),
        "artifact_marker_verified": bool(
            marker_artifact_ids & completed_artifact_ids
        ),
        "artifact_result_sha256s": artifact_hashes,
        "library_result_verified": bool(
            library_hashes or library_no_match_hashes
        ),
        "library_result_sha256s": [
            *library_hashes,
            *library_no_match_hashes,
        ],
        "library_no_match_result_sha256s": library_no_match_hashes,
        "library_collections": sorted(library_collections),
        "validated_mcp_tool_call_count": validated_mcp_tool_call_count,
        "formal_write_count": 0,
    }


def validate_trial_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all hard gates; quality warnings never weaken technical gates."""

    tasks = report.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 9:
        raise TrialError("trial_completion_count_invalid")
    if report.get("completion_count") != 9:
        raise TrialError("trial_completion_count_invalid")
    if report.get("model_call_count") != 45:
        raise TrialError("trial_model_call_count_invalid")
    if report.get("production_surface_before") != report.get(
        "production_surface_after"
    ):
        raise TrialError("trial_production_surface_changed")
    if _nonzero_formal_writes(report):
        raise TrialError("trial_formal_write_detected")
    root_binding = report.get("source_root_binding")
    canonical_roots = (
        root_binding.get("canonical_source_roots")
        if isinstance(root_binding, Mapping) else None
    )
    production_roots = (
        root_binding.get("production_roots")
        if isinstance(root_binding, Mapping) else None
    )
    if (
        not isinstance(canonical_roots, Mapping)
        or set(canonical_roots) != set(SUBJECTS)
        or not isinstance(production_roots, Mapping)
        or set(production_roots) != set(SUBJECTS)
        or any(
            str((canonical_roots[subject] or {}).get("path") or "")
            == str(production_roots[subject])
            for subject in SUBJECTS
        )
    ):
        raise TrialError("trial_source_root_binding_invalid")
    source_binding = report.get("immutable_source_binding")
    if not isinstance(source_binding, Mapping):
        raise TrialError("trial_source_binding_missing")
    source_basis = source_binding.get("closure_basis")
    if (
        not isinstance(source_basis, Mapping)
        or source_binding.get("closure_sha256") != _sha(source_basis)
        or report.get("immutable_source_binding_after", {}).get(
            "closure_sha256"
        ) != source_binding.get("closure_sha256")
    ):
        raise TrialError("trial_source_binding_invalid")

    identities: set[tuple[str, str]] = set()
    sessions: set[str] = set()
    task_intervals: list[Mapping[str, Any]] = []
    luna_intervals: list[Mapping[str, Any]] = []
    subject_task_counts = {subject: 0 for subject in SUBJECTS}
    diagnostic_warnings: list[str] = []
    for task in tasks:
        subject = str(task.get("subject") or "")
        capture_id = str(task.get("capture_id") or "")
        identity = (subject, capture_id)
        if subject not in SUBJECTS or not capture_id or identity in identities:
            raise TrialError("trial_task_identity_invalid")
        identities.add(identity)
        subject_task_counts[subject] += 1
        if task.get("status") != "completed":
            raise TrialError("trial_task_incomplete", {"task": identity})
        completion = task.get("dispatcher_completion")
        if (
            not isinstance(completion, Mapping)
            or completion.get("subject") != subject
            or completion.get("capture_id") != capture_id
            or completion.get("outcome") != "succeeded"
            or not completion.get("unit_sha256")
            or completion.get("lease_fence") != 1
        ):
            raise TrialError("trial_dispatcher_completion_invalid")
        if task.get("semantic_stage_count") != 5:
            raise TrialError("trial_model_call_count_invalid", {"task": identity})
        if task.get("active_processes_after") not in ([], ()):  # exact cleanup
            raise TrialError("trial_process_cleanup_failed", {"task": identity})
        interval = task.get("task_interval")
        if not isinstance(interval, Mapping):
            raise TrialError("trial_task_interval_missing")
        task_intervals.append(interval)

        providers = task.get("provider_intervals")
        if not isinstance(providers, list) or len(providers) != 5:
            raise TrialError("trial_provider_topology_invalid")
        terra = [row for row in providers if row.get("provider") == "terra"]
        luna = [row for row in providers if row.get("provider") == "luna"]
        if (
            [row.get("phase") for row in providers]
            != ["terra_initial", "luna", "luna", "luna", "terra_final"]
            or len(terra) != 2
            or len(luna) != 3
        ):
            raise TrialError("trial_provider_topology_invalid")
        if not _intervals_overlap(luna):
            raise TrialError(
                "trial_capture_luna_branches_not_concurrent",
                {"subject": subject, "capture_id": capture_id},
            )
        if any(row.get("mcp_tool_call_count") != 0 for row in terra):
            raise TrialError("trial_terra_mcp_access_detected")
        if (
            _seconds(str(terra[0]["ended_at"]))
            > min(_seconds(str(row["started_at"])) for row in luna)
            or max(_seconds(str(row["ended_at"])) for row in luna)
            > _seconds(str(terra[1]["started_at"]))
        ):
            raise TrialError(
                "trial_provider_stage_order_invalid",
                {"subject": subject, "capture_id": capture_id},
            )
        luna_intervals.extend(
            {
                **copy.deepcopy(dict(row)),
                "subject": subject,
                "capture_id": capture_id,
            }
            for row in luna
        )

        reports = task.get("luna_reports")
        diagnostics = task.get("luna_diagnostics")
        package = task.get("package")
        marker = str((task.get("producer_receipt") or {}).get("prebuild_marker") or "")
        if not marker:
            raise TrialError("trial_capture_marker_missing")
        if (
            not isinstance(reports, list)
            or not reports
            or not isinstance(diagnostics, list)
            or len(reports) + len(diagnostics) != 3
        ):
            raise TrialError("trial_luna_terminal_material_count_invalid")
        if not isinstance(package, Mapping):
            raise TrialError("trial_package_missing")
        plan_sha256 = package.get("plan_sha256")
        read_bundle_sha256 = package.get("read_bundle_sha256")
        if (
            not plan_sha256
            or not read_bundle_sha256
            or not package.get("plan_object_sha256")
            or not package.get("read_bundle_object_sha256")
            or not package.get("terra_initial_report_sha256")
        ):
            raise TrialError("trial_plan_read_bundle_binding_invalid")
        plan_branch_ids = package.get("plan_branch_ids")
        if (
            not isinstance(plan_branch_ids, list)
            or len(plan_branch_ids) != 3
            or any(not isinstance(value, str) or not value for value in plan_branch_ids)
            or len(set(plan_branch_ids)) != 3
        ):
            raise TrialError("trial_plan_branch_set_invalid")
        report_by_branch = {
            str(row.get("branch_id")): row
            for row in reports if isinstance(row, Mapping)
        }
        diagnostic_by_branch = {
            str(row.get("branch_id")): row
            for row in diagnostics if isinstance(row, Mapping)
        }
        if (
            len(report_by_branch) != len(reports)
            or len(diagnostic_by_branch) != len(diagnostics)
            or set(report_by_branch) & set(diagnostic_by_branch)
            or set(report_by_branch) | set(diagnostic_by_branch)
            != set(plan_branch_ids)
        ):
            raise TrialError("trial_luna_terminal_branch_coverage_invalid")
        bindings = package.get("ordered_luna_reports")
        diagnostic_bindings = package.get("ordered_luna_diagnostics")
        if not isinstance(bindings, list) or len(bindings) != len(reports):
            raise TrialError("trial_package_report_binding_invalid")
        if (
            not isinstance(diagnostic_bindings, list)
            or len(diagnostic_bindings) != len(diagnostics)
        ):
            raise TrialError("trial_package_diagnostic_binding_invalid")
        final_projection = [
            {
                "branch_id": row.get("branch_id"),
                "report_sha256": row.get("report_sha256") or row.get("sha256"),
                "report_ref": (
                    "study-intake-luna-investigation-report://sha256/"
                    + str(row.get("report_sha256") or row.get("sha256"))
                ),
            }
            for row in bindings
            if isinstance(row, Mapping)
        ]
        final_diagnostic_projection = [
            {
                "branch_id": branch_id,
                "diagnostic_sha256": diagnostic_by_branch[branch_id].get(
                    "diagnostic_sha256"
                ),
                "diagnostic_ref": (
                    "study-intake-luna-diagnostic-record://sha256/"
                    + str(
                        diagnostic_by_branch[branch_id].get(
                            "diagnostic_sha256"
                        )
                    )
                ),
                "status": diagnostic_by_branch[branch_id].get(
                    "diagnostic_status"
                ),
            }
            for branch_id in plan_branch_ids
            if branch_id in diagnostic_by_branch
        ]
        expected_coverage = [
            {
                "branch_id": branch_id,
                "outcome": (
                    "report" if branch_id in report_by_branch else "diagnostic"
                ),
            }
            for branch_id in plan_branch_ids
        ]
        assessments = package.get("terra_final_branch_assessments")
        if (
            package.get("terra_final_ordered_luna_reports")
            != final_projection
            or package.get("terra_final_ordered_luna_diagnostics")
            != final_diagnostic_projection
            or package.get("terra_final_branch_coverage") != expected_coverage
            or not isinstance(assessments, list)
            or any(not isinstance(row, Mapping) for row in assessments)
            or [row.get("branch_id") for row in assessments]
            != plan_branch_ids
            or any(
                not isinstance(row, Mapping)
                or row.get("outcome") != expected_coverage[index]["outcome"]
                or not isinstance(row.get("rationale"), str)
                or not row.get("rationale")
                for index, row in enumerate(assessments)
            )
            or not package.get("terra_final_report_sha256")
        ):
            raise TrialError("trial_terra_final_report_binding_invalid")
        binding_by_branch = {
            str(row.get("branch_id")): row
            for row in bindings if isinstance(row, Mapping)
        }
        diagnostic_binding_by_branch = {
            str(row.get("branch_id")): row
            for row in diagnostic_bindings if isinstance(row, Mapping)
        }
        if len(binding_by_branch) != len(reports):
            raise TrialError("trial_package_report_binding_invalid")
        if len(diagnostic_binding_by_branch) != len(diagnostics):
            raise TrialError("trial_package_diagnostic_binding_invalid")

        luna_by_branch = {
            str(row.get("branch_id")): row
            for row in luna if isinstance(row, Mapping)
        }
        if len(luna_by_branch) != 3 or set(luna_by_branch) != set(plan_branch_ids):
            raise TrialError("trial_provider_topology_invalid")
        for branch_id in plan_branch_ids:
            material_kind = (
                "report" if branch_id in report_by_branch else "diagnostic"
            )
            material_row = (
                report_by_branch[branch_id]
                if material_kind == "report"
                else diagnostic_by_branch[branch_id]
            )
            luna_row = luna_by_branch[branch_id]
            if (
                material_row.get("subject") != subject
                or material_row.get("capture_id") != capture_id
                or material_row.get("plan_sha256") != plan_sha256
                or (
                    material_kind == "report"
                    and material_row.get("read_bundle_sha256")
                    != read_bundle_sha256
                )
                or not material_row.get("branch_result_sha256")
                or not material_row.get("branch_result_object_sha256")
            ):
                raise TrialError("trial_cross_capture_report_binding")
            if material_kind == "diagnostic" and (
                material_row.get("diagnostic_status")
                not in {"failed", "cancelled", "timed_out"}
                or not material_row.get("diagnostic_sha256")
            ):
                raise TrialError("trial_luna_diagnostic_invalid")
            session_id = str(material_row.get("read_session_id") or "")
            if not session_id or session_id in sessions:
                raise TrialError("trial_read_session_not_independent")
            sessions.add(session_id)
            if luna_row.get("read_session_id") != session_id:
                raise TrialError("trial_cross_capture_session_binding")
            if material_row.get("mcp_server") != EXPECTED_MCP_SERVERS[subject]:
                raise TrialError("trial_cross_subject_mcp_binding")
            if (
                material_row.get("capture_marker") != marker
                or not material_row.get("marker_transport_sha256")
            ):
                raise TrialError("trial_mcp_capture_marker_missing")
            proof = material_row.get("transport_proof")
            if (
                not isinstance(proof, Mapping)
                or proof.get("task_context_verified") is not True
                or proof.get("task_artifacts_verified") is not True
                or proof.get("artifact_marker_verified") is not True
            ):
                raise TrialError("trial_mcp_capture_marker_missing")
            collections = material_row.get("collections")
            evidence_refs = material_row.get("evidence_refs")
            if (
                luna_row.get("mcp_tool_call_count", 0) < 1
                or proof.get("library_result_verified") is not True
                or not isinstance(collections, list)
                or not collections
                or not set(collections).issubset(ALLOWED_COLLECTIONS[subject])
                or (
                    material_kind == "report"
                    and (
                        not isinstance(evidence_refs, list)
                        or not evidence_refs
                    )
                )
            ):
                raise TrialError("trial_luna_mcp_evidence_missing")
            expected_binding = (
                binding_by_branch.get(branch_id)
                if material_kind == "report"
                else diagnostic_binding_by_branch.get(branch_id)
            )
            expected_digest = (
                material_row.get("report_sha256")
                if material_kind == "report"
                else material_row.get("diagnostic_sha256")
            )
            if (
                not isinstance(expected_binding, Mapping)
                or (
                    expected_binding.get("report_sha256")
                    or expected_binding.get("diagnostic_sha256")
                    or expected_binding.get("sha256")
                )
                != expected_digest
            ):
                raise TrialError(
                    "trial_package_report_binding_invalid"
                    if material_kind == "report"
                    else "trial_package_diagnostic_binding_invalid"
                )
            if material_kind == "diagnostic":
                diagnostic_warnings.append(
                    str(material_row.get("diagnostic_error_code"))
                    if material_row.get("diagnostic_error_code")
                    else f"luna_diagnostic:{subject}:{capture_id}:{branch_id}"
                )

    if len(sessions) != 27 or report.get("independent_read_session_count") != 27:
        raise TrialError("trial_read_session_count_invalid")
    if subject_task_counts != {subject: 3 for subject in SUBJECTS}:
        raise TrialError("trial_subject_task_count_invalid", subject_task_counts)
    if not _intervals_overlap(task_intervals):
        raise TrialError("trial_tasks_not_concurrent")
    maximum_luna_overlap = _maximum_interval_overlap(luna_intervals)
    if (
        maximum_luna_overlap < 3
        or not _cross_subject_overlap(luna_intervals)
        or not _three_subject_overlap(luna_intervals)
    ):
        raise TrialError("trial_provider_calls_not_concurrent")
    if report.get("remaining_processes") not in ([], ()):  # global cleanup
        raise TrialError("trial_process_cleanup_failed")

    checked = copy.deepcopy(dict(report))
    checked["content_quality_warnings"] = sorted(
        {
            *(str(value) for value in checked.get("content_quality_warnings") or []),
            *diagnostic_warnings,
        }
    )
    checked["maximum_concurrent_luna_provider_count"] = maximum_luna_overlap
    checked["cross_subject_luna_overlap"] = True
    checked["three_subject_luna_overlap"] = True
    checked["technical_gate"] = "PASS"
    checked["status"] = (
        "PASS_WITH_CONTENT_WARNINGS"
        if checked.get("content_quality_warnings")
        else "PASS"
    )
    return checked


def run_trial_core(
    *,
    source_roots: Mapping[str, Path],
    trial_root: Path,
    producer: CaptureProducer,
    executor: TaskExecutor | None = None,
    executor_factory: TaskExecutorFactory | None = None,
    surface_snapshot: Callable[[], Mapping[str, Any]] = production_surface_snapshot,
    source_root_binding: Mapping[str, Any] | None = None,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Copy roots, invoke Producers, release nine tasks, and verify evidence."""

    before = copy.deepcopy(dict(surface_snapshot()))
    isolated = _copy_isolated_roots(source_roots, trial_root)
    captures = list(producer(isolated, trial_root))
    _validate_capture_set(captures)
    if (executor is None) == (executor_factory is None):
        raise TrialError("trial_executor_binding_invalid")
    if executor_factory is not None:
        executor = executor_factory(isolated, trial_root, captures)
    assert executor is not None
    source_binding_factory = getattr(executor, "source_binding", None)
    if not callable(source_binding_factory):
        raise TrialError("trial_source_binding_factory_missing")
    immutable_source_binding = copy.deepcopy(dict(source_binding_factory()))
    runtime_root = trial_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    barrier = threading.Barrier(9)
    results: list[dict[str, Any]] = []

    def execute(capture: CaptureEnvelope) -> dict[str, Any]:
        try:
            barrier.wait(timeout=min(timeout_seconds, 60.0))
        except threading.BrokenBarrierError as exc:
            raise TrialError("trial_nine_task_release_barrier_failed") from exc
        started_at = _utc_now()
        started = time.monotonic()
        outcome = copy.deepcopy(dict(executor(capture, runtime_root)))
        ended_at = _utc_now()
        outcome.setdefault(
            "task_interval",
            {
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            },
        )
        outcome.setdefault("subject", capture.subject)
        outcome.setdefault("capture_id", capture.capture_id)
        outcome.setdefault("producer_entrypoint", capture.producer_entrypoint)
        outcome.setdefault("producer_receipt", copy.deepcopy(capture.producer_receipt))
        return outcome

    with ThreadPoolExecutor(max_workers=9, thread_name_prefix="prebuild-v2") as pool:
        futures = {pool.submit(execute, capture): capture for capture in captures}
        try:
            for future in as_completed(futures, timeout=timeout_seconds):
                results.append(future.result())
        except TimeoutError as exc:
            for future in futures:
                future.cancel()
            cancel_all = getattr(executor, "cancel_all", None)
            if callable(cancel_all):
                cancel_all("prebuild_trial_timeout")
            raise TrialError("trial_timeout") from exc
        except BaseException:
            for future in futures:
                future.cancel()
            cancel_all = getattr(executor, "cancel_all", None)
            if callable(cancel_all):
                cancel_all("prebuild_trial_peer_failure")
            raise
    results.sort(key=lambda row: (SUBJECTS.index(str(row["subject"])), str(row["capture_id"])))
    drain = getattr(executor, "drain", None)
    if callable(drain) and drain() is not True:
        raise TrialError("trial_dispatcher_drain_failed")
    immutable_source_binding_after = copy.deepcopy(
        dict(source_binding_factory())
    )
    if (
        immutable_source_binding_after.get("closure_sha256")
        != immutable_source_binding.get("closure_sha256")
    ):
        raise TrialError(
            "trial_source_closure_changed_during_execution",
            {
                "before": str(immutable_source_binding.get("closure_sha256")),
                "after": str(
                    immutable_source_binding_after.get("closure_sha256")
                ),
            },
        )
    after = copy.deepcopy(dict(surface_snapshot()))
    report = {
        "schema_version": "study-intake-prebuild-multi-agent-v2-concurrency-trial-v1",
        "execution_mode": "isolated_real_model",
        "task_count": 9,
        "completion_count": sum(row.get("status") == "completed" for row in results),
        "model_call_count": sum(
            int(row.get("semantic_stage_count") or 0) for row in results
        ),
        "independent_read_session_count": sum(
            len(row.get("luna_reports") or [])
            + len(row.get("luna_diagnostics") or [])
            for row in results
        ),
        "tasks": results,
        "production_surface_before": before,
        "production_surface_after": after,
        "remaining_processes": [
            process
            for row in results
            for process in (row.get("active_processes_after") or [])
        ] + _trial_processes(trial_root),
        "content_quality_warnings": sorted(
            {
                str(warning)
                for row in results
                for warning in (row.get("content_quality_warnings") or [])
            }
        ),
        "immutable_source_binding": immutable_source_binding,
        "immutable_source_binding_after": immutable_source_binding_after,
        "source_root_binding": copy.deepcopy(
            dict(
                source_root_binding
                or {
                    "schema_version": (
                        "study-intake-prebuild-source-root-binding-v1"
                    ),
                    "canonical_source_roots": {
                        subject: {"path": str(Path(source_roots[subject]).resolve())}
                        for subject in SUBJECTS
                    },
                    "production_roots": {
                        subject: str(path.resolve())
                        for subject, path in PRODUCTION_SUBJECT_ROOTS.items()
                    },
                    "formal_write_count": 0,
                }
            )
        ),
        "formal_write_count": 0,
    }
    return validate_trial_report(report)


class _ProviderProcessObserver:
    """Poll exact Codex Provider processes without changing their lifecycle."""

    def __init__(self, *, codex_path: Path, trial_root: Path) -> None:
        self.codex_path = codex_path.resolve()
        self.trial_root = trial_root.resolve()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.rows: dict[int, dict[str, Any]] = {}
        self.thread = threading.Thread(
            target=self._run, name="prebuild-provider-observer", daemon=True
        )
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(0.05):
            observed_at = _utc_now()
            completed = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,command="],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, check=False,
            )
            for line in completed.stdout.splitlines():
                parts = line.strip().split(None, 2)
                if len(parts) != 3:
                    continue
                try:
                    pid, ppid = int(parts[0]), int(parts[1])
                    argv = shlex.split(parts[2])
                except (ValueError, OSError):
                    continue
                exact_executable = Path(argv[0]).resolve() == self.codex_path
                zero_model_wrapper = bool(
                    self.codex_path.name
                    == "prebuild_multi_agent_v2_zero_model_codex.py"
                    and len(argv) > 1
                    and Path(argv[1]).resolve() == self.codex_path
                )
                if (
                    len(argv) < 3
                    or not (exact_executable or zero_model_wrapper)
                    or "exec" not in argv[1:3]
                    or "--output-last-message" not in argv
                    or str(self.trial_root) not in parts[2]
                ):
                    continue
                output_index = argv.index("--output-last-message") + 1
                if output_index >= len(argv):
                    continue
                output_path = Path(argv[output_index])
                match = __import__("re").search(
                    r"model-(math|cs408|english)_"
                    r"(analysis|luna_analysis|critical_review)-",
                    output_path.name,
                )
                if match is None:
                    continue
                manifest_match = __import__("re").search(
                    r'--read-session-manifest["\\, ]+([^"\\,\]]+)',
                    parts[2],
                )
                with self.lock:
                    row = self.rows.setdefault(
                        pid,
                        {
                            "pid": pid, "ppid": ppid,
                            "subject": match.group(1),
                            "provider_stage": match.group(2),
                            "started_at": observed_at,
                            "ended_at": observed_at,
                            "output_path": str(output_path),
                            "read_session_manifest_path": (
                                manifest_match.group(1)
                                if manifest_match else None
                            ),
                            "sample_count": 0,
                            "zero_model_wrapper": zero_model_wrapper,
                        },
                    )
                    row["ended_at"] = observed_at
                    row["sample_count"] += 1

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(2)
        path = self.trial_root / "provider-process-observations.json"
        if not path.exists():
            path.write_bytes(
                _canonical(
                    {
                        "schema_version": (
                            "study-intake-prebuild-provider-observations-v1"
                        ),
                        "rows": sorted(
                            self.snapshot(), key=lambda row: int(row["pid"])
                        ),
                        "formal_write_count": 0,
                    }
                )
            )

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [copy.deepcopy(row) for row in self.rows.values()]


def _real_executor_factory(
    config: Mapping[str, Any], captures: Sequence[CaptureEnvelope],
    runtime_root: Path,
) -> TaskExecutor:
    from concurrent_dispatch import (
        ConcurrentDispatcher, FrozenTask, TaskExecutionContext,
        dispatch_rule_binding,
    )
    from core_dispatch_bridge import CoreCandidateSubprocessRunner

    release_id = str(config["authority_release_id"])
    capture_by_identity = {
        (row.subject, row.capture_id): row for row in captures
    }
    tasks: dict[tuple[str, str], FrozenTask] = {}
    for row in captures:
        processing_sha = row.candidate.input_binding.get(
            "processing_contract_sha256"
        )
        if not isinstance(processing_sha, str):
            raise TrialError("trial_candidate_processing_contract_missing")
        tasks[(row.subject, row.capture_id)] = FrozenTask(
            {
                "subject": row.subject,
                "capture_id": row.capture_id,
                "study_date": row.candidate.study_date,
                "recorded_at": row.candidate.recorded_at,
                "input_fingerprint": row.candidate.input_fingerprint,
                "input_binding": copy.deepcopy(row.candidate.input_binding),
                "model_input": copy.deepcopy(row.candidate.model_input),
                "allowed_evidence_refs": list(
                    row.candidate.allowed_evidence_refs
                ),
                "image_paths": [str(path) for path in row.candidate.image_paths],
                "target_label": row.candidate.target_label,
                "canonical_state": row.candidate.canonical_state,
                "sol_state": row.candidate.sol_state,
                "dispatch_contract": {
                    "schema_version": (
                        "study-intake-dispatch-release-binding-v1"
                    ),
                    **dispatch_rule_binding(
                        release_id=release_id,
                        subject=row.subject,
                        subject_processing_contract_sha256=processing_sha,
                    ),
                    "dispatch_reason": "prebuild_multi_agent_v2_trial",
                },
            }
        )
    task_evidence_root = Path(str(config["_trial_root"])) / "task-inputs"
    task_evidence_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for task in tasks.values():
        (task_evidence_root / f"{task.unit_sha256}.json").write_bytes(
            _canonical(task.as_dict())
        )

    def runner_factory(
        task: FrozenTask, _context: TaskExecutionContext
    ) -> CoreCandidateSubprocessRunner:
        if (
            str(task.frozen_payload["subject"]),
            str(task.frozen_payload["capture_id"]),
        ) not in capture_by_identity:
            raise TrialError("trial_dispatcher_capture_missing")
        return CoreCandidateSubprocessRunner(
            Path(str(config["_trial_config_path"])),
            reason="prebuild_multi_agent_v2_trial",
            command=[
                str(ROOT / "bin/preprocess_task_runner.py")
            ],
            lease_store=dispatcher.lease_store,
        )

    dispatcher = ConcurrentDispatcher(runtime_root, runner_factory)
    observer = _ProviderProcessObserver(
        codex_path=Path(str(config["codex_path"])),
        trial_root=Path(str(config["_trial_root"])),
    )

    def execute(capture: CaptureEnvelope, supplied_runtime: Path) -> Mapping[str, Any]:
        from analysis_package_store import AnalysisPackageStore

        if supplied_runtime.resolve() != runtime_root.resolve():
            raise TrialError("trial_dispatcher_runtime_binding_invalid")
        task_root = runtime_root
        task = tasks[(capture.subject, capture.capture_id)]
        dispatch_result = dispatcher.submit(task).wait()
        if dispatch_result.outcome != "succeeded":
            raise TrialError(
                "trial_dispatcher_task_failed",
                {
                    "subject": capture.subject,
                    "capture_id": capture.capture_id,
                    "error_code": dispatch_result.error_code,
                },
            )
        if not isinstance(dispatch_result.completion, Mapping):
            raise TrialError("trial_dispatcher_completion_missing")
        verified_completion = (
            dispatcher.lease_store.verify_authoritative_completion(
                capture.subject,
                capture.capture_id,
                expected_release_id=release_id,
                expected_unit_sha256=task.unit_sha256,
            )
        )
        if verified_completion.get("completion") != dispatch_result.completion:
            raise TrialError("trial_dispatcher_completion_reopen_mismatch")
        outer_package = verified_completion.get("package")
        expected_inner_binding = (
            outer_package.get("analysis_package_binding")
            if isinstance(outer_package, Mapping) else None
        )
        if (
            not isinstance(outer_package, Mapping)
            or not isinstance(expected_inner_binding, Mapping)
        ):
            raise TrialError("trial_dispatcher_package_binding_mismatch")
        store = AnalysisPackageStore(task_root)
        package = store.reopen_package(str(expected_inner_binding["sha256"]))
        plan_object = store.reopen_analysis_object(
            str(package["plan"]["sha256"])
        )
        read_bundle_object = store.reopen_analysis_object(
            str(package["read_bundle"]["sha256"])
        )
        terra_initial_report = store.reopen_analysis_object(
            str(package["terra_initial"]["sha256"])
        )
        reports: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        terminal_materials: list[dict[str, Any]] = []
        branch_results: dict[str, dict[str, Any]] = {}
        for binding in package.get("luna_outputs") or []:
            output = store.reopen_analysis_object(str(binding["sha256"]))
            branch_result = store.reopen_analysis_object(
                str(binding["branch_result_sha256"])
            )
            branch_id = str(binding["branch_id"])
            branch_results[branch_id] = branch_result
            calls = branch_result.get("calls") or []
            servers = {
                str(call.get("server")) for call in calls
                if isinstance(call, Mapping) and call.get("server")
            }
            kind = str(binding.get("kind") or "")
            execution_artifacts = output.get("execution_artifacts")
            read_session_id = (
                execution_artifacts.get("read_session_id")
                if isinstance(execution_artifacts, Mapping)
                else branch_result.get("read_session_id")
            )
            common = {
                "subject": output.get("subject"),
                "capture_id": output.get("capture_id"),
                "branch_id": branch_id,
                "read_session_id": read_session_id,
                "mcp_server": next(iter(servers)) if len(servers) == 1 else None,
                "collections": sorted(
                    {
                        value
                        for call in calls
                        if isinstance(call, Mapping)
                        for value in _call_collections(call)
                    }
                ),
                "evidence_refs": copy.deepcopy(
                    output.get("evidence_refs") or []
                ),
                "mcp_calls": copy.deepcopy(calls),
                "plan_sha256": output.get("plan_sha256"),
                "read_bundle_sha256": output.get("read_bundle_sha256"),
                "branch_result_sha256": branch_result.get("result_sha256"),
                "branch_result_object_sha256": binding.get(
                    "branch_result_sha256"
                ),
                "formal_write_count": 0,
            }
            if kind == "investigation_report":
                material = {
                    **common,
                    "material_kind": "report",
                    "report_sha256": output.get("report_sha256"),
                }
                reports.append(material)
            elif kind == "diagnostic_record":
                material = {
                    **common,
                    "material_kind": "diagnostic",
                    "diagnostic_sha256": output.get("result_sha256"),
                    "diagnostic_status": output.get("status"),
                    "diagnostic_error_code": output.get("error_code"),
                }
                diagnostics.append(material)
            else:
                raise TrialError("trial_luna_terminal_material_kind_invalid")
            terminal_materials.append(material)
        plan_branch_ids = [
            str(row["branch_id"]) for row in plan_object.get("branches") or []
        ]
        ordered = {
            row["branch_id"]: index
            for index, row in enumerate(plan_object.get("branches") or [])
        }
        reports.sort(key=lambda row: ordered[row["branch_id"]])
        diagnostics.sort(key=lambda row: ordered[row["branch_id"]])
        terminal_materials.sort(key=lambda row: ordered[row["branch_id"]])

        marker = str(capture.producer_receipt.get("prebuild_marker") or "")
        transport_root = (
            task_root / "private/reports/model-mcp-transport/sha256"
        )
        transports = []
        for path in sorted(transport_root.glob("*/*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            transports.append((path, value))
        for material in terminal_materials:
            matches = [
                (path, value)
                for path, value in transports
                if value.get("read_session_id") == material["read_session_id"]
            ]
            proof = (
                _transport_proof(
                    transport=matches[0][1], subject=capture.subject,
                    capture_id=capture.capture_id, marker=marker,
                    read_session_id=material["read_session_id"],
                )
                if len(matches) == 1
                else {}
            )
            material["mcp_server"] = EXPECTED_MCP_SERVERS[capture.subject]
            material["collections"] = copy.deepcopy(
                proof.get("library_collections") or []
            )
            material["capture_marker"] = marker
            material["marker_transport_sha256"] = (
                _file_sha(matches[0][0]) if len(matches) == 1 else None
            )
            material["transport_proof"] = proof
            material["transport_mcp_tool_call_count"] = int(
                proof.get("validated_mcp_tool_call_count") or 0
            )
        process_execution = verified_completion["completion"].get(
            "process_execution"
        )
        if (
            not isinstance(process_execution, Mapping)
            or process_execution.get("canonical_task_runner") is not True
            or not process_execution.get("supervisor_process_identity_sha256")
            or not process_execution.get("supervisor_process_exit_sha256")
            or not isinstance(process_execution.get("supervisor_pid"), int)
        ):
            raise TrialError("trial_supervisor_process_execution_invalid")
        supervisor_pid = int(process_execution["supervisor_pid"])
        observed = [
            row for row in observer.snapshot()
            if row.get("ppid") == supervisor_pid
        ]
        observed_luna = [
            row for row in observed
            if row.get("provider_stage") == "luna_analysis"
        ]
        if len(observed_luna) != 3:
            raise TrialError(
                "trial_provider_process_count_invalid",
                {
                    "subject": capture.subject,
                    "capture_id": capture.capture_id,
                    "observed_luna": str(len(observed_luna)),
                },
            )
        material_by_session = {
            row["read_session_id"]: row for row in terminal_materials
        }
        providers = []
        for row in observed_luna:
            stage = str(row["provider_stage"])
            provider = "luna"
            phase = "luna"
            interval = {
                **copy.deepcopy(row),
                "provider": provider,
                "phase": phase,
                "formal_write_count": 0,
            }
            manifest_path = row.get("read_session_manifest_path")
            if not isinstance(manifest_path, str) or not Path(
                manifest_path
            ).is_file():
                raise TrialError("trial_provider_read_session_observation_missing")
            session_manifest = json.loads(
                Path(manifest_path).read_text(encoding="utf-8")
            )
            session_id = session_manifest.get("read_session_id")
            material = material_by_session.get(session_id)
            if material is None:
                raise TrialError("trial_provider_read_session_binding_invalid")
            interval.update(
                {
                    "branch_id": material["branch_id"],
                    "read_session_id": session_id,
                    "mcp_tool_call_count": material[
                        "transport_mcp_tool_call_count"
                    ],
                }
            )
            providers.append(interval)
        outer_stage_rows = {
            "analysis": "terra_initial",
            "critical_review": "terra_final",
        }
        terra_rows: dict[str, dict[str, Any]] = {}
        for semantic_stage, phase in outer_stage_rows.items():
            provider_stage = f"{capture.subject}_{semantic_stage}"
            closure = process_execution["provider_stages"].get(
                provider_stage
            )
            if not isinstance(closure, Mapping):
                raise TrialError("trial_terra_process_closure_missing")
            identity = json.loads(
                Path(closure["provider_process_identity_path"]).read_text(
                    encoding="utf-8"
                )
            )
            exit_value = json.loads(
                Path(closure["provider_process_exit_path"]).read_text(
                    encoding="utf-8"
                )
            )
            terra_rows[phase] = {
                "provider": "terra", "phase": phase,
                "pid": identity["provider_pid"],
                "ppid": supervisor_pid,
                "started_at": identity["launched_at"],
                "ended_at": exit_value["finished_at"],
                "mcp_tool_call_count": 0,
                "durable_process_identity_sha256": closure[
                    "provider_process_identity_sha256"
                ],
                "durable_process_exit_sha256": closure[
                    "provider_process_exit_sha256"
                ],
                "formal_write_count": 0,
            }
        terra_initial = terra_rows["terra_initial"]
        terra_final_interval = terra_rows["terra_final"]
        luna_by_branch = {
            str(row["branch_id"]): row for row in providers
            if row["phase"] == "luna"
        }
        luna_ordered = [luna_by_branch[branch_id] for branch_id in plan_branch_ids]
        terra_final_report = store.reopen_analysis_object(
            str(package["terra_final"]["sha256"])
        )
        final_bindings = copy.deepcopy(
            terra_final_report["ordered_luna_reports"]
        )
        final_diagnostic_bindings = copy.deepcopy(
            terra_final_report.get("ordered_luna_diagnostics") or []
        )
        package_final_projection = [
            {
                "branch_id": row["branch_id"],
                "report_sha256": row["report_sha256"],
                "report_ref": (
                    "study-intake-luna-investigation-report://sha256/"
                    + row["report_sha256"]
                ),
            }
            for row in package["ordered_luna_reports"]
        ]
        diagnostic_by_branch = {
            row["branch_id"]: row for row in diagnostics
        }
        package_final_diagnostic_projection = [
            {
                "branch_id": row["branch_id"],
                "diagnostic_sha256": row["report_sha256"],
                "diagnostic_ref": (
                    "study-intake-luna-diagnostic-record://sha256/"
                    + row["report_sha256"]
                ),
                "status": diagnostic_by_branch[row["branch_id"]][
                    "diagnostic_status"
                ],
            }
            for row in package.get("ordered_luna_diagnostics") or []
        ]
        if (
            final_bindings != package_final_projection
            or final_diagnostic_bindings
            != package_final_diagnostic_projection
        ):
            raise TrialError("trial_terra_final_report_binding_invalid")
        ordered_branch_results = {
            row["branch_id"]: row["result_sha256"]
            for row in read_bundle_object["ordered_branch_results"]
        }
        if (
            read_bundle_object.get("plan_sha256")
            != plan_object.get("plan_sha256")
            or terra_final_report.get("plan_sha256")
            != plan_object.get("plan_sha256")
            or terra_final_report.get("read_bundle_sha256")
            != read_bundle_object.get("read_bundle_sha256")
            or any(
                material["plan_sha256"] != plan_object["plan_sha256"]
                or (
                    material["material_kind"] == "report"
                    and material["read_bundle_sha256"]
                    != read_bundle_object["read_bundle_sha256"]
                )
                or material["branch_result_sha256"]
                != ordered_branch_results.get(material["branch_id"])
                or branch_results[material["branch_id"]].get("plan_sha256")
                != plan_object["plan_sha256"]
                or (
                    material["material_kind"] == "diagnostic"
                    and material["diagnostic_sha256"]
                    != material["branch_result_sha256"]
                )
                for material in terminal_materials
            )
        ):
            raise TrialError("trial_plan_read_bundle_binding_invalid")
        expected_coverage = [
            {
                "branch_id": branch_id,
                "outcome": (
                    "diagnostic"
                    if branch_id in diagnostic_by_branch
                    else "report"
                ),
            }
            for branch_id in plan_branch_ids
        ]
        if terra_final_report.get("branch_coverage") != expected_coverage:
            raise TrialError("trial_terra_final_report_binding_invalid")
        return {
            "status": "completed",
            "dispatcher_completion": copy.deepcopy(
                dict(verified_completion["completion"])
            ),
            "dispatcher_authoritative_receipt_sha256": (
                verified_completion["completion"].get("receipt_sha256")
            ),
            "provider_intervals": [
                terra_initial, *luna_ordered, terra_final_interval
            ],
            "luna_reports": reports,
            "luna_diagnostics": diagnostics,
            "package": {
                "package_id": package["package_id"],
                "package_sha256": expected_inner_binding["sha256"],
                "ordered_luna_reports": copy.deepcopy(
                    package["ordered_luna_reports"]
                ),
                "ordered_luna_diagnostics": copy.deepcopy(
                    package.get("ordered_luna_diagnostics") or []
                ),
                "plan_branch_ids": plan_branch_ids,
                "terra_final_report_sha256": terra_final_report[
                    "report_sha256"
                ],
                "terra_final_ordered_luna_reports": final_bindings,
                "terra_final_ordered_luna_diagnostics": (
                    final_diagnostic_bindings
                ),
                "terra_final_branch_coverage": copy.deepcopy(
                    terra_final_report.get("branch_coverage") or []
                ),
                "terra_final_branch_assessments": copy.deepcopy(
                    terra_final_report.get("branch_assessments") or []
                ),
                "branch_result_sha256s": {
                    branch_id: _sha(value)
                    for branch_id, value in branch_results.items()
                },
                "plan_sha256": plan_object["plan_sha256"],
                "plan_object_sha256": package["plan"]["sha256"],
                "read_bundle_sha256": read_bundle_object[
                    "read_bundle_sha256"
                ],
                "read_bundle_object_sha256": package["read_bundle"][
                    "sha256"
                ],
                "terra_initial_report_sha256": terra_initial_report[
                    "report_sha256"
                ],
                "formal_write_count": 0,
            },
            "semantic_stage_count": 5,
            "provider_request_count": 2 + sum(
                row["transport_mcp_tool_call_count"] + 1
                for row in terminal_materials
            ),
            "mcp_tool_call_count": sum(
                row["transport_mcp_tool_call_count"]
                for row in terminal_materials
            ),
            "active_processes_after": [],
            "content_quality_warnings": sorted({
                *(str(value) for value in terra_final_report.get("warnings") or []),
                *(
                    str(row.get("diagnostic_error_code"))
                    if row.get("diagnostic_error_code")
                    else f"luna_{row['diagnostic_status']}"
                    for row in diagnostics
                ),
            }),
            "formal_write_count": 0,
        }
    execute.source_binding = lambda: _source_closure_binding(  # type: ignore[attr-defined]
        isolated_roots={
            subject: Path(path)
            for subject, path in dict(config["subject_repo_roots"]).items()
        },
        config=config,
    )
    def cancel_all(reason: str) -> None:
        dispatcher.emergency_cancel(timeout=10, error_code=reason)
        observer.stop()
    execute.cancel_all = cancel_all  # type: ignore[attr-defined]
    def drain() -> bool:
        drained = dispatcher.drain(timeout=30)
        observer.stop()
        return drained and dispatcher.active_count == 0
    execute.drain = drain  # type: ignore[attr-defined]
    return execute


def _load_factory(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TrialError("trial_factory_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _default_real_producer(
    isolated_roots: Mapping[str, Path], trial_root: Path
) -> Sequence[CaptureEnvelope]:
    """Delegate only fixture construction to the real-Producer fixture module.

    The module is part of this trial's tests because it contains sizeable,
    answer-safe synthetic stems.  It invokes the copied Producer source; it
    does not fabricate Candidate objects.  Adapters in that module reopen the
    resulting ledgers and return their real Candidates.
    """

    fixture = _load_factory(
        ROOT / "tests/fixtures/prebuild_multi_agent_v2_producers.py",
        "prebuild_multi_agent_v2_producers",
    )
    rows = fixture.produce_and_scan(
        backend_root=ROOT,
        isolated_roots=dict(isolated_roots),
        trial_root=trial_root,
    )
    return [
        CaptureEnvelope(
            subject=str(row["subject"]),
            capture_id=str(row["capture_id"]),
            candidate=row["candidate"],
            producer_entrypoint=str(row["producer_entrypoint"]),
            producer_receipt=copy.deepcopy(row["producer_receipt"]),
        )
        for row in rows
    ]


def _build_real_config(
    *, trial_root: Path, isolated_roots: Mapping[str, Path], codex_path: Path,
    mcp_python: Path,
) -> dict[str, Any]:
    fixture = _load_factory(
        ROOT / "tests/fixtures/prebuild_multi_agent_v2_producers.py",
        "prebuild_multi_agent_v2_runtime",
    )
    return fixture.build_runtime_config(
        backend_root=ROOT,
        trial_root=trial_root,
        isolated_roots=dict(isolated_roots),
        codex_path=codex_path,
        mcp_python=mcp_python,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-path", type=Path, required=True)
    parser.add_argument("--mcp-python", type=Path, required=True)
    parser.add_argument("--math-root", type=Path, required=True)
    parser.add_argument("--cs408-root", type=Path, required=True)
    parser.add_argument("--english-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=3600.0)
    args = parser.parse_args(argv)
    args.output = args.output.expanduser().resolve()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    for executable in (args.codex_path, args.mcp_python):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise SystemExit(f"executable required: {executable}")
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit(f"output already exists: {args.output}")
    source_roots = {
        "math": args.math_root,
        "cs408": args.cs408_root,
        "english": args.english_root,
    }
    try:
        source_root_binding = _canonical_source_root_binding(
            source_roots, require_exact=True
        )
    except TrialError as exc:
        raise SystemExit(exc.code) from exc
    evidence_root = args.output.with_name(args.output.name + ".evidence")
    if evidence_root.exists() or evidence_root.is_symlink():
        raise SystemExit(f"evidence sibling already exists: {evidence_root}")
    protected_roots = [
        Path.home() / ".codex/study-intake-preprocessor",
        *PRODUCTION_SUBJECT_ROOTS.values(),
        *CANONICAL_SOURCE_ROOTS.values(),
        ROOT,
    ]
    output_target = args.output.absolute()
    evidence_target = evidence_root.absolute()
    if (
        output_target == evidence_target
        or output_target.is_relative_to(evidence_target)
        or evidence_target.is_relative_to(output_target)
    ):
        raise SystemExit("output_and_evidence_must_be_disjoint_siblings")
    for target in (output_target, evidence_target):
        if any(
            target == root.resolve() or target.is_relative_to(root.resolve())
            for root in protected_roots
        ):
            raise SystemExit("output_and_evidence_must_be_outside_production")
    before = production_surface_snapshot()
    if before.get("formal_surface_manifest_status") != "captured":
        raise SystemExit("production_formal_surface_manifest_unavailable")
    exit_code = 0
    evidence_root.mkdir(parents=True, mode=0o700)
    try:
        trial_root = evidence_root.resolve()
        result = run_trial_core(
            source_roots=source_roots,
            trial_root=trial_root,
            producer=_default_real_producer,
            executor_factory=lambda isolated, root, captures: _real_executor_factory(
                _build_real_config(
                    trial_root=root,
                        isolated_roots=isolated,
                        codex_path=args.codex_path.resolve(),
                        mcp_python=args.mcp_python.expanduser().absolute(),
                ),
                captures,
                root / "runtime",
            ),
            source_root_binding=source_root_binding,
            timeout_seconds=args.timeout,
        )
        result = copy.deepcopy(dict(result))
        (trial_root / "immutable-source-binding.json").write_bytes(
            _canonical(result["immutable_source_binding"])
        )
        result["evidence_bundle"] = {
            "schema_version": "study-intake-prebuild-evidence-bundle-v1",
            "root": str(trial_root),
            "tree_sha256_before_report": _tree_fingerprint(trial_root),
            "preserved_on_failure": True,
            "formal_write_count": 0,
        }
    except BaseException as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        result = {
            "schema_version": (
                "study-intake-prebuild-multi-agent-v2-concurrency-trial-v1"
            ),
            "status": "FAIL",
            "technical_gate": "FAIL",
            "technical_error_code": code,
            "technical_diagnostic": copy.deepcopy(
                dict(getattr(exc, "diagnostic", {}) or {})
            ),
            "content_quality_warnings": [],
            "production_surface_before": before,
            "production_surface_after": production_surface_snapshot(),
            "formal_write_count": 0,
        }
        exit_code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
