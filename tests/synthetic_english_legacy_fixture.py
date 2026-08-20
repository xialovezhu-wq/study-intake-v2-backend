from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_file_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


@dataclass(frozen=True)
class SyntheticEnglishLegacyFixture:
    root: Path
    master_bank_path: Path
    pattern_path: Path
    article_path: Path
    mastered_items_path: Path
    relation_rebuild_script: Path
    projection_side_effect_paths: tuple[tuple[str, Path], ...]
    rollout_path: Path
    rollout_sha256: str
    rollout_window_sha256: str
    rollout_window_event_sha256s: tuple[str, ...]
    master_ids: tuple[str, ...]
    pattern_ids: tuple[str, ...]
    review_pattern_ids: tuple[str, ...]
    article_record_id: str
    requirements: dict[str, Any]

    @property
    def allowed_authority_paths(self) -> dict[str, str]:
        return {
            str(self.master_bank_path.resolve()): "master_bank_row",
            str(self.pattern_path.resolve()): "sentence_pattern_card",
            str(self.article_path.resolve()): "article_learning_page",
            str(self.mastered_items_path.resolve()): "mastered_items_boundary",
        }


def build_synthetic_english_legacy_fixture(
    root: Path,
) -> SyntheticEnglishLegacyFixture:
    """Create isolated 88 + 9 + 1 legacy evidence for contract tests only."""

    root = root.resolve() / "synthetic-english-legacy"
    authority_root = root / "authority"
    master_bank_path = authority_root / "master_bank.csv"
    pattern_path = authority_root / "sentence_patterns.md"
    article_path = authority_root / "articles" / "synthetic-article.md"
    mastered_items_path = authority_root / "mastered_items.csv"
    for path in (
        master_bank_path,
        pattern_path,
        article_path,
        mastered_items_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)

    master_ids = tuple(f"MB-{index:03d}" for index in range(1, 89))
    pattern_ids = tuple(
        [f"SP-{index:03d}" for index in range(1, 9)] + ["SP-022"]
    )
    review_pattern_ids = pattern_ids[:-1]
    master_bank_path.write_text(
        "id,last_seen\n"
        + "".join(f"{record_id},2026-08-06\n" for record_id in master_ids),
        encoding="utf-8",
    )
    pattern_path.write_text(
        "".join(
            f"## {record_id}｜synthetic 2026-08-06\n"
            f"synthetic fixture for {record_id}\n\n"
            for record_id in pattern_ids
        ),
        encoding="utf-8",
    )
    article_path.write_text("synthetic article authority\n", encoding="utf-8")
    mastered_items_path.write_text("id\n", encoding="utf-8")

    derived_root = root / "derived"
    relation_rebuild_script = root / "scripts" / "rebuild_relations.py"
    relation_rebuild_script.parent.mkdir(parents=True, exist_ok=True)
    relation_rebuild_script.write_text(
        "raise SystemExit('synthetic fixture: execution forbidden')\n",
        encoding="utf-8",
    )
    projection_names = (
        "relation_nodes",
        "relation_edges",
        "relation_lookup",
        "relation_summary",
        "relation_manifest",
        "relation_wiki_projection",
    )
    projection_side_effect_paths: list[tuple[str, Path]] = []
    for name in projection_names:
        path = derived_root / f"{name}.synthetic"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"synthetic {name}\n", encoding="utf-8")
        projection_side_effect_paths.append((name, path))

    master_diff = "\n".join(
        ["--- a/master_bank.csv", "+++ b/master_bank.csv", "@@ -1,0 +2,88 @@"]
        + [f"+{record_id},2026-08-06" for record_id in master_ids]
    )
    pattern_lines = [
        "--- a/sentence_patterns.md",
        "+++ b/sentence_patterns.md",
        "@@ -1,0 +1,30 @@",
    ]
    for record_id in review_pattern_ids:
        pattern_lines.extend(
            [
                f"+## {record_id}｜synthetic 2026-08-06",
                f"+synthetic fixture for {record_id}",
                "+",
            ]
        )
    pattern_lines.extend(
        [
            "-## SP-022｜synthetic 2026-08-06",
            "-synthetic fixture for SP-022",
            "-",
            "+## SP-022｜synthetic 2026-08-06",
            "+synthetic fixture for SP-022",
            "+",
        ]
    )
    pattern_diff = "\n".join(pattern_lines)
    article_diff = "\n".join(
        [
            "--- a/synthetic-article.md",
            "+++ b/synthetic-article.md",
            "@@ -1 +1 @@",
            "-prior synthetic article authority",
            "+synthetic article authority",
        ]
    )

    changes = [
        {str(master_bank_path.resolve()): {"unified_diff": master_diff}},
        {str(pattern_path.resolve()): {"unified_diff": pattern_diff}},
    ] + [
        {str(article_path.resolve()): {"unified_diff": article_diff}}
        for _ in range(9)
    ]
    raw_lines: list[bytes] = []
    for index, change_set in enumerate(changes, start=1):
        value = {
            "timestamp": f"2026-08-06T12:{11 + (index - 1) // 60:02d}:"
            f"{index % 60:02d}Z",
            "type": "event_msg",
            "payload": {
                "type": "patch_apply_end",
                "success": True,
                "call_id": f"synthetic-patch-{index:02d}",
                "changes": change_set,
            },
        }
        raw_lines.append(_json_file_bytes(value))
    rollout_path = root / "rollout" / "synthetic-rollout.jsonl"
    rollout_path.parent.mkdir(parents=True, exist_ok=True)
    rollout_bytes = b"".join(raw_lines)
    rollout_path.write_bytes(rollout_bytes)

    master_id_list = sorted(master_ids)
    review_pattern_id_list = sorted(review_pattern_ids)
    requirements = {
        "schema_version": (
            "study-intake-english-legacy-disposition-requirements-v1"
        ),
        "issue_id": "EN-P0-006",
        "originating_thread_id": "synthetic-english-legacy-thread",
        "inventory_status": "evidence_incomplete",
        "identified_target_count": 97,
        "unidentified_target_count_lower_bound": 1,
        "disposition_receipt_count": 0,
        "allowed_dispositions": [
            "legacy_attestation",
            "deterministic_recuration",
            "rollback",
        ],
        "target_groups": [
            {
                "target_kind": "master_bank_row",
                "authority_path": str(master_bank_path.resolve()),
                "current_file_sha256": _sha256(master_bank_path),
                "observed_target_count": len(master_id_list),
                "observed_target_id_set_sha256": hashlib.sha256(
                    _json_file_bytes(master_id_list)
                ).hexdigest(),
            },
            {
                "target_kind": "sentence_pattern_card",
                "authority_path": str(pattern_path.resolve()),
                "current_file_sha256": _sha256(pattern_path),
                "observed_target_count": len(review_pattern_id_list),
                "observed_target_ids": review_pattern_id_list,
                "observed_target_id_set_sha256": hashlib.sha256(
                    _json_file_bytes(review_pattern_id_list)
                ).hexdigest(),
            },
            {
                "target_kind": "article_learning_page",
                "authority_path": str(article_path.resolve()),
                "current_file_sha256": _sha256(article_path),
                "observed_target_count": 1,
            },
        ],
        "mastered_items_state": {
            "authority_path": str(mastered_items_path.resolve()),
            "current_sha256": _sha256(mastered_items_path),
            "row_count": 0,
            "status": "unchanged_no_rows",
        },
        "closure_rule": "synthetic explicit per-target authority required",
        "model_call_count": 0,
        "formal_write_count": 0,
    }
    return SyntheticEnglishLegacyFixture(
        root=root,
        master_bank_path=master_bank_path,
        pattern_path=pattern_path,
        article_path=article_path,
        mastered_items_path=mastered_items_path,
        relation_rebuild_script=relation_rebuild_script,
        projection_side_effect_paths=tuple(projection_side_effect_paths),
        rollout_path=rollout_path,
        rollout_sha256=hashlib.sha256(rollout_bytes).hexdigest(),
        rollout_window_sha256=hashlib.sha256(rollout_bytes).hexdigest(),
        rollout_window_event_sha256s=tuple(
            hashlib.sha256(line).hexdigest() for line in raw_lines
        ),
        master_ids=master_ids,
        pattern_ids=pattern_ids,
        review_pattern_ids=review_pattern_ids,
        article_record_id="articles/synthetic-article.md",
        requirements=requirements,
    )
