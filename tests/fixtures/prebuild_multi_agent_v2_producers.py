"""Answer-safe real-Producer fixtures for the nine-task pre-Build trial.

This module intentionally lives under tests/fixtures because its stems and
payloads are synthetic.  Every ledger mutation is still performed by the
copied subject's real foreground Producer entrypoint, and every Candidate is
reopened through the backend's real subject Adapter.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any, Mapping


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_value(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_json(command: list[str], *, cwd: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env or os.environ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Producer failed ({completed.returncode}): {' '.join(command)}\n"
            + completed.stderr
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise AssertionError("Producer did not return an object")
    return value


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def _descriptor(
    *, subject: str, skill: Path, installed: Path,
    producer_files: list[Path], contract_files: list[Path],
    attestation_relative_root: str,
) -> dict[str, Any]:
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(skill.read_bytes())
    sources = [
        {"path": str(path.resolve()), "sha256": _sha_file(path)}
        for path in producer_files
    ]
    contracts = [
        {"path": str(path.resolve()), "sha256": _sha_file(path)}
        for path in contract_files
    ]
    core = {
        "schema_version": "producer_binding_descriptor_v1",
        "subject": subject,
        "attestation_required_after": "2026-01-01T00:00:00+00:00",
        "foreground_skill": {
            "authoritative_path": str(skill.resolve()),
            "authoritative_sha256": _sha_file(skill),
            "installed_path": str(installed.resolve()),
            "installed_sha256": _sha_file(installed),
        },
        "producer": {
            "source_files": sources,
            "source_closure_sha256": _sha_value(sources),
        },
        "capture_contract": {"files": contracts},
        "attestation_relative_root": attestation_relative_root,
        "formal_write_count": 0,
    }
    return {**core, "descriptor_content_sha256": _sha_value(core)}


def _write_descriptor(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _seed_math(root: Path, backend_root: Path) -> list[dict[str, Any]]:
    script = root / "数学一回滚复习系统/scripts/quick_intake.py"
    skill = root / "codex-skill-sources/kaoyan-math-wrong-intake/SKILL.md"
    descriptor_path = root / "数学一回滚复习系统/schema/producer-binding-v1.json"
    _write_descriptor(
        descriptor_path,
        _descriptor(
            subject="math",
            skill=skill,
            installed=root / ".prebuild-installed/kaoyan-math-wrong-intake/SKILL.md",
            producer_files=[script, script.with_name("producer_binding_attestation.py")],
            contract_files=[
                root / "数学一回滚复习系统/schema/quick_intake_events.md",
                root / "数学一回滚复习系统/schema/producer-binding-v1.example.json",
            ],
            attestation_relative_root="数学一回滚复习系统/快速入库绑定证明",
        ),
    )
    rollback = root / "数学一回滚复习系统"
    for path in (
        rollback / "快速入库事件.jsonl",
        rollback / ".快速入库事件.jsonl.lock",
        rollback / ".快速入库来源.lock",
    ):
        path.unlink(missing_ok=True)
    for path in (rollback / "快速入库来源", rollback / "快速入库绑定证明"):
        if path.exists():
            shutil.rmtree(path)
    (rollback / "复习记录.jsonl").write_text("", encoding="utf-8")
    (rollback / "复习单元.json").write_text("[]\n", encoding="utf-8")
    env = dict(os.environ)
    env["KAOYAN_MATH_PRODUCER_BINDING_DESCRIPTOR"] = str(descriptor_path)
    receipts: list[dict[str, Any]] = []
    for ordinal in range(1, 4):
        source_dir = root / ".prebuild-input" / f"math-{ordinal}"
        source_dir.mkdir(parents=True, exist_ok=True)
        question = source_dir / "question.png"
        solution = source_dir / "solution.txt"
        question.write_bytes(_png())
        solution_text = (
            f"prebuild:v2:math:{ordinal} synthetic solution text; "
            "compare the stated conditions."
        )
        solution.write_text(solution_text + "\n", encoding="utf-8")
        locator = f"prebuild://math/{ordinal}"
        stage_payload = source_dir / "stage.json"
        stage_payload.write_bytes(
            _canonical(
                {
                    "schema_version": "math-fast-intake-source-stage-v1",
                    "study_date": "2026-08-24",
                    "source_locator": locator,
                    "artifacts": [
                        {"role": "question", "path": str(question)},
                        {"role": "solution_text", "path": str(solution)},
                    ],
                }
            )
        )
        staged = _run_json(
            [sys.executable, str(script), "stage-source", "--payload-file", str(stage_payload)],
            cwd=root,
            env=env,
        )
        capture_payload = source_dir / "capture.json"
        capture_payload.write_bytes(
            _canonical(
                {
                    "schema_version": "math-fast-intake-capture-v2",
                    "attempt_id": f"prebuild:v2:math:{ordinal}",
                    "study_date": "2026-08-24",
                    "target": {
                        "kind": "new_source", "formal_id": None,
                        "source_locator": locator,
                        "source_hash_before": staged["manifest_hash"],
                    },
                    "score_event_id": None,
                    "requested_action": "record_wrong",
                    "thread_ref": "prebuild-v2-nine-task-trial",
                    "capture_authorization": {
                        "current_user_message": "synthetic prebuild 快速入库 Capture"
                    },
                    "source_bundle": {
                        "manifest_path": staged["manifest_path"],
                        "manifest_hash": staged["manifest_hash"],
                    },
                    "episode_evidence": {
                        "solution_text": solution_text,
                        "user_answer_text": f"Synthetic reasoning {ordinal}",
                        "teaching_turns": [
                            {
                                "speaker": "user", "kind": "reasoning",
                                "text": "I checked the first condition only.",
                                "origin": "user_observed",
                            }
                        ],
                    },
                    "evidence": {
                        "result": "wrong",
                        "user_facts": [{
                            "text": "The second condition was not checked.",
                            "origin": "user_observed",
                        }],
                        "independent_correct_steps": [],
                        "first_break": {
                            "kind": "condition",
                            "text": "Missed one independent condition.",
                            "origin": "user_confirmed",
                        },
                        "later_breaks": [],
                        "hints_needed": [],
                        "self_corrections": [],
                        "mastery_score": 1,
                        "mastery_source": "assistant_assessment",
                        "score_basis": {
                            "text": "Synthetic observed condition gap.",
                            "origin": "source_verified",
                        },
                        "unresolved": [],
                    },
                }
            )
        )
        receipt = _run_json(
            [sys.executable, str(script), "record", "--payload-file", str(capture_payload)],
            cwd=root,
            env=env,
        )
        receipts.append(receipt)

    if str(backend_root / "lib") not in sys.path:
        sys.path.insert(0, str(backend_root / "lib"))
    from preprocessor_core import MathAdapter
    adapter = MathAdapter(
        {
            "enabled": True,
            "repo_root": str(root),
            "status_script": str(script),
            "python_path": sys.executable,
            "adapter_version": "prebuild-real-math-v2",
            "deep_v2_mode": "production",
            "max_images": 8,
            "processing_contract": {
                "processing_contract_sha256": "9" * 64,
                "adapter_build_sha256": "8" * 64,
            },
        },
        {"status_timeout_seconds": 30},
    )
    candidates = [adapter.deep_candidate(row) for row in adapter.candidates(adapter.status(None))]
    by_id = {row.capture_id: row for row in candidates}
    return [
        {
            "subject": "math", "capture_id": receipt["event_id"],
            "candidate": by_id[receipt["event_id"]],
            "producer_entrypoint": str(script),
            "producer_receipt": {
                **receipt, "prebuild_marker": f"prebuild:v2:math:{ordinal}"
            },
        }
        for ordinal, receipt in enumerate(receipts, 1)
    ]


def _seed_cs408(root: Path, backend_root: Path, trial_root: Path) -> list[dict[str, Any]]:
    test_module = _load(
        "prebuild_cs408_managed_fixture",
        root / "tests/test_morning_review_prepared_pack_managed_hot_408.py",
    )
    test_module.setUpModule()
    case = test_module.PreparedPackManagedHotPath408Tests(methodName="runTest")
    # Reproduce its setup directly in the supplied isolated subject root.
    case.repo = root
    case.queue = root / "queue.md"
    case.queue.write_text(test_module.QUEUE, encoding="utf-8")
    case.evidence = root / "safe-source.md"
    case.evidence.write_text("Synthetic safe mechanism evidence.\n", encoding="utf-8")
    policy = root / "schema/morning-review-backflow-policy-v1.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_bytes(
        (root / "schema/morning-review-backflow-policy-v1.json").read_bytes()
    )
    case._write_formal_projection_sources()
    case._write_empty_review_truth()
    case._write_empty_capture_truth()
    test_module.formal_hot.build_projection(root, as_of="2026-07-31")
    published = case._publish_pack()

    capture_script = root / "scripts/intake_fact_capture_408.py"
    skill = root / "codex-skill-sources/kaoyan-408-wrong-intake/SKILL.md"
    descriptor_path = root / "schema/producer-binding-v1.json"
    _write_descriptor(
        descriptor_path,
        _descriptor(
            subject="cs408",
            skill=skill,
            installed=root / ".prebuild-installed/kaoyan-408-wrong-intake/SKILL.md",
            producer_files=[
                root / "scripts/intake_fact_capture_408.py",
                root / "scripts/capture_hot_writer_408.py",
                root / "scripts/managed_408_current_turn.py",
                root / "scripts/capture_commit_index_408.py",
                root / "scripts/bounded_jsonl_index_408.py",
                root / "scripts/intake_lib_408.py",
                root / "scripts/producer_binding_attestation_408.py",
            ],
            contract_files=[
                root / "schema/current-question-evidence-bundle-v3.md",
                root / "schema/morning-review-backflow-policy-v1.json",
                root / "schema/producer-binding-v1.example.json",
                skill,
            ],
            attestation_relative_root=".producer-binding-attestations",
        ),
    )
    test_module.capture_model.PRODUCER_BINDING_DESCRIPTOR_PATH = descriptor_path
    private_root = trial_root / "runtime/private/current-question-evidence"
    sessions = [f"MR-prebuild-v2-{ordinal}" for ordinal in range(1, 4)]
    receipts: list[dict[str, Any]] = []
    for ordinal, session_id in enumerate(sessions, 1):
        test_module.morning.command_start(
            argparse.Namespace(
                repo=str(root), queue=str(case.queue), session_id=session_id,
                item=None, prepared_manifest=str(published["manifest_ref"]),
            )
        )
        shown = test_module.prepared_pack.show_item(root, session_id, test_module.ITEM_ID)
        prepared = test_module.prepared_pack.prepare_current_turn(
            root,
            session_id=session_id,
            item_id=test_module.ITEM_ID,
            choice="C",
            confidence="high",
            prompt_level="none",
            request_id=f"prebuild-v2-cs408-{ordinal}",
            event_time=f"2026-07-31T08:0{ordinal}:00+08:00",
            display_surface_sha256=shown["surface_sha256"],
            private_root=private_root,
        )
        capsule = test_module.current_evidence.read_evaluation_capsule(
            prepared["grader_capsule_locator"],
            expected_sha256=prepared["grader_capsule_sha256"],
            private_root=private_root,
        )
        receipt = test_module.current_turn.run_current_question_turn(
            root,
            prepared["context"],
            feedback_text=(
                f"prebuild-v2-cs408-{ordinal} synthetic bounded feedback."
            ),
            private_evaluation=capsule["evaluation_evidence"],
            private_root=private_root,
        )
        receipts.append(receipt)

    if str(backend_root / "lib") not in sys.path:
        sys.path.insert(0, str(backend_root / "lib"))
    from preprocessor_core import Cs408Adapter
    controlled = backend_root / "schemas/luna-cs408-controlled-contract-v3.json"
    adapter = Cs408Adapter(
        {
            "enabled": True,
            "repo_root": str(root),
            "status_script": str(capture_script),
            "python_path": sys.executable,
            "adapter_version": "prebuild-real-cs408-v2",
            "deep_v2_enabled": True,
            "processing_contract": {
                "processing_contract_sha256": "9" * 64,
                "status_script_sha256": _sha_file(capture_script),
                "controlled_contract_sha256": _sha_file(controlled),
            },
            "private_current_question_root": str(private_root),
            "controlled_contract_path": str(controlled),
            "max_bundle_bytes": 262144,
        },
        {"status_timeout_seconds": 30},
    )
    candidates = adapter.candidates(adapter.status(None))
    by_id = {row.capture_id: row for row in candidates}
    missing = [
        receipt["capture_id"] for receipt in receipts
        if receipt["capture_id"] not in by_id
    ]
    if missing:
        raise AssertionError(
            {
                "cs408_candidates_missing": missing,
                "available": sorted(by_id),
                "errors": copy.deepcopy(adapter.candidate_errors),
                "diagnostics": copy.deepcopy(adapter.candidate_diagnostics),
            }
        )
    rows = [
        {
            "subject": "cs408", "capture_id": receipt["capture_id"],
            "candidate": by_id[receipt["capture_id"]],
            "producer_entrypoint": str(root / "scripts/managed_408_current_turn.py"),
            "producer_receipt": {
                **receipt, "prebuild_marker": f"prebuild-v2-cs408-{ordinal}"
            },
        }
        for ordinal, receipt in enumerate(receipts, 1)
    ]
    test_module.tearDownModule()
    return rows


def _write_english_source(root: Path, ordinal: int) -> tuple[Path, str, str, str]:
    source_id = f"PREBUILD-EN-{ordinal:02d}"
    slug = f"prebuild-en-{ordinal:02d}"
    sentence = f"Synthetic sentence {ordinal} preserves a bounded context."
    handoff = root / "raw/fixtures" / slug / "pipeline_handoff.json"
    payload = handoff.parent / "source/practice-safe.md"
    payload.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        f"# {slug} Practice Safe\r\nsource_id: {source_id}  \r\n\r\n"
        f"## Article\r\n\r\n{sentence}\t\r\n"
    ).encode("utf-8")
    payload.write_bytes(raw)
    canonical = "\n".join(
        line.rstrip() for line in raw.decode("utf-8").replace("\r\n", "\n").split("\n")
    ).rstrip("\n").encode("utf-8")
    source_hash = hashlib.sha256(canonical).hexdigest()
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_bytes(
        _canonical(
            {
                "schema_version": "english-learning-pipeline-handoff-v1",
                "source_id": source_id,
                "source_hash": f"sha256:{source_hash}",
                "canonical_payload": {
                    "path": "source/practice-safe.md",
                    "normalization": "UTF-8; LF line endings; trailing whitespace removed per line; no final LF",
                    "visibility": "practice_safe",
                },
                "units": [],
            }
        )
    )
    article = root / "articles" / f"{slug}.md"
    article.parent.mkdir(parents=True, exist_ok=True)
    article.write_text(
        f"# {slug}\n\n## 基本信息\n\n- source_id：{source_id}\n"
        f"- source_hash：sha256:{source_hash}\n"
        f"- pipeline_handoff：`raw/fixtures/{slug}/pipeline_handoff.json`\n\n"
        f"## 原文全文\n\n{sentence}\n",
        encoding="utf-8",
    )
    return article, source_id, source_hash, sentence


def _seed_english(root: Path, backend_root: Path) -> list[dict[str, Any]]:
    cli = root / "english_pipeline/cli.py"
    cli_command = [sys.executable, "-m", "english_pipeline.cli"]
    state = root / "intake"
    if state.exists():
        shutil.rmtree(state)
    attestation_root = root / "producer-attestations"
    if attestation_root.exists():
        shutil.rmtree(attestation_root)
    receipts: list[dict[str, Any]] = []
    for ordinal in range(1, 4):
        article, source_id, source_hash, sentence = _write_english_source(root, ordinal)
        raw_path = root / ".prebuild-input" / f"english-raw-{ordinal}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(
            _canonical(
                {
                    "event_type": "english_raw_dialogue_turn_v1",
                    "idempotency_key": f"prebuild-en-raw-{ordinal}",
                    "occurred_at": f"2026-08-24T01:0{ordinal}:00Z",
                    "messages": [
                        {
                            "role": "user", "message_id": f"u-{ordinal}",
                            "timestamp": f"2026-08-24T01:0{ordinal}:00Z",
                            "content": "Synthetic sentence evidence.",
                        },
                        {
                            "role": "assistant", "message_id": f"a-{ordinal}",
                            "timestamp": f"2026-08-24T01:0{ordinal}:30Z",
                            "content": "Synthetic bounded reply.", "complete": True,
                        },
                    ],
                    "attachments": [],
                    "context_identity": {
                        "conversation_id": "prebuild", "thread_id": f"t-{ordinal}",
                        "workspace_id": "prebuild", "assistant_context_id": f"a-{ordinal}",
                    },
                    "resolution_status": "resolved",
                }
            )
        )
        raw_receipt = _run_json(
            [*cli_command, "capture-raw-turn", "--repo-root", str(root),
             "--state-dir", str(state), "--input-json", str(raw_path)],
            cwd=root,
        )
        receipt = _run_json(
            [
                *cli_command, "capture", "--repo-root", str(root),
                "--state-dir", str(state), "--idempotency-key", f"prebuild-en-{ordinal}",
                "--source-id", source_id, "--source-article", str(article.relative_to(root)),
                "--article-sha256", source_hash, "--sentence-id", "S01",
                "--source-sentence", sentence, "--occurred-at", f"2026-08-24T02:0{ordinal}:00Z",
                "--parent-raw-capture-id", str(raw_receipt["capture_id"]),
                "--first-translation", "Synthetic first translation",
                "--user-evidence", "bounded is unfamiliar", "--evidence-state", "unknown_observed",
                "--evidence-kind", "unknown", "--evidence-origin", "synthetic_fixture",
                "--answer-protection", "practice_safe", "--quick-flush",
            ],
            cwd=root,
        )
        receipts.append(receipt)

    generated_descriptors = sorted(
        (state / "producer-binding").glob("producer-binding-*.json")
    )
    if len(generated_descriptors) != 1:
        raise AssertionError("English Producer descriptor was not materialized")
    deployed_descriptor = root / "schema/english_pipeline/producer-binding-v1.json"
    deployed_descriptor.parent.mkdir(parents=True, exist_ok=True)
    deployed_descriptor.write_bytes(generated_descriptors[0].read_bytes())

    adapter_fixtures = root / "scripts"
    adapter_fixtures.mkdir(parents=True, exist_ok=True)
    foundation = adapter_fixtures / "select_bbdc_foundation.py"
    foundation.write_text(
        """#!/usr/bin/env python3
