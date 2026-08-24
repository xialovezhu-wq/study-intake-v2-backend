from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from multi_agent_v2_orchestrator import (  # noqa: E402
    bind_frozen_authority_snapshot,
    completion_status,
    frozen_authority_snapshot,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context() -> dict[str, Any]:
    manifest_sha = _sha("manifest")
    receipt_sha = _sha("receipt")
    fingerprint = _sha("authority")
    receipt = {
        "generation": "generation-1",
        "authority_fingerprint": fingerprint,
        "authority_snapshot_manifest_sha256": manifest_sha,
        "file_count": 2,
        "total_bytes": 256,
        "formal_write_count": 0,
    }
    return {
        "mcp_read_session": {
            "generation": "generation-1",
            "authority_fingerprint": fingerprint,
            "authority_snapshot_manifest_path": "/snapshot/manifest.json",
            "authority_snapshot_manifest_sha256": manifest_sha,
            "authority_snapshot_root": "/snapshot/root",
            "authority_snapshot_receipt_sha256": receipt_sha,
        },
        "authority_snapshot_receipt": receipt,
        "authority_snapshot_receipt_sha256": receipt_sha,
        "authority_snapshot_receipt_ref": (
            "study-intake-mcp-authority-snapshot://sha256/" + receipt_sha
        ),
    }


class _Host:
    def __init__(self) -> None:
        self.freeze_count = 0

    def _freeze_authority_snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        self.freeze_count += 1
        raise AssertionError("a second physical snapshot was attempted")


class MultiAgentV2OrchestratorTests(unittest.TestCase):
    def test_one_frozen_snapshot_is_reused_by_independent_session_hosts(self) -> None:
        snapshot = frozen_authority_snapshot(_context())
        hosts = [_Host(), _Host(), _Host()]
        for host in hosts:
            bind_frozen_authority_snapshot(host, snapshot)
            rebound = host._freeze_authority_snapshot(
                generation="generation-1",
                authority_fingerprint=_sha("authority"),
                subject="math",
                mcp_server_release="fixture",
            )
            self.assertEqual(rebound, snapshot)
            self.assertEqual(host.freeze_count, 0)

    def test_completion_status_marks_any_luna_failure_as_warning(self) -> None:
        self.assertEqual(
            completion_status([{"status": "succeeded"}] * 3),
            "multi_agent_analysis_package_ready",
        )
        self.assertEqual(
            completion_status([{"status": "failed"}] * 3),
            "completed_with_warnings",
        )
        self.assertEqual(
            completion_status(
                [{"status": "succeeded"}, {"status": "timed_out"}]
            ),
            "completed_with_warnings",
        )


if __name__ == "__main__":
    unittest.main()
