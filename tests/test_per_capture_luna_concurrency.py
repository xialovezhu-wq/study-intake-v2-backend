from __future__ import annotations

import copy
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from orchestration_plan import seal_read_plan
from read_branch import SubprocessBranchWorker
from read_fanout import ReadFanoutScheduler


def _branch(index: int) -> dict:
    return {
        "branch_id": f"branch-{index:02d}",
        "purpose": f"read-domain-{index:02d}",
        "rationale": f"independent-domain-{index:02d}",
        "required": True,
        "depends_on": [],
        "allowed_task_artifact_ids": ["artifact-dialogue"],
        "allowed_mcp_tools": [
            "get_task_context",
            "read_task_artifact",
            "get_records",
        ],
        "collection_scope": [f"collection-{index:02d}"],
        "query_constraints": {"delay_ms": 120},
        "maximum_calls": 8,
        "maximum_records": 100,
        "maximum_bytes": 65536,
        "completion_requirements": [
            "task-context",
            "artifact-complete",
            "library-evidence",
        ],
        "failure_policy": "fail_closed",
        "expected_evidence_kinds": ["record"],
    }


def _plan(subject: str, index: int) -> dict:
    return seal_read_plan(
        {
            "schema_version": "orchestration_read_plan_v1",
            "plan_id": f"PLAN-{subject}-{index}",
            "subject": subject,
            "capture_id": f"CAP-{subject}-{index}",
            "frozen_task_sha256": f"{index + 1:x}" * 64,
            "release_id": "a" * 64,
            "activation_id": "b" * 64,
            "authority_snapshot_sha256": "c" * 64,
            "generation": f"generation-{subject}-{index}",
            "orchestrate_skill": {
                "id": "multi-agent-read-orchestrate",
                "version": "1.0.0",
                "sha256": "d" * 64,
            },
            "terra_agent_contract_sha256": "e" * 64,
            "created_at": "2026-08-25T00:00:00+00:00",
            "branches": [_branch(branch_index) for branch_index in range(1, 5)],
            "formal_write_count": 0,
        }
    )


class _ObservedWorker:
    def __init__(self, delegate: SubprocessBranchWorker) -> None:
        self.delegate = delegate
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0

    def run(self, request, *, wave_index, cancellation):
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            return self.delegate.run(
                copy.deepcopy(request),
                wave_index=wave_index,
                cancellation=cancellation,
            )
        finally:
            with self.lock:
                self.active -= 1


class PerCaptureLunaConcurrencyTests(unittest.TestCase):
    def test_three_subjects_can_exceed_four_luna_globally(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "fake_multi_agent_branch.py"
        delegate = SubprocessBranchWorker(
            execution_config={
                "execution_mode": "fixture",
                "fixture_execution": {
                    "allowed_executable_roots": [str(fixture.parent)]
                },
            },
            command=[sys.executable, str(fixture)],
        )
        observed = _ObservedWorker(delegate)
        plans = [
            _plan(subject, index)
            for index, subject in enumerate(("math", "cs408", "english"))
        ]

        with ThreadPoolExecutor(max_workers=3) as pool:
            executions = list(
                pool.map(
                    lambda value: ReadFanoutScheduler(physical_slots=4).run(
                        value, observed
                    ),
                    plans,
                )
            )

        self.assertGreater(observed.maximum_active, 4)
        self.assertEqual(
            [execution["maximum_active_branch_count"] for execution in executions],
            [4, 4, 4],
        )
        self.assertTrue(
            all(execution["terminal_branch_count"] == 4 for execution in executions)
        )


if __name__ == "__main__":
    unittest.main()
