import json
import re
import unittest
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2]
DAILY_ROOT = SKILLS_ROOT / "kaoyan-english-daily-intake-curation"


class EnglishPipelineSkillContracts(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (SKILLS_ROOT / relative_path).read_text(encoding="utf-8")

    def test_exact_trigger_is_one_shot_and_not_scheduled(self) -> None:
        skill = self.read("kaoyan-english-daily-intake-curation/SKILL.md")
        self.assertIn("开始 YYYY-MM-DD 英语正式入库", skill)
        self.assertIn("full-string", skill)
        self.assertIn("not a timer, recurring schedule or standing permission", skill)
        pattern = re.compile(r"^开始 \d{4}-\d{2}-\d{2} 英语正式入库$")
        self.assertIsNotNone(pattern.fullmatch("开始 2026-08-05 英语正式入库"))
        self.assertIsNone(pattern.fullmatch("今晚开始英语正式入库"))
        self.assertIsNone(pattern.fullmatch("开始 2026-08-05 英语正式入库，以后每天自动执行"))

    def test_daytime_capture_and_article_completion_remain_zero_write(self) -> None:
        intensive = self.read("kaoyan-english-intensive-reading/SKILL.md")
        capture = self.read("kaoyan-english-intensive-reading/references/quick-capture-contract.md")
        export = self.read("kaoyan-english-vocab-export/SKILL.md")
        completion = self.read("kaoyan-english-vocab-export/references/article-completion-contract.md")
        self.assertIn("english_learning_pipeline.py capture", intensive)
        self.assertIn("formal_write_count=0", intensive)
        self.assertIn("formal_writeback=none", capture)
        self.assertIn("--article-sha256 <article-source-hash-64-hex-without-prefix>", capture)
        self.assertIn("--sentence-sha256 <sentence-hash-64-hex-without-prefix>", capture)
        self.assertIn("<sentence-hash-64-hex-without-prefix>", capture)
        self.assertIn("--supersedes <event_id>", capture)
        self.assertNotIn("--source-sha256", capture)
        self.assertNotIn("--article-id <source_id>", capture)
        self.assertIn("capture_saved_but_projection_failed", capture)
        self.assertIn("zero-candidate event", capture)
        self.assertIn("independent_correct_use", capture)
        self.assertIn("english_learning_pipeline.py complete-article", export)
        self.assertIn("Do not invoke, poll or wait for Luna", export)
        self.assertIn("formal write", completion.lower())
        self.assertIn("--source-id <source_id>", completion)
        self.assertNotIn("--article-id <source_id>", completion)

    def test_nightly_path_is_manifest_bound_and_writer_only(self) -> None:
        skill = self.read("kaoyan-english-daily-intake-curation/SKILL.md")
        workflow = self.read("kaoyan-english-daily-intake-curation/references/nightly-curation-contract.md")
        actions = self.read("kaoyan-english-daily-intake-curation/references/typed-actions-contract.md")
        self.assertIn("freeze-nightly", workflow)
        self.assertIn("--dry-run", workflow)
        self.assertIn("--apply --authorization <batch_id>", workflow)
        self.assertIn("/current/bin/preprocess_worker.py", workflow)
        self.assertIn("/current/config.json run-once --subject english --date <YYYY-MM-DD>", workflow)
        self.assertIn("exactly once", workflow)
        self.assertIn("needs_preprocess", workflow)
        self.assertIn("requested_unverified", workflow)
        self.assertIn("mismatch", workflow)
        self.assertIn("runtime_attestation", workflow)
        self.assertIn("stdout-only", workflow)
        self.assertIn("intake/receipts/nightly/YYYY-MM-DD/", workflow)
        self.assertIn("without a second confirmation", skill)
        self.assertIn("PARTIAL", skill)
        self.assertIn("formal_writeback=none", actions)
        self.assertNotIn("typed_actions_only", actions)
        for action_type in (
            "master_bank_insert",
            "master_bank_update",
            "mastered_insert",
            "sentence_pattern_append",
            "sentence_pattern_merge",
        ):
            self.assertIn(action_type, actions)
        self.assertIn("runtime_identity_summary", workflow)
        self.assertIn("english_freeze_receipt_v1", workflow)
        self.assertIn("DRY_RUN_PARTIAL", workflow)
        self.assertNotIn("apply_patch", skill)


class EvalShape(unittest.TestCase):
    def test_evals_follow_skill_creator_schema(self) -> None:
        payload = json.loads((DAILY_ROOT / "evals/evals.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["skill_name"], "kaoyan-english-daily-intake-curation")
        self.assertEqual(len(payload["evals"]), 3)
        for case in payload["evals"]:
            self.assertIsInstance(case["id"], int)
            self.assertTrue(case["prompt"])
            self.assertTrue(case["expected_output"])
            self.assertGreaterEqual(len(case["expectations"]), 3)


if __name__ == "__main__":
    unittest.main()
