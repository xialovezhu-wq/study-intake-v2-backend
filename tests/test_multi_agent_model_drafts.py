from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from multi_agent_model_drafts import (  # noqa: E402
    ModelDraftError,
    build_sealed_read_plan_from_draft,
    validate_luna_investigation_draft,
    validate_terra_final_draft,
    validate_terra_initial_draft,
)
from orchestration_plan import validate_read_plan  # noqa: E402


def initial_draft(count: int = 3, *, subject: str = "math") -> dict:
    collection = {
        "math": "knowledge_catalog",
        "cs408": "formal_knowledge_catalog",
        "english": "article_catalog",
    }[subject]
    return {
        "schema_version": "terra_initial_draft_v1",
        "subject": subject,
        "capture_id": "CAP-DRAFT-1",
        "learning_sections": {
            "task_summary": "Investigate the captured learning evidence.",
            "known_facts": ["The capture is immutable."],
            "open_questions": ["Which knowledge boundary explains the gap?"],
        },
        "proposed_branches": [
            {
                "branch_id": f"branch-{index}",
                "purpose": f"investigation-{index}",
                "rationale": f"independent evidence domain {index}",
                "collection_scope": [collection],
                "expected_evidence_kinds": ["record"],
            }
            for index in range(1, count + 1)
        ],
        "warnings": [],
        "formal_write_count": 0,
    }


def runtime_binding(*, subject: str = "math") -> dict:
    return {
        "subject": subject,
        "capture_id": "CAP-DRAFT-1",
        "plan_id": "PLAN-DRAFT-1",
        "frozen_task_sha256": "1" * 64,
        "release_id": "2" * 64,
        "activation_id": "3" * 64,
        "authority_snapshot_sha256": "4" * 64,
        "generation": "draft-generation-v1",
        "orchestrate_skill": {
            "id": "multi-agent-read-orchestrate",
            "version": "1.0.0",
            "sha256": "5" * 64,
        },
        "terra_agent_contract_sha256": "6" * 64,
        "created_at": "2026-08-24T00:00:00+00:00",
        "allowed_task_artifact_ids": ["artifact-dialogue"],
        "allowed_mcp_tools": ["get_task_context", "read_task_artifact"],
        "query_constraints": {"maximum_age_days": 30},
        "maximum_calls": 6,
        "maximum_records": 100,
        "maximum_bytes": 65536,
        "completion_requirements": ["report-complete"],
        "failure_policy": "fail_closed",
    }


def luna_draft(branch_id: str = "branch-1") -> dict:
    return {
        "schema_version": "luna_investigation_draft_v1",
        "subject": "math",
        "capture_id": "CAP-DRAFT-1",
        "branch_id": branch_id,
        "summary": "Evidence indicates a condition boundary.",
        "findings": ["The condition was not checked."],
        "conflicts": [],
        "missing_evidence": [],
        "confidence": "high",
        "evidence_refs": ["mcp-item:math:item-1"],
        "formal_write_count": 0,
    }


def final_draft(ids: list[str]) -> dict:
    return {
        "schema_version": "terra_final_draft_v1",
        "subject": "math",
        "capture_id": "CAP-DRAFT-1",
        "branch_assessments": [
            {
                "branch_id": branch_id,
                "input_kind": "diagnostic" if branch_id == "branch-2" else "report",
                "disposition": (
                    "request_more_evidence"
                    if branch_id == "branch-2"
                    else "adopt"
                ),
                "assessment": f"Assessment for {branch_id}",
                "evidence_refs": [],
            }
            for branch_id in ids
        ],
        "subject_sections": {
            "learning_summary": "The learning gap is bounded.",
            "cross_branch_synthesis": "The independent evidence converges.",
            "recommended_next_step": "Verify the condition before applying the method.",
        },
        "proposals": ["Preserve as a proposal for Sol review."],
        "conflicts": [],
        "gaps": [],
        "checklist": ["All presented branches assessed."],
        "warnings": [],
        "formal_write_count": 0,
    }


