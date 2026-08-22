from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from concurrent_dispatch import DispatchError, FrozenTask, LeaseStore  # noqa: E402
from manual_capture_admission import (  # noqa: E402
    ManualAdmissionError,
    build_task_execution_authorization_v2,
    capture_manifest_sha256,
    validate_task_execution_authorization_v2,
)


NOW = dt.datetime(2026, 8, 22, 4, 0, tzinfo=dt.timezone.utc)
RELEASE_ID = "a" * 64
ACTIVATION_ID = "b" * 64


def manifest(capture_id: str, marker: str) -> list[dict[str, str]]:
    return [
        {
            "capture_id": capture_id,
            "capture_content_sha256": marker * 64,
            "source_receipt_sha256": chr(ord(marker) + 1) * 64,
            "producer_attestation_sha256": chr(ord(marker) + 2) * 64,
        }
    ]


def task(capture_id: str, marker: str) -> FrozenTask:
    return FrozenTask(
        {
            "subject": "math",
            "capture_id": capture_id,
            "study_date": "2026-08-22",
            "recorded_at": "2026-08-22T11:59:00+08:00",
            "input_fingerprint": marker * 64,
            "input_binding": {"capture_content_sha256": marker * 64},
            "model_input": {"fixture": capture_id},
            "allowed_evidence_refs": [f"capture:{capture_id}"],
            "image_paths": [],
            "target_label": capture_id,
            "canonical_state": "pending_nightly",
            "sol_state": "disabled",
            "dispatch_contract": {
                "schema_version": "study-intake-dispatch-release-binding-v1",
                "release_id": RELEASE_ID,
                "activation_id": ACTIVATION_ID,
                "rule_version": "authorization-v2-test",
                "rule_version_sha256": "f" * 64,
                "dispatch_reason": "production_canary_pending_queue",
            },
        }
    )