import hashlib, json, sys
args=sys.argv[1:]
def arg(name, default=''):
    return args[args.index(name)+1] if name in args else default
item=arg('--item')
digest=lambda value: hashlib.sha256(value.encode()).hexdigest()
print(json.dumps({
 'schema':'bbdc_foundation_packet_v1','read_only':True,
 'eligibility':{'bank_match':'new','bank_ids':[],'mastered_check':'clear'},
 'relationship_graph':{'status':'loaded'},
 'foundation':{'current_user_foundation':[{'wording':item,'evidence_kind':'unknown'}],
  'user_old_word_candidates':[],'writing_pattern_candidates':[],
  'writing_vocab_candidates':[],'syllabus_vocab_candidates':[],
  'double_hits':[],'optional_sp_candidates':[]},
 'validation':{'degradation_path':[],'reference_gap':[],
  'required_final_checks':['source_separation']},
 'formal_input_hashes_before':{'bank/master_bank.csv':digest('master'),
  'bank/mastered_items.csv':digest('mastered'),
  'bank/sentence_patterns.md':digest('patterns')},
 'formal_sources_unchanged':True,'formal_writeback':'none'}))
""",
        encoding="utf-8",
    )
    review_status = adapter_fixtures / "build_review_status_proposals.py"
    review_status.write_text(
        """#!/usr/bin/env python3
