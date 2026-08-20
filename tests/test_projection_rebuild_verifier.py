from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lib.projection_rebuild_verifier import (
    ProjectionRebuildError,
    render_english_projection_v2,
    verify_cs408_projection_rebuild,
    verify_english_projection_bytes,
    verify_english_projection_rebuild,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_synthetic_cs408_repo(root: Path) -> Path:
    repo = root / "cs408"
    script = repo / "scripts/review_feedback_loop.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        """from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("operation", choices=("reconcile", "audit"))
parser.add_argument("--repo", type=Path, required=True)
args = parser.parse_args()
if args.operation == "reconcile":
    outputs = {
        "wiki/study_vaults/408-full/state/review-loop/state.json": {"state": "synthetic"},
        "wiki/study_vaults/408-full/state/review-loop/runtime.json": {"runtime": "synthetic"},
        "wiki/study_vaults/408-full/state/review-loop/dashboard.json": {"dashboard": "synthetic"},
    }
    for relative, value in outputs.items():
        path = args.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\\n", encoding="utf-8")
    pointer = args.repo / "wiki/study_vaults/408-full/StudyVault/00-Dashboard/当前晨间复盘.md"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("synthetic pointer\\n", encoding="utf-8")
    result = {"status": "PASS", "formal_write_count": 0, "operation": "reconcile"}
else:
    result = {"status": "PASS", "formal_write_count": 0, "operation": "audit"}
print(json.dumps(result, sort_keys=True))
""",
        encoding="utf-8",
    )
    loop = repo / "wiki/study_vaults/408-full/state/review-loop"
    loop.mkdir(parents=True, exist_ok=True)
    (loop / "events.jsonl").write_text(
        json.dumps({"event_id": "SYNTHETIC-408-EVENT-1"}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    session = (
        repo
        / "wiki/study_vaults/408-full/state/morning-review/session-synthetic"
    )
    queue_rel = (
        "wiki/study_vaults/408-full/state/morning-review/"
        "session-synthetic/queue.json"
    )
    pack_rel = (
        "wiki/study_vaults/408-full/state/morning-review/"
        "session-synthetic/pack.json"
    )
    _write_json(
        session / "state.json",
        {"queue_path": queue_rel, "delivery_pack_ref": pack_rel},
    )
    (session / "events.jsonl").write_text(
        json.dumps({"event_id": "SYNTHETIC-MORNING-1"}) + "\n",
        encoding="utf-8",
    )
    _write_json(repo / queue_rel, {"items": []})
    _write_json(repo / pack_rel, {"items": []})
    return repo


def _build_synthetic_english_repo(root: Path) -> Path:
    repo = root / "english"
    package = repo / "english_pipeline"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "events.py").write_text(
        """from __future__ import annotations
import json

def load_events(state_dir):
    rows = []
    for path in sorted((state_dir / "events").rglob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows

def effective_sentence_events(events, *, article_id, study_date):
    return [
        row for row in events
        if row.get("article_id") == article_id and row.get("study_date") == study_date
    ]
""",
        encoding="utf-8",
    )
    (package / "views.py").write_text(
        """from __future__ import annotations

def render_quick_capture(events, *, article_id, study_date):
    event_ids = sorted(
        row["event_id"] for row in events
        if row.get("article_id") == article_id and row.get("study_date") == study_date
    )
    return "# synthetic projection\\n" + "\\n".join(event_ids) + "\\n"
""",
        encoding="utf-8",
    )
    (package / "util.py").write_text(
        """from __future__ import annotations
import hashlib
import json

def object_sha256(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
""",
        encoding="utf-8",
    )
    event_path = repo / "intake/events/2026-08-09/synthetic.json"
    _write_json(
        event_path,
        {
            "event_id": "SYNTHETIC-ENGLISH-EVENT-1",
            "article_id": "RAW-SYNTHETIC-001",
            "study_date": "2026-08-09",
            "content": "synthetic",
        },
    )
    (repo / "intake/migrations/event-v2").mkdir(parents=True, exist_ok=True)
    return repo


class ProjectionRebuildVerifierTests(unittest.TestCase):
    def test_english_projection_v2_binds_high_water_and_rejects_stale_bytes(self) -> None:
        effective = {
            "event_ids": ["EVT-1"],
            "event_count": 1,
            "high_water_sha256": "1" * 64,
        }
        expected = render_english_projection_v2(
            "# candidate\n",
            source_id="RAW-TEST-001",
            study_date="2026-08-09",
            effective=effective,
            raw_high_water="2" * 64,
            migration_high_water="3" * 64,
        )
        current = verify_english_projection_bytes(
            expected,
            expected,
            effective_high_water_sha256="1" * 64,
        )
        stale = verify_english_projection_bytes(
            expected + b"changed\n",
            expected,
            effective_high_water_sha256="1" * 64,
        )
        self.assertEqual(current["status"], "bound_current")
        self.assertTrue(current["metadata_high_water_matches"])
        self.assertEqual(stale["status"], "stale_or_unbound")
        self.assertFalse(stale["byte_equal"])

    def test_synthetic_cs408_inputs_rebuild_twice_and_pass_isolated_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-cs408-projection-") as raw:
            root = Path(raw)
            repo = _build_synthetic_cs408_repo(root)
            receipt = verify_cs408_projection_rebuild(
                repo, root / "artifacts"
            )
        self.assertEqual(receipt["issue_id"], "CS408-AUDIT-004")
        self.assertEqual(receipt["repair_capability_status"], "verified")
        self.assertTrue(receipt["checks"]["two_rebuilds_byte_identical"])
        self.assertTrue(receipt["checks"]["isolated_canonical_inputs_unchanged"])
        self.assertTrue(receipt["checks"]["isolated_canonical_audit_passed"])
        self.assertEqual(
            receipt["canonical_input"]["review_ledger"]["event_count"], 1
        )
        self.assertEqual(receipt["formal_write_count"], 0)
        self.assertEqual(receipt["model_call_count"], 0)

    def test_synthetic_english_effective_events_bind_audited_high_water(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-english-projection-") as raw:
            root = Path(raw)
            repo = _build_synthetic_english_repo(root)
            receipt = verify_english_projection_rebuild(
                repo,
                root / "artifacts",
                source_id="RAW-SYNTHETIC-001",
                study_date="2026-08-09",
            )
        self.assertEqual(receipt["issue_id"], "EN-P0-004")
        self.assertEqual(receipt["repair_capability_status"], "verified")
        self.assertTrue(receipt["checks"]["two_rebuilds_byte_identical"])
        self.assertTrue(receipt["checks"]["stale_fixture_rejected"])
        self.assertEqual(
            receipt["canonical_input"]["effective_events"]["event_count"], 1
        )
        self.assertRegex(
            receipt["canonical_input"]["effective_events"]["high_water_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(receipt["canonical_body"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(receipt["formal_write_count"], 0)
        self.assertEqual(receipt["model_call_count"], 0)

    def test_artifact_root_inside_live_repo_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-english-projection-") as raw:
            repo = _build_synthetic_english_repo(Path(raw))
            with self.assertRaisesRegex(
                ProjectionRebuildError,
                "projection_artifact_root_inside_live_repo",
            ):
                verify_english_projection_rebuild(
                    repo,
                    repo / "tmp" / "forbidden-projection-test",
                    source_id="RAW-SYNTHETIC-001",
                    study_date="2026-08-09",
                )


if __name__ == "__main__":
    unittest.main()