class TaskExecutionAuthorizationV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="task-authorization-v2-")
        self.runtime = Path(self.temp.name) / "runtime"
        self.key = b"k" * 32
        key_path = self.runtime / "dispatch/state/authority.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(self.key)
        os.chmod(key_path, 0o600)
        self.store = LeaseStore(self.runtime)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def authorization(
        self,
        frozen: FrozenTask,
        capture_manifest: list[dict[str, str]],
    ) -> dict[str, object]:
        return build_task_execution_authorization_v2(
            subject=str(frozen.frozen_payload["subject"]),
            unit_sha256=frozen.unit_sha256,
            frozen_payload_sha256=frozen.frozen_payload_sha256,
            capture_manifest=capture_manifest,
            release_id=RELEASE_ID,
            activation_id=ACTIVATION_ID,
            issuer_authorization_receipt_sha256="e" * 64,
            authority_key=self.key,
            now=NOW,
        )

    def test_capability_binds_exact_task_and_complete_capture_manifest(self) -> None:
        frozen = task("MFI-CAP-AUTH-001", "1")
        capture_manifest = manifest("MFI-CAP-AUTH-001", "1")
        authorization = self.authorization(frozen, capture_manifest)
        identity = {
            "subject": "math",
            "unit_sha256": frozen.unit_sha256,
            "frozen_payload_sha256": frozen.frozen_payload_sha256,
            "capture_manifest_sha256": capture_manifest_sha256(
                capture_manifest
            ),
            "release_id": RELEASE_ID,
            "activation_id": ACTIVATION_ID,
        }

        checked = validate_task_execution_authorization_v2(
            authorization,
            task_identity=identity,
            authority_key=self.key,
            now=NOW,
        )

        self.assertEqual(checked["maximum_tasks"], 1)
        self.assertTrue(checked["allow_terra"])
        self.assertTrue(checked["allow_luna"])
        self.assertFalse(checked["formal_write_allowed"])
        self.assertEqual(checked["capture_manifest"], capture_manifest)

        sibling = dict(identity)
        sibling["capture_manifest_sha256"] = "9" * 64
        with self.assertRaises(ManualAdmissionError) as caught:
            validate_task_execution_authorization_v2(
                authorization,
                task_identity=sibling,
                authority_key=self.key,
                now=NOW,
            )
        self.assertEqual(
            caught.exception.code,
            "task_execution_authorization_task_mismatch",
        )

    def test_claim_and_capability_are_atomic_under_existing_dispatch_lock(self) -> None:
        frozen = task("MFI-CAP-AUTH-002", "2")
        authorization = self.authorization(
            frozen, manifest("MFI-CAP-AUTH-002", "2")
        )

        decision = self.store.claim(
            frozen.unit_sha256,
            "owner-a",
            subject="math",
            task=frozen,
            task_execution_authorization=authorization,
            require_task_execution_authorization=True,
            now="2026-08-22T04:00:00+00:00",
        )

        self.assertEqual(decision.status, "claimed")
        self.assertIsNotNone(decision.lease)
        claimed = self.store.verify_claimed_task_execution_authorization(
            frozen, decision.lease
        )
        self.assertEqual(
            claimed["authorization_sha256"],
            authorization["authorization_sha256"],
        )
        lease = json.loads(
            self.store._lease_path(frozen.unit_sha256).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            lease["task_execution_authorization_sha256"],
            authorization["authorization_sha256"],
        )
        self.assertEqual(
            self.store.task_execution_authorization_status(
                str(authorization["authorization_sha256"])
            )["status"],
            "claimed",
        )

        duplicate = self.store.claim(
            frozen.unit_sha256,
            "owner-b",
            subject="math",
            task=frozen,
            task_execution_authorization=authorization,
            require_task_execution_authorization=True,
            now="2026-08-22T04:00:01+00:00",
        )
        self.assertEqual(duplicate.status, "active")
        metrics = self.store.task_execution_authorization_metrics()
        self.assertEqual(metrics["authorization_issued_count"], 1)
        self.assertEqual(metrics["authorization_claimed_count"], 1)
        self.assertEqual(metrics["authorization_double_claim_rejected_count"], 1)

    def test_terminal_consumes_capability_without_touching_fixture_v1(self) -> None:
        frozen = task("MFI-CAP-AUTH-003", "3")
        authorization = self.authorization(
            frozen, manifest("MFI-CAP-AUTH-003", "3")
        )
        decision = self.store.claim(
            frozen.unit_sha256,
            "owner-a",
            subject="math",
            task=frozen,
            task_execution_authorization=authorization,
            require_task_execution_authorization=True,
            now="2026-08-22T04:00:00+00:00",
        )
        assert decision.lease is not None

        completion = self.store.publish_terminal(
            decision.lease,
            task=frozen,
            outcome="failed",
            error_code="fixture_terminal",
            analysis=None,
            critical_review=None,
            started_at="2026-08-22T04:00:00+00:00",
            finished_at="2026-08-22T04:00:01+00:00",
        )

        self.assertEqual(
            completion["task_execution_authorization_sha256"],
            authorization["authorization_sha256"],
        )
        terminal = self.store.task_execution_authorization_status(
            str(authorization["authorization_sha256"])
        )
        self.assertEqual(terminal["status"], "consumed_failed")
        self.assertEqual(
            terminal["terminal_receipt_sha256"], completion["receipt_sha256"]
        )
        self.assertFalse(
            (
                self.runtime
                / "dispatch/manual-live-authorization-v1/state.json"
            ).exists()
        )

    def test_missing_or_cross_subject_capability_fails_before_claim(self) -> None:
        frozen = task("MFI-CAP-AUTH-004", "4")
        with self.assertRaises(DispatchError) as missing:
            self.store.claim(
                frozen.unit_sha256,
                "owner-a",
                subject="math",
                task=frozen,
                require_task_execution_authorization=True,
                now="2026-08-22T04:00:00+00:00",
            )
        self.assertEqual(
            missing.exception.code, "task_execution_authorization_missing"
        )
        self.assertFalse(self.store._lease_path(frozen.unit_sha256).exists())

        authorization = self.authorization(
            frozen, manifest("MFI-CAP-AUTH-004", "4")
        )
        authorization["subject"] = "english"
        with self.assertRaises(DispatchError) as mismatched:
            self.store.claim(
                frozen.unit_sha256,
                "owner-a",
                subject="math",
                task=frozen,
                task_execution_authorization=authorization,
                require_task_execution_authorization=True,
                now="2026-08-22T04:00:00+00:00",
            )
        self.assertEqual(
            mismatched.exception.code,
            "task_execution_authorization_binding_rejected",
        )
        self.assertFalse(self.store._lease_path(frozen.unit_sha256).exists())


if __name__ == "__main__":
    unittest.main()
