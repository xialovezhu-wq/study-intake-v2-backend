from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _write_json(path: Path, value: Any, *, read_only: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    if read_only:
        path.chmod(0o444)
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SyntheticZeroModelGoldenFixture:
    root: Path
    trusted_math_root: Path
    spec_path: Path
    business_manifest_path: Path
    business_manifest_sha256: str
    superseded_manifest_paths: tuple[Path, ...]
    negative_manifest_path: Path
    live_preflight: dict[str, Any]
    superseded_preflight: dict[str, Any]
    negative_preflight: dict[str, Any]


def build_synthetic_zero_model_golden_fixture(
    base: Path,
) -> SyntheticZeroModelGoldenFixture:
    root = base.resolve() / "synthetic-zero-model-golden"
    trusted_math_root = root / "math-source"
    trusted_math_root.mkdir(parents=True, exist_ok=True)
    release_id = "d" * 64

    def source_bundle(name: str, roles: tuple[str, ...]) -> dict[str, str]:
        bundle = trusted_math_root / name
        bundle.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for index, role in enumerate(roles, start=1):
            suffix = ".md" if role == "solution_text" else ".png"
            artifact = bundle / f"{role}-{index}{suffix}"
            artifact.write_text(f"synthetic {role}\n", encoding="utf-8")
            artifacts.append(
                {
                    "role": role,
                    "path": str(artifact.relative_to(trusted_math_root)),
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                }
            )
        manifest = bundle / "manifest.json"
        digest = _write_json(manifest, {"artifacts": artifacts})
        return {
            "manifest_path": str(manifest.relative_to(trusted_math_root)),
            "manifest_hash": digest,
        }

    def external_capture(
        name: str,
        roles: tuple[str, ...],
        *,
        user_answer: str = "synthetic user answer",
    ) -> tuple[str, str]:
        wrapper = {
            "schema_version": "study-intake-controlled-replay-math-capture-v1",
            "release_id": release_id,
            "formal_write_count": 0,
            "capture": {
                "event_id": f"SYNTHETIC-{name.upper()}",
                "capture_schema_version": "math-fast-intake-capture-v2",
                "formal_id": None,
                "source_bundle": source_bundle(name, roles),
                "episode_evidence": {
                    "solution_text": "inline text is not artifact evidence",
                    "user_answer_text": user_answer,
                },
            },
        }
        staging = root / "external" / f"{name}.json"
        digest = _write_json(staging, wrapper)
        final = staging.with_name(f"{digest}.json")
        staging.replace(final)
        final.chmod(0o444)
        return str(final.resolve()), digest

    complete_path, complete_sha = external_capture(
        "complete", ("question", "solution_text")
    )
    entries = [
        {
            "subject": "english",
            "capture_id": "SYNTHETIC-EN-A",
            "sample_role": "english-q37-a",
            "expected_input_fingerprint": "1" * 64,
        },
        {
            "subject": "english",
            "capture_id": "SYNTHETIC-EN-C",
            "sample_role": "english-q37-c",
            "expected_input_fingerprint": "1" * 64,
        },
        {
            "subject": "english",
            "capture_id": "SYNTHETIC-EN-D",
            "sample_role": "english-q37-d",
            "expected_input_fingerprint": "1" * 64,
        },
        {
            "subject": "cs408",
            "capture_id": "SYNTHETIC-CS-DS",
            "sample_role": "DS_2023_002",
            "expected_input_fingerprint": "2" * 64,
        },
        {
            "subject": "cs408",
            "capture_id": "SYNTHETIC-CS-FILE",
            "sample_role": "FILE_PROTECTION",
            "expected_input_fingerprint": "3" * 64,
        },
        {
            "subject": "cs408",
            "capture_id": "SYNTHETIC-CS-OS",
            "sample_role": "OS_2009_003",
            "expected_input_fingerprint": "4" * 64,
        },
        {
            "subject": "cs408",
            "capture_id": "SYNTHETIC-CS-FREE",
            "sample_role": "FREE_SPACE",
            "expected_input_fingerprint": "5" * 64,
        },
        {
            "subject": "math",
            "capture_id": "SYNTHETIC-MATH-111",
            "sample_role": "GS-111",
            "expected_input_fingerprint": "6" * 64,
        },
        {
            "subject": "math",
            "capture_id": "SYNTHETIC-MATH-240",
            "sample_role": "GS-240",
            "expected_input_fingerprint": "7" * 64,
        },
        {
            "subject": "math",
            "capture_id": "SYNTHETIC-MATH-NEW",
            "sample_role": "complete-new-intake",
            "expected_input_fingerprint": "8" * 64,
            "external_capture_manifest_path": complete_path,
            "external_capture_manifest_sha256": complete_sha,
        },
    ]

    preflight = []
    external_cases = {
        "missing_question_image": external_capture(
            "missing-question", ("solution_text",)
        ),
        "missing_solution_evidence": external_capture(
            "missing-solution", ("question",)
        ),
        "solution_image_only": external_capture(
            "solution-image", ("question", "solution")
        ),
        "missing_user_answer": external_capture(
            "missing-user", ("question", "solution_text"), user_answer=""
        ),
    }
    for role, (path, digest) in external_cases.items():
        preflight.append(
            {
                "sample_role": role,
                "fixture_kind": "external_math_capture",
                "external_capture_manifest_path": path,
                "external_capture_manifest_sha256": digest,
                "expected_outcome": (
                    "eligible" if role == "solution_image_only" else "rejected"
                ),
                "expected_error_code": (
                    None
                    if role == "solution_image_only"
                    else "math_new_source_evidence_incomplete"
                ),
            }
        )

    business_manifest_path = root / "business" / "manifest.json"
    business_manifest_sha256 = _write_json(
        business_manifest_path,
        {
            "schema_version": "synthetic-math-business-binding-v1",
            "model_call_count": 0,
            "formal_write_count": 0,
        },
    )
    preflight.append(
        {
            "sample_role": "solution_text_only",
            "fixture_kind": "math_live_business_task",
            "business_manifest_path": str(business_manifest_path.resolve()),
            "business_manifest_sha256": business_manifest_sha256,
            "capture_id": "LUNA-MATH-20260809-001",
            "expected_outcome": "eligible",
            "expected_error_code": None,
        }
    )
    spec_path = root / "controlled-replay-spec.json"
    _write_json(
        spec_path,
        {
            "schema_version": "study-intake-controlled-replay-spec-v1",
            "release_id": release_id,
            "entries": entries,
            "preflight_cases": preflight,
            "formal_write_count": 0,
        },
    )

    tasks = []
    task_rows = (
        (
            "LUNA-MATH-20260809-001",
            "MATH-LUNA-BIZ-20260809-001",
            "GS-109",
            "existing_formal_card_review",
            "formal_card:GS-109",
            "independent_correct_no_false_wrong_card",
            ["solution_text.md"],
        ),
        (
            "LUNA-MATH-20260809-002",
            "MATH-LUNA-BIZ-20260809-002",
            "GS-507",
            "existing_formal_card_review",
            "formal_card:GS-507",
            "wrong_then_corrected_weakness_proposal",
            ["solution_text.md"],
        ),
        (
            "LUNA-MATH-20260809-003",
            "MATH-LUNA-BIZ-20260809-003",
            None,
            "new_source_learning_episode",
            "question-bank-id:170710",
            "wrong_then_corrected_multistage_method_proposal",
            ["solution_01.png", "solution_text.md"],
        ),
    )
    for index, (row, fingerprint_token) in enumerate(
        zip(task_rows, ("9", "a", "b"), strict=True), start=1
    ):
        (
            capture_id,
            business_task_id,
            formal_id,
            source_route,
            source_locator,
            task_kind,
            satisfied_by,
        ) = row
        tasks.append(
            {
                "capture_id": capture_id,
                "business_task_id": business_task_id,
                "formal_id": formal_id,
                "source_route": source_route,
                "source_locator": source_locator,
                "task_kind": task_kind,
                "content_fingerprint": fingerprint_token * 64,
                "manifest_sha256": str(index) * 64,
                "solution_evidence": {
                    "accepted_policy": "solution_text_or_solution_image",
                    "satisfied_by": satisfied_by,
                    "solution_text_present": True,
                    "solution_image_present": "solution_01.png" in satisfied_by,
                    "source_sha256": str(index + 3) * 64,
                    "source_verified": True,
                },
                "reasoning_boundary": {
                    "status": "synthetic_observed_evidence_only"
                },
                "luna_eligible": True,
                "proposal_only": True,
            }
        )
    live_preflight = {
        "status": "passed_real_luna_business_preflight",
        "authority_schema_version": "math-luna-real-business-samples-v1",
        "batch_id": "SYNTHETIC-MATH-BATCH",
        "batch_manifest_path": str(business_manifest_path.resolve()),
        "batch_manifest_sha256": business_manifest_sha256,
        "fixture_tree_sha256": "f" * 64,
        "solution_evidence_policy": "solution_text_or_solution_image",
        "task_count": 3,
        "distinct_capture_count": 3,
        "tasks": tasks,
        "fixture_write_count": 0,
        "model_call_count": 0,
        "formal_write_count": 0,
    }

    superseded_manifest_paths = tuple(
        root / "superseded" / f"manifest-{index}.json" for index in (1, 2)
    )
    superseded_rows = []
    for path in superseded_manifest_paths:
        digest = _write_json(path, {"schema_version": "synthetic-superseded-v1"})
        superseded_rows.append(
            {
                "manifest_path": str(path.resolve()),
                "manifest_sha256": digest,
                "expected_error_code": "math_live_manifest_superseded",
                "status": "passed_expected_reject",
                "model_call_count": 0,
                "formal_write_count": 0,
            }
        )
    superseded_preflight = {
        "status": "passed_expected_reject",
        "manifest_count": 2,
        "manifests": superseded_rows,
        "model_call_count": 0,
        "formal_write_count": 0,
    }

    negative_manifest_path = root / "negative" / "manifest.json"
    negative_manifest_sha256 = _write_json(
        negative_manifest_path, {"schema_version": "synthetic-negative-v1"}
    )
    negative_samples = [
        {
            "staging_id": f"SYNTHETIC-NEGATIVE-{index}",
            "preflight_status": "failed_closed",
            "luna_eligible": False,
            "model_call_count": 0,
            "formal_write_count": 0,
        }
        for index in (1, 2)
    ]
    negative_preflight = {
        "status": "passed_expected_fail_closed",
        "manifest_path": str(negative_manifest_path.resolve()),
        "manifest_sha256": negative_manifest_sha256,
        "sample_count": 2,
        "distinct_capture_count": 2,
        "samples": negative_samples,
        "fixture_write_count": 0,
        "model_call_count": 0,
        "formal_write_count": 0,
    }
    return SyntheticZeroModelGoldenFixture(
        root=root,
        trusted_math_root=trusted_math_root,
        spec_path=spec_path,
        business_manifest_path=business_manifest_path,
        business_manifest_sha256=business_manifest_sha256,
        superseded_manifest_paths=superseded_manifest_paths,
        negative_manifest_path=negative_manifest_path,
        live_preflight=live_preflight,
        superseded_preflight=superseded_preflight,
        negative_preflight=negative_preflight,
    )