import argparse, hashlib, json
p=argparse.ArgumentParser(); p.add_argument('--repo-root'); p.add_argument('--state-dir'); p.add_argument('--study-date',required=True); a=p.parse_args()
print(json.dumps({'schema_version':'english_review_status_proposals_v2',
 'study_date':a.study_date,'capture_event_ids':[],
 'daily_explicit_unknown_terms':[],'review_exclusion_proposals':[],
 'reactivation_proposals':[],'needs_user_decision':[],
 'source_hashes':{'prebuild':hashlib.sha256(b'prebuild').hexdigest()},
 'formal_write_count':0}))
""",
        encoding="utf-8",
    )
    (adapter_fixtures / "build_old_word_memory_curve_index.py").write_text(
        "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'status':'PASS','formal_write_count':0}))\n",
        encoding="utf-8",
    )

    if str(backend_root / "lib") not in sys.path:
        sys.path.insert(0, str(backend_root / "lib"))
    from preprocessor_core import EnglishAdapter
    adapter = EnglishAdapter(
        {
            "enabled": True,
            "repo_root": str(root),
            "status_script": str(root / "scripts/english_learning_pipeline.py"),
            "python_path": sys.executable,
            "adapter_version": "prebuild-real-english-v2",
            "state_dir": str(state),
            "candidate_root": str(state / "candidates"),
            "candidate_schema": str(root / "schema/english_pipeline/luna-candidate-v2.schema.json"),
            "foundation_script": str(foundation),
            "review_status_script": str(review_status),
            "microbatch_capture_count": 5,
            "quiet_seconds": 180,
            "max_events_per_scan": 100,
            "max_event_bytes": 262144,
            "max_batch_bytes": 1048576,
            "max_foundation_items": 24,
            "max_mastered_matches": 24,
            "max_master_bank_matches": 24,
            "max_sentence_pattern_matches": 24,
            "max_formal_prefetch_bytes": 262144,
            "max_candidate_documents": 100,
            "processing_contract": {
                "processing_contract_sha256": "9" * 64,
                "requested_model": "gpt-5.6-luna",
                "requested_reasoning_effort": "max",
            },
        },
        {"status_timeout_seconds": 30},
    )
    candidates = adapter.candidates(adapter.status(None))
    if len(candidates) != 3:
        raise AssertionError({"english_candidate_count": len(candidates), "errors": adapter.candidate_errors})
    by_event = {
        event_id: candidate
        for candidate in candidates
        for event_id in candidate.input_binding["capture_event_ids"]
    }
    return [
        {
            "subject": "english",
            "capture_id": by_event[receipt["capture_id"]].capture_id,
            "candidate": by_event[receipt["capture_id"]],
            "producer_entrypoint": str(cli),
            "producer_receipt": {
                **receipt,
                "source_capture_id": receipt["capture_id"],
                "background_capture_id": by_event[
                    receipt["capture_id"]
                ].capture_id,
                "prebuild_marker": f"PREBUILD-EN-{ordinal:02d}",
            },
        }
        for ordinal, receipt in enumerate(receipts, 1)
    ]


def produce_and_scan(
    *, backend_root: Path, isolated_roots: dict[str, Path], trial_root: Path,
) -> list[dict[str, Any]]:
    rows = [
        *_seed_math(isolated_roots["math"], backend_root),
        *_seed_cs408(isolated_roots["cs408"], backend_root, trial_root),
        *_seed_english(isolated_roots["english"], backend_root),
    ]
    if len(rows) != 9:
        raise AssertionError("real Producer fixture did not create nine Candidates")
    return rows


def _pin_prebuild_terra_branch_schema(schema_path: Path) -> None:
    """Constrain only the nine-task trial to exactly three Luna branches."""

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    proposed = (
        schema.get("properties", {}).get("proposed_branches")
        if isinstance(schema, dict)
        else None
    )
    if (
        not isinstance(proposed, dict)
        or proposed.get("type") != "array"
        or proposed.get("minItems") != 3
        or proposed.get("maxItems") not in {3, 4}
    ):
        raise AssertionError("Terra initial branch schema is not recognized")
    proposed["minItems"] = 3
    proposed["maxItems"] = 3
    schema_path.write_bytes(_canonical(schema))


def build_runtime_config(
    *, backend_root: Path, trial_root: Path, isolated_roots: dict[str, Path],
    codex_path: Path, mcp_python: Path,
) -> dict[str, Any]:
    """Build one portable MCP/plugin closure around the isolated subject roots."""

    release_id = "a" * 64
    runtime = trial_root / "runtime"
    source_release = runtime / "releases" / release_id
    source_release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        backend_root, source_release,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "component-lock.json", "components.json"),
    )
    _pin_prebuild_terra_branch_schema(
        source_release / "schemas/terra-initial-draft-v1.json"
    )
    mcp_source = Path(
        os.environ.get("STUDY_READ_MCP_SOURCE_ROOT", str(backend_root.parent / "local-study-read-mcp"))
    ).resolve()
    mcp_git: dict[str, str] = {}
    for key, command in {
        "git_head": ["git", "rev-parse", "HEAD"],
        "git_origin": ["git", "remote", "get-url", "origin"],
        "git_status_porcelain": ["git", "status", "--porcelain"],
    }.items():
        completed = subprocess.run(
            command, cwd=mcp_source, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError("canonical Shared MCP git binding unavailable")
        mcp_git[key] = completed.stdout.strip()
    if mcp_git["git_status_porcelain"]:
        raise AssertionError("canonical Shared MCP source worktree is dirty")
    mcp_git["path"] = str(mcp_source)
    mcp_source_entry = str(mcp_source / "src")
    sys.path.insert(0, mcp_source_entry)
    try:
        helper = _load(
            "prebuild_shared_mcp_fixture_helpers",
            mcp_source / "tests/helpers.py",
        )
        mcp_subjects = helper.make_fixture(
            trial_root / "mcp-subject-data"
        )
    finally:
        if sys.path[0] == mcp_source_entry:
            sys.path.pop(0)
    selected = {
        "math": (
            "数学一回滚复习系统/学习前5题记录.jsonl",
            "数学一回滚复习系统/复习记录.jsonl",
            "错题知识网络/知识点库.md",
            "错题知识网络/生成/wrong_questions.json",
            "错题知识网络/错题卡/GS-001_fixture.md",
        ),
        "cs408": (
            "知识点标签表.md", "节点总表.md", "关系规则.md", "关系边表.md",
            "wiki/study_vaults/408-full/StudyVault/01-DS/DS01-01-测试.md",
            "wiki/study_vaults/408-full/state/morning-review/MR-2026-08-07-fixture/state.json",
            "wiki/study_vaults/408-full/state/review-loop/hot-state/databases/RHS-AABBCCDD.sqlite3",
            "wiki/study_vaults/408-full/state/review-loop/hot-state/manifest.json",
            "wiki/study_vaults/408-full/state/review-loop/events.jsonl",
            "wiki/study_vaults/408-full/manifest.json",
        ),
        "english": (
            "bank/mastered_items.csv", "bank/master_bank.csv",
            "bank/sentence_patterns.md",
            "articles/2026-08-07-fixture-learning-page.md",
            "raw/articles/exam-reading-corpus/2020/text-1.json",
            "raw/articles/2026-08-07-fixture/pack/dataset/pipeline_handoff.json",
        ),
    }
    fixture_roots = {
        "math": mcp_subjects.math_root,
        "cs408": mcp_subjects.cs408_root,
        "english": mcp_subjects.english_root,
    }
    for subject, relatives in selected.items():
        for relative in relatives:
            source = fixture_roots[subject] / relative
            target = isolated_roots[subject] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    marker_cards = isolated_roots["math"] / "错题知识网络/错题卡"
    marker_cards.mkdir(parents=True, exist_ok=True)
    for ordinal in range(1, 4):
        (marker_cards / f"GS-90{ordinal}_prebuild.md").write_text(
            f"---\nid: GS-90{ordinal}\n---\nprebuild:v2:math:{ordinal}\n",
            encoding="utf-8",
        )
        cs_marker = (
            isolated_roots["cs408"]
            / f"wiki/study_vaults/408-full/StudyVault/PREBUILD-C{ordinal}.md"
        )
        cs_marker.parent.mkdir(parents=True, exist_ok=True)
        cs_marker.write_text(
            f"prebuild-v2-cs408-{ordinal}\n", encoding="utf-8"
        )
        en_marker = isolated_roots["english"] / f"articles/PREBUILD-EN-0{ordinal}.md"
        en_marker.parent.mkdir(parents=True, exist_ok=True)
        en_marker.write_text(
            f"PREBUILD-EN-0{ordinal}\n", encoding="utf-8"
        )
    built = _run_json(
        [sys.executable, str(mcp_source / "scripts/build_release.py"),
         "--release-base", str(trial_root / "mcp-releases")],
        cwd=mcp_source,
    )
    mcp_root = Path(str(built["release_dir"])).resolve()
    (source_release / "release.json").write_bytes(
        _canonical(
            {
                "schema_version": "study-intake-preprocessor-release-v2",
                "release_id": release_id,
                "component_inventory": {},
                "formal_write_count": 0,
            }
        )
    )
    generator = source_release / "plugin/kaoyan-study-intake/scripts/generate_manifests.py"
    generated = subprocess.run(
        [
            sys.executable, str(generator),
            "--math-root", str(isolated_roots["math"]),
            "--cs408-root", str(isolated_roots["cs408"]),
            "--english-root", str(isolated_roots["english"]),
            "--mcp-root", str(mcp_root),
            "--mcp-python-executable", str(mcp_python),
        ],
        cwd=generator.parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if generated.returncode != 0:
        raise AssertionError("portable plugin manifest generation failed: " + generated.stderr)
    (source_release / "release.json").write_bytes(
        _canonical(
            {
                "schema_version": "study-intake-preprocessor-release-v2",
                "release_id": release_id,
                "component_inventory": {},
                "formal_write_count": 0,
            }
        )
    )
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "packages/objects").mkdir(parents=True, exist_ok=True)
    (runtime / "current").symlink_to(source_release)
    key = runtime / "dispatch/state/authority.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    replacements = {
        "${RUNTIME_DATA_ROOT}": str(runtime),
        "${RELEASE_ROOT}": str(source_release),
        "${MCP_PYTHON_EXECUTABLE}": str(mcp_python),
        "${MCP_ROOT}": str(mcp_root),
        "${CODEX_EXECUTABLE}": str(codex_path),
        "${PYTHON_EXECUTABLE}": str(Path(sys.executable).resolve()),
        "${MATH_ROOT}": str(isolated_roots["math"]),
        "${CS408_ROOT}": str(isolated_roots["cs408"]),
        "${ENGLISH_ROOT}": str(isolated_roots["english"]),
    }
    raw_config = (source_release / "config.example.json").read_text(
        encoding="utf-8"
    )
    for token, value in replacements.items():
        raw_config = raw_config.replace(token, value)
    config = json.loads(raw_config)
    config["execution_mode"] = "hosted_synthetic"
    config["hosted_synthetic_trial"] = {
        "enabled": True,
        "synthetic_only": True,
        "capture_source_kind": "synthetic",
        "runtime_root": str(trial_root),
        "subject_roots": {
            key: str(value) for key, value in isolated_roots.items()
        },
        "formal_write_count": 0,
    }
    config["analysis_package_v2"]["physical_branch_slots"] = 3
    config["branch_scheduler"]["configured_maximum_active_branches"] = 27
    config["worker"]["model_timeout_seconds"] = 3600
    config["worker"]["max_jobs_per_scan"] = 9
    config_path = source_release / "config.json"
    config_path.write_bytes(_canonical(config))
    return {
        **config,
        "authority_release_id": release_id,
        "authority_generation_id": "b" * 64,
        "subject_repo_roots": {
            key: str(value) for key, value in isolated_roots.items()
        },
        "math_repo_root": str(isolated_roots["math"]),
        "codex_path": str(codex_path),
        "task_model_temp_root": str(runtime / "state/model-output"),
        "task_context_root": str(runtime),
        "_trial_mcp_root": str(mcp_root),
        "_trial_mcp_release_id": built["release_id"],
        "_trial_source_release": str(source_release),
        "_trial_root": str(trial_root),
        "_trial_mcp_source_binding": mcp_git,
        "_trial_config_path": str(config_path),
    }