class MultiAgentModelDraftTests(unittest.TestCase):
    def test_initial_accepts_exactly_three_or_four_unique_branches(self) -> None:
        for count in (3, 4):
            checked = validate_terra_initial_draft(
                initial_draft(count), subject="math", capture_id="CAP-DRAFT-1"
            )
            self.assertEqual(len(checked["proposed_branches"]), count)
        for count in (2, 5):
            with self.subTest(count=count), self.assertRaisesRegex(
                ModelDraftError, "terra_initial_branch_count_invalid"
            ):
                validate_terra_initial_draft(
                    initial_draft(count), subject="math", capture_id="CAP-DRAFT-1"
                )
        duplicate = initial_draft()
        duplicate["proposed_branches"][2]["branch_id"] = "branch-1"
        with self.assertRaisesRegex(ModelDraftError, "terra_initial_branch_id_invalid"):
            validate_terra_initial_draft(
                duplicate, subject="math", capture_id="CAP-DRAFT-1"
            )

    def test_initial_rejects_empty_learning_and_subject_collection_drift(self) -> None:
        empty = initial_draft()
        empty["learning_sections"]["known_facts"] = []
        with self.assertRaisesRegex(
            ModelDraftError, "terra_initial_learning_sections_invalid"
        ):
            validate_terra_initial_draft(
                empty, subject="math", capture_id="CAP-DRAFT-1"
            )
        stale = initial_draft()
        stale["proposed_branches"][0]["collection_scope"] = ["relationships"]
        with self.assertRaisesRegex(
            ModelDraftError, "terra_initial_collection_forbidden"
        ):
            validate_terra_initial_draft(
                stale, subject="math", capture_id="CAP-DRAFT-1"
            )

    def test_host_injects_policy_and_seals_existing_read_plan(self) -> None:
        sealed = build_sealed_read_plan_from_draft(
            initial_draft(4), runtime_binding=runtime_binding()
        )
        validate_read_plan(sealed)
        self.assertRegex(sealed["plan_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(sealed["branches"]), 4)
        for row in sealed["branches"]:
            self.assertTrue(row["required"])
            self.assertEqual(row["depends_on"], [])
            self.assertEqual(row["allowed_task_artifact_ids"], ["artifact-dialogue"])
            self.assertEqual(row["allowed_mcp_tools"], ["get_task_context", "read_task_artifact"])
            self.assertEqual(row["maximum_calls"], 6)
            self.assertEqual(row["failure_policy"], "fail_closed")
        forbidden_tool = runtime_binding()
        forbidden_tool["allowed_mcp_tools"] = ["write_records"]
        with self.assertRaisesRegex(ModelDraftError, "host_runtime_tools_forbidden"):
            build_sealed_read_plan_from_draft(
                initial_draft(), runtime_binding=forbidden_tool
            )
        optional = runtime_binding()
        optional["failure_policy"] = "optional_missing"
        with self.assertRaisesRegex(
            ModelDraftError, "host_runtime_failure_policy_invalid"
        ):
            build_sealed_read_plan_from_draft(initial_draft(), runtime_binding=optional)

    def test_luna_draft_rejects_all_binding_and_shape_tamper(self) -> None:
        validate_luna_investigation_draft(
            luna_draft(), subject="math", capture_id="CAP-DRAFT-1",
            branch_id="branch-1"
        )
        mutations = []
        for key, changed in (
            ("subject", "english"),
            ("capture_id", "CAP-OTHER"),
            ("branch_id", "branch-2"),
            ("formal_write_count", 1),
        ):
            value = luna_draft()
            value[key] = changed
            mutations.append(value)
        extra = luna_draft()
        extra["unexpected"] = True
        mutations.append(extra)
        for value in mutations:
            with self.assertRaises(ModelDraftError):
                validate_luna_investigation_draft(
                    value, subject="math", capture_id="CAP-DRAFT-1",
                    branch_id="branch-1"
                )

    def test_final_requires_exact_presented_membership_and_order(self) -> None:
        ids = ["branch-1", "branch-2", "branch-3", "branch-4"]
        presented = [
            {
                "branch_id": branch_id,
                "input_kind": "diagnostic" if branch_id == "branch-2" else "report",
            }
            for branch_id in ids
        ]
        validate_terra_final_draft(
            final_draft(ids), subject="math", capture_id="CAP-DRAFT-1",
            presented_branches=presented
        )
        for changed in (
            ids[:-1],
            [*ids, "branch-5"],
            list(reversed(ids)),
            ["branch-1", "branch-1", "branch-3", "branch-4"],
        ):
            with self.subTest(changed=changed), self.assertRaises(ModelDraftError):
                validate_terra_final_draft(
                    final_draft(changed), subject="math",
                    capture_id="CAP-DRAFT-1", presented_branches=presented
                )
        kind_drift = final_draft(ids)
        kind_drift["branch_assessments"][1]["input_kind"] = "report"
        with self.assertRaisesRegex(
            ModelDraftError, "terra_final_branch_membership_invalid"
        ):
            validate_terra_final_draft(
                kind_drift, subject="math", capture_id="CAP-DRAFT-1",
                presented_branches=presented
            )
        drift = final_draft(ids)
        drift["subject"] = "english"
        with self.assertRaisesRegex(ModelDraftError, "model_draft_binding_invalid"):
            validate_terra_final_draft(
                drift, subject="math", capture_id="CAP-DRAFT-1",
                presented_branches=presented
            )

    def test_schemas_are_strict_draft_202012_and_have_no_self_hashes(self) -> None:
        for name in (
            "terra-initial-draft-v1.json",
            "luna-investigation-draft-v1.json",
            "terra-final-draft-v1.json",
        ):
            schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual(
                schema["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("sha256", json.dumps(schema))


if __name__ == "__main__":
    unittest.main()
