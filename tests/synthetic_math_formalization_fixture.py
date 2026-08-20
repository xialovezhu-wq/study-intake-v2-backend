"""Synthetic, temporary inputs for the mathematics formalization contract.

The contract test deliberately exercises the same production validators and
shadow-replay builder as the deployed path, but all evidence in this module is
created below a caller-owned temporary directory.  Nothing here points at a
user home, deployment, release, or non-synthetic capture.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import math_shadow_backaudit as backaudit


CAPTURE_BACKED_FIELDS = (
    "safe_summary",
    "question_body",
    "source_and_answer",
    "wrong_point",
    "methods",
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
        + b"\n"
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _claim(ref: str, text: str = "Synthetic evidence-bound claim.") -> dict[str, Any]:
    return {
        "claim_type": "evidence_bound_inference",
        "text": text,
        "provenance": "capture",
        "evidence_refs": [ref],
        "confidence": "medium",
        "counterevidence_or_boundary": (
            "Synthetic fixture evidence is bounded to this capture and does not establish library identity."
        ),
        "sol_verification_action": (
            "Sol must independently reopen the synthetic capture before any formal decision."
        ),
    }


def synthetic_analysis_payload(ref: str) -> dict[str, Any]:
    """Return a complete v2 analysis with deliberately empty formal fields."""

    claim = _claim(ref)
    formal_fields = {
        field: []
        for field in (
            "safe_summary",
            "question_body",
            "source_and_answer",
            "independent_performance",
            "wrong_point",
            "error_causes",
            "methods",
            "traps",
            "method_gap",
            "wrong_history",
            "mastery_evidence",
            "relationship_proposals",
        )
    }

    def c() -> dict[str, Any]:
        return copy.deepcopy(claim)

    unresolved_identity = c()
    unresolved_identity["claim_type"] = "unresolved"

    return {
        "schema_version": "study-intake-luna-math-analysis-v2",
        "report_profile": "math_deep",
        "executive_summary": (
            "Synthetic evidence is bounded; formal identity and relationship decisions remain unresolved."
        ),
        "target_identity": {
            "formal_target": [unresolved_identity],
            "delivered_target": [c()],
            "knowledge_fallback_anchor": [],
            "identity_boundary": [c()],
        },
        "question_structure": {
            "objects": [c()],
            "conditions": [c()],
            "asked_task": [c()],
            "source_answer": [c()],
        },
        "correct_reasoning_reconstruction": [],
        "evidence_assessment": {
            "completeness": "limited_by_evidence",
            "evidence_inventory": [c()],
            "observed_facts": [c()],
            "inferences": [c()],
            "contradictions": [],
            "gaps": [c()],
        },
        "reasoning_diagnosis": {
            "independent_correct_steps": [c()],
            "first_break": c(),
            "later_breaks": [],
            "hint_dependencies": [c()],
            "self_corrections": [],
            "history_merge": [c()],
        },
        "knowledge_error_signatures": {
            "primary_knowledge": [c()],
            "secondary_knowledge": [],
            "methods": [c()],
            "first_error": c(),
            "later_errors": [],
            "historical_errors": [],
            "current_errors": [],
            "relationship_search_intents": [c()],
        },
        "concept_method_analysis": {
            "mechanisms": [c()],
            "method_triggers": [c()],
            "applicability_conditions": [c()],
            "boundary_conditions": [c()],
            "common_confusions": [c()],
            "transfer_risks": [c()],
        },
        "formalization_candidates": formal_fields,
        "sol_verification_plan": {
            "must_verify": [c()],
            "reject_if": [c()],
            "source_checks": [c()],
            "identity_checks": [c()],
            "recommended_disposition": "insufficient_evidence",
        },
        "risk_flags": [],
        "unresolved": [c()],
        "atomic_signals": [
            {
                "signal_id": "SIG-MATH-SYNTHETIC-0001",
                "signal_type": "knowledge",
                "canonical_term": "synthetic-knowledge",
                "surface_form": "synthetic-knowledge",
                "importance": "primary",
                "provenance": "capture",
                "evidence_refs": [ref],
                "confidence": "high",
                "error_role": "none",
                "specificity": "Synthetic knowledge signal for contract coverage.",
                "applicability_boundary": "Only the synthetic capture.",
                "truth_library_match_status": "exact",
            },
            {
                "signal_id": "SIG-MATH-SYNTHETIC-0002",
                "signal_type": "method",
                "canonical_term": "synthetic-method",
                "surface_form": "synthetic-method",
                "importance": "primary",
                "provenance": "capture",
                "evidence_refs": [ref],
                "confidence": "high",
                "error_role": "none",
                "specificity": "Synthetic method signal for contract coverage.",
                "applicability_boundary": "Only the synthetic capture.",
                "truth_library_match_status": "exact",
            },
        ],
    }


def _synthetic_worker_config(
    *, root: Path, repo: Path, schema_root: Path
) -> dict[str, Any]:
    """Build the smallest complete shadow Worker configuration."""

    runtime = root / "worker-runtime"
    status_script = repo / "synthetic-status.py"
    status_script.write_text("# Synthetic status script; never executed by this test.\n")
    return {
        "schema_version": "study-intake-preprocessor-config-v1",
        "runtime_root": str(runtime),
        "timezone": "Asia/Shanghai",
        "dispatch": {
            "authority_required": True,
            "heartbeat_interval_seconds": 15,
            "lease_ttl_seconds": 120,
            "infrastructure_recovery_attempts": 1,
        },
        "worker": {
            "enabled": True,
            "poll_interval_seconds": 1,
            "debounce_seconds": 0,
            "status_timeout_seconds": 5,
            "model_timeout_seconds": 5,
            "max_attempts": 3,
            "retry_base_seconds": 0,
            "max_jobs_per_scan": 4,
            "log_path": str(runtime / "logs/worker.log"),
            "lock_path": str(runtime / "state/worker.lock"),
        },
        "model": {
            "codex_path": sys.executable,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "output_schema": str(schema_root / "luna-analysis-v1.json"),
            "prompt_version": "synthetic-v1",
            "max_prompt_bytes": 131072,
            "max_images": 8,
        },
        "math_deep_v2": {
            "enabled": True,
            "mode": "shadow",
            "canary_percent": 10,
            "shadow_evaluation_start_at": "2026-08-20T00:00:00+08:00",
            "shadow_evaluation_target_count": 20,
            "soft_runtime_warning_seconds": 1800,
            "stall_timeout_seconds": 1800,
            "stall_probe_interval_seconds": 60,
            "stall_probe_required_consecutive_failures": 2,
            "analysis_output_schema": str(schema_root / "luna-math-analysis-v2.json"),
            "critical_review_output_schema": str(
                schema_root / "luna-math-critical-review-v2.json"
            ),
            "package_output_schema": str(schema_root / "preprocess-package-v3.json"),
            "analysis_prompt_version": "synthetic-math-analysis-v2",
            "critical_review_prompt_version": "synthetic-math-review-v2",
            "max_prompt_bytes": 524288,
            "max_output_bytes": 262144,
            "min_complete_chinese_chars": 0,
            "max_chinese_chars": 7000,
            "gs111_cold_replay_max_seconds": 1800,
            "three_replay_p95_max_seconds": 1200,
        },
        "math_knowledge_snapshot": {
            "enabled": True,
            "max_source_bytes": 8388608,
            "max_distribution_terms": 64,
            "max_local_neighbors": 8,
            "max_relationship_candidates": 5,
            "max_snapshot_bytes": 131072,
            "sources": {
                "graph": "synthetic/graph.mmd",
                "projection": "synthetic/projection.json",
                "taxonomy": "synthetic/taxonomy.md",
                "relationship_policy": "synthetic/policy.json",
            },
        },
        "dashboard": {
            "projection_path": str(runtime / "state/dashboard_projection.json"),
            "max_items_per_subject": 200,
        },
        "adapters": {
            "math": {
                "enabled": True,
                "adapter_version": "math-synthetic-v1",
                "python_path": sys.executable,
                "repo_root": str(repo),
                "status_script": str(status_script),
            }
        },
    }


@dataclass
class SyntheticMathFormalizationFixture:
    root: Path
    schema_root: Path
    payload: dict[str, Any]
    transport: dict[str, Any]
    transcript: dict[str, Any]
    read_session: dict[str, Any]
    failure_receipt: dict[str, Any]
    provider_schema: dict[str, Any]
    invalid_provider_schema: dict[str, Any]
    invalid_provider_schema_path: Path
    source_binding: dict[str, str]
    evidence_refs: tuple[str, ...]
    manifest: dict[str, Any]
    config: dict[str, Any]
    repo_root: Path
    capture_id: str
    formal_id: str

    @classmethod
    def create(cls, root: Path, schema_root: Path) -> "SyntheticMathFormalizationFixture":
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        repo = root / "synthetic-source-repo"
        source_dir = repo / "synthetic"
        source_dir.mkdir(parents=True, exist_ok=True)

        image_path = source_dir / "question.png"
        image_path.write_bytes(b"synthetic-image-bytes")
        image_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
        source_manifest = {
            "schema_version": "synthetic-math-source-manifest-v1",
            "source_locator": "synthetic://math/source",
            "artifacts": [
                {
                    "role": "question",
                    "media_type": "image/png",
                    "path": "synthetic/question.png",
                    "sha256": image_sha256,
                }
            ],
        }
        source_manifest_path = source_dir / "source-manifest.json"
        source_manifest_path.write_bytes(_canonical_bytes(source_manifest))
        source_manifest_sha256 = hashlib.sha256(
            source_manifest_path.read_bytes()
        ).hexdigest()

        formal_id = "SYN-001"
        formal_content = (
            "# Synthetic formal card\n\n"
            "This is an isolated card preimage used only for replay binding.\n"
        )
        formal_card_path = source_dir / "formal-card.md"
        formal_card_path.write_text(formal_content, encoding="utf-8")
        formal_card_sha256 = hashlib.sha256(
            formal_content.encode("utf-8")
        ).hexdigest()

        capture_id = "MFI-CAP-000000000000000000000001"
        study_date = "2026-08-20"
        target_group_key = f"formal:{formal_id}"
        capture = {
            "event_id": capture_id,
            "capture_schema_version": "math-fast-intake-event-v1",
            "study_date": study_date,
            "recorded_at": "2026-08-20T00:01:00+08:00",
            "formal_id": formal_id,
            "source_locator": "synthetic://math/source",
            "source_hash_before": formal_card_sha256,
            "identity_state": "verified_formal",
            "source_bundle": {
                "source_locator": "synthetic://math/source",
                "manifest_hash": source_manifest_sha256,
            },
            "requested_action": "record_wrong",
            "score_ref": {
                "delivered_card_id": formal_id,
                "match_mode": "direct",
            },
            "original_content_hash": _sha256({"capture": "original"}),
            "amendment_count": 0,
            "amendment_event_ids": [],
            "effective_evidence_hash": _sha256({"capture": "evidence"}),
            "effective_target_hash": _sha256({"capture": "target"}),
            "target": {
                "identity_mode": "verified_formal",
                "identity_state": "verified_formal",
                "source_locator": "synthetic://math/source",
                "source_hash_before": formal_card_sha256,
                "card_path_before": "synthetic/formal-card.md",
                "card_hash_before": formal_card_sha256,
            },
            "evidence": {
                "user_facts": [{"text": "Synthetic user fact."}],
                "first_break": [{"text": "Synthetic first break."}],
                "result": {"kind": "synthetic"},
            },
        }
        snapshot = {
            "capture_content_hash": _sha256({"snapshot": "content"}),
            "effective_evidence_hash": capture["effective_evidence_hash"],
            "effective_target_hash": capture["effective_target_hash"],
            "amendment_event_ids": [],
        }
        limitations = ["synthetic_network_not_frozen"]
        historical_card = {
            "status": "verified_unchanged",
            "path": "synthetic/formal-card.md",
            "sha256": formal_card_sha256,
            "content": formal_content,
        }
        replay_input = {
            "subject": "math",
            "study_date": study_date,
            "capture_id": capture_id,
            "target_group_key": target_group_key,
            "capture_event": capture,
            "amendment_events": [],
            "capture_snapshot": snapshot,
            "source_bundle": {
                "manifest_path": "synthetic/source-manifest.json",
                "manifest_sha256": source_manifest_sha256,
                "manifest": source_manifest,
                "artifacts": [
                    {
                        "role": "question",
                        "media_type": "image/png",
                        "path": "synthetic/question.png",
                        "sha256": image_sha256,
                    }
                ],
            },
            "formal_target": {
                "formal_id": formal_id,
                "identity_mode": "verified_formal",
                "frozen_mapping": {
                    "path_before": "synthetic/formal-card.md",
                    "sha256_before": formal_card_sha256,
                },
                "historical_card": historical_card,
            },
            "limitations": limitations,
            "formal_write_count": 0,
        }
        item: dict[str, Any] = {
            "capture_id": capture_id,
            "formal_id": formal_id,
            "target_group_key": target_group_key,
            "visual_required": True,
            "capture_event_sha256": backaudit.sha256_value(capture),
            "capture_snapshot": snapshot,
            "frozen_target_mapping": capture["target"],
            "current_formal_target": {
                "path": "synthetic/formal-card.md",
                "sha256": formal_card_sha256,
            },
            "current_formal_diagnostics": {"status": "synthetic"},
            "replay_input": replay_input,
            "replay_input_sha256": backaudit.sha256_value(replay_input),
            "replay_status": "limited_by_evidence",
            "replay_limitations": limitations,
            "formal_write_count": 0,
        }
        selection_payload = {
            "study_date": study_date,
            "freeze_id": "MFI-FREEZE-SYNTHETIC-0001",
            "capture_ids": [capture_id],
            "capture_snapshot_hashes": [backaudit.sha256_value(snapshot)],
        }
        manifest: dict[str, Any] = {
            "schema_version": "study-intake-math-shadow-manifest-v1",
            "subject": "math",
            "study_date": study_date,
            "selection_contract": {
                "kind": "synthetic_scenario",
                "expected_count": 1,
                "actual_count": 1,
                "freeze_id": selection_payload["freeze_id"],
                "capture_ids": [capture_id],
                "capture_snapshot_hashes": selection_payload[
                    "capture_snapshot_hashes"
                ],
                "selection_sha256": backaudit.sha256_value(selection_payload),
            },
            "baseline_adoption_counts": {"fallback": 1},
            "items": [item],
            "integrity": {
                "formal_write_count": 0,
                "package_count": 1,
                "adoption_count": 1,
                "run_receipt_count": 1,
            },
        }
        manifest["manifest_sha256"] = backaudit.sha256_value(
            backaudit.manifest_integrity_payload(manifest)
        )

        ref = "capture.synthetic.evidence"
        payload = synthetic_analysis_payload(ref)
        transport_items = [
            {
                "item": {
                    "tool": f"synthetic_tool_{index}",
                    "arguments": {"page_size": index + 1},
                    "result": {"structured_content": {"items": []}},
                }
            }
            for index in range(13)
        ]
        transport_items[-1] = {
            "item": {
                "tool": "search_records",
                "arguments": {"query": "synthetic-query", "page_size": 48},
                "result": {
                    "structured_content": {
                        "error": {"code": "OUTPUT_LIMIT"}
                    }
                },
            }
        }
        transport = {
            "schema_version": "synthetic-math-transport-v1",
            "mcp_items": transport_items,
            "mcp_item_count": len(transport_items),
        }
        transcript = {
            "schema_version": "synthetic-math-transcript-v1",
            "calls": [
                {"call_index": index, "tool": "synthetic_tool"}
                for index in range(12)
            ],
        }
        read_session = {
            "schema_version": "synthetic-math-read-session-v1",
            "artifact_ids": [
                _sha256({"artifact_index": index}) for index in range(10)
            ],
        }
        failure_receipt = {
            "schema_version": "synthetic-math-failure-receipt-v1",
            "error_code": "math_analysis_formal_field_coverage_failed",
            "formal_write_count": 0,
        }

        provider_schema = json.loads(
            (schema_root / "luna-math-analysis-v2.json").read_text(encoding="utf-8")
        )
        invalid_provider_schema = copy.deepcopy(provider_schema)
        properties = invalid_provider_schema["$defs"]["formalization_candidates"][
            "properties"
        ]
        for field in CAPTURE_BACKED_FIELDS:
            properties[field] = {
                "$ref": "#/$defs/claims",
                "minItems": 1,
                "maxItems": 1,
            }
        invalid_path = root / "synthetic-invalid-provider-schema.json"
        invalid_path.write_bytes(_canonical_bytes(invalid_provider_schema))

        config = _synthetic_worker_config(
            root=root, repo=repo, schema_root=schema_root
        )
        source_binding = {
            "source_route": "new_intake",
            "evidence_manifest_sha256": _sha256({"binding": "manifest"}),
            "evidence_bundle_sha256": _sha256({"binding": "bundle"}),
        }
        return cls(
            root=root,
            schema_root=schema_root,
            payload=payload,
            transport=transport,
            transcript=transcript,
            read_session=read_session,
            failure_receipt=failure_receipt,
            provider_schema=provider_schema,
            invalid_provider_schema=invalid_provider_schema,
            invalid_provider_schema_path=invalid_path,
            source_binding=source_binding,
            evidence_refs=(ref,),
            manifest=manifest,
            config=config,
            repo_root=repo,
            capture_id=capture_id,
            formal_id=formal_id,
        )

    def source_snapshot(self) -> dict[str, str]:
        """Hash the synthetic source tree to prove replay stays read-only."""

        return {
            str(path.relative_to(self.repo_root)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(self.repo_root.rglob("*"))
            if path.is_file()
        }
