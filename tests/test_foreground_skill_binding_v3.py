from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from skill_binding_contract import (
    SkillBindingError,
    bind_candidate_producer_attestations,
    build_processing_binding_v3,
)


@dataclass(frozen=True)
class CandidateFixture:
    subject: str
    capture_id: str
    recorded_at: str
    input_binding: dict
    model_input: dict
    input_fingerprint: str


_SUBJECT_FIXTURE_LAYOUT = {
    "math": {
        "canonical_root": "kaoyan-math",
        "descriptor": Path(
            "数学一回滚复习系统/schema/producer-binding-v1.json"
        ),
        "helper": Path(
            "数学一回滚复习系统/scripts/producer_binding_attestation.py"
        ),
        "producer": (
            Path("数学一回滚复习系统/scripts/quick_intake.py"),
            Path("数学一回滚复习系统/scripts/producer_binding_attestation.py"),
        ),
        "skill": Path(
            "codex-skill-sources/kaoyan-math-wrong-intake/SKILL.md"
        ),
        "attestation_root": "数学一回滚复习系统/快速入库绑定证明",
    },
    "cs408": {
        "canonical_root": "kaoyan-408",
        "descriptor": Path("schema/producer-binding-v1.json"),
        "helper": Path("scripts/producer_binding_attestation_408.py"),
        "producer": (
            Path("scripts/intake_fact_capture_408.py"),
            Path("scripts/producer_binding_attestation_408.py"),
        ),
        "skill": Path("codex-skill-sources/kaoyan-408-wrong-intake/SKILL.md"),
        "attestation_root": ".producer-binding-attestations",
    },
    "english": {
        "canonical_root": "kaoyan-english",
        "descriptor": Path("schema/english_pipeline/producer-binding-v1.json"),
        "helper": Path("english_pipeline/producer_binding_attestation.py"),
        "producer": (
            Path("english_pipeline/events.py"),
            Path("english_pipeline/producer_binding_attestation.py"),
        ),
        "skill": Path(
            "codex-skill-sources/kaoyan-english-intensive-reading/SKILL.md"
        ),
        "attestation_root": "producer-attestations",
    },
}


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_canonical_or_synthetic(
    source: Path, destination: Path, *, label: str
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        shutil.copy2(source, destination)
    elif destination.suffix == ".json":
        destination.write_text('{"synthetic": true}\n', encoding="utf-8")
    else:
        destination.write_text(f"# synthetic canonical fixture: {label}\n", encoding="utf-8")


def _build_subject_fixture(base: Path, subject: str, label: str) -> dict[str, Path]:
    layout = _SUBJECT_FIXTURE_LAYOUT[subject]
    root = (base / label / subject).resolve()
    canonical_root = ROOT.parent / str(layout["canonical_root"])

    producer_paths: list[Path] = []
    for relative in layout["producer"]:  # type: ignore[union-attr]
        destination = root / relative
        _copy_canonical_or_synthetic(
            canonical_root / relative,
            destination,
            label=f"{subject}:{relative}",
        )
        producer_paths.append(destination)

    authoritative = root / "skills/authoritative/SKILL.md"
    installed = root / "skills/installed/SKILL.md"
    skill_relative = layout["skill"]  # type: ignore[assignment]
    _copy_canonical_or_synthetic(
        canonical_root / skill_relative,
        authoritative,
        label=f"{subject}:authoritative-skill",
    )
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(authoritative, installed)

    contract = root / "contracts/capture-contract.json"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text('{"synthetic": true}\n', encoding="utf-8")
    contract_rows = [{"path": str(contract), "sha256": _sha256_file(contract)}]
    source_rows = [
        {"path": str(path), "sha256": _sha256_file(path)}
        for path in producer_paths
    ]
    core = {
        "schema_version": "producer_binding_descriptor_v1",
        "subject": subject,
        "attestation_required_after": "2026-08-17T00:00:00+00:00",
        "foreground_skill": {
            "authoritative_path": str(authoritative),
            "authoritative_sha256": _sha256_file(authoritative),
            "installed_path": str(installed),
            "installed_sha256": _sha256_file(installed),
        },
        "producer": {
            "source_files": source_rows,
            "source_closure_sha256": hashlib.sha256(
                _canonical_bytes(source_rows)
            ).hexdigest(),
        },
        "capture_contract": {
            "files": contract_rows,
            "files_sha256": hashlib.sha256(
                _canonical_bytes(contract_rows)
            ).hexdigest(),
        },
        "attestation_relative_root": str(layout["attestation_root"]),
        "formal_write_count": 0,
    }
    descriptor = root / layout["descriptor"]  # type: ignore[arg-type]
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_bytes(
        _canonical_bytes(
            {**core, "descriptor_content_sha256": hashlib.sha256(
                _canonical_bytes(core)
            ).hexdigest()}
        )
    )
    return {
        "root": root,
        "descriptor": descriptor,
        "helper": root / layout["helper"],  # type: ignore[arg-type]
    }


class ForegroundSkillBindingV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = _build_subject_fixture(
            Path(self.temp.name).resolve(), "math", "primary"
        )
        self.repo = self.fixture["root"]
        self.descriptor = self.fixture["descriptor"]
        value = json.loads(self.descriptor.read_text(encoding="utf-8"))
        self.binding = {
            "descriptor_path": str(self.descriptor),
            "descriptor_sha256": hashlib.sha256(self.descriptor.read_bytes()).hexdigest(),
            "attestation_required_after": value["attestation_required_after"],
            "producer_source_closure_sha256": value["producer"]["source_closure_sha256"],
            "foreground_skill_sha256": value["foreground_skill"]["installed_sha256"],
        }
        self.lock = self.repo / "component-lock.json"
        self.lock.write_text(
            json.dumps(
                {
                    "foreground_capture_contracts": {"math": self.binding},
                    "fixture_contracts": {
                        "math": {"profile": "math-v1", "sha256": "6" * 64}
                    },
                    "skills": {
                        "background-math-processing": {
                            "version": "4.0.1",
                            "sha256": "3" * 64,
                        },
                        "multi-agent-read-orchestrate": {
                            "version": "1.1.0",
                            "sha256": "4" * 64,
                        },
                    },
                    "multi_agent": {
                        "roles": {
                            "orchestrator": {"model": "gpt-5.6-terra"},
                            "reader": {"model": "gpt-5.6-luna"},
                            "critical_reviewer": {"model": "gpt-5.6-terra"},
                        }
                    },
                    "agent_configs": {"fixture": "5" * 64},
                    "luna_mcp_servers": {
                        "math": {
                            "server_name": "kaoyan_math_read",
                            "launch_mode": "subject-server",
                        }
                    },
                    "formal_write_count": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.config = {
            "execution_mode": "fixture",
            "adapters": {"math": {"repo_root": str(self.repo)}},
            "processing_plugin": {"component_lock_path": str(self.lock)},
        }
        self.capture_id = "MFI-CAP-fixture0000000000000000"
        self.content_hash = "1" * 64
        self.candidate = CandidateFixture(
            subject="math",
            capture_id=self.capture_id,
            recorded_at="2026-08-17T07:00:00+00:00",
            input_binding={"original_content_hash": self.content_hash},
            model_input={},
            input_fingerprint="2" * 64,
        )

    def _publish_sidecar(self) -> Path:
        module_path = self.fixture["helper"]
        spec = importlib.util.spec_from_file_location("math_attest_test", module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.publish_attestation(
            descriptor_path=self.descriptor,
            repo_root=self.repo,
            subject="math",
            capture_id=self.capture_id,
            capture_content_sha256=self.content_hash,
            recorded_at=self.candidate.recorded_at,
        )
        return Path(result["attestation_path"])

    @staticmethod
    def _rewrite_descriptor(path: Path, mutate: object) -> dict:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)  # type: ignore[operator]
        core = {
            key: item
            for key, item in value.items()
            if key != "descriptor_content_sha256"
        }
        value["descriptor_content_sha256"] = hashlib.sha256(
            (
                json.dumps(
                    core,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return value

    def test_post_threshold_candidate_requires_and_binds_attestation(self) -> None:
        sidecar = self._publish_sidecar()
        self.assertTrue(sidecar.is_file())
        rebound = bind_candidate_producer_attestations(self.config, self.candidate)
        self.assertNotEqual(rebound.input_fingerprint, self.candidate.input_fingerprint)
        bundle = rebound.input_binding["producer_binding_attestation_bundle"]
        self.assertEqual(bundle["subject"], "math")
        self.assertEqual(bundle["formal_write_count"], 0)
        self.assertEqual(len(bundle["attestation_sha256s"]), 1)
        self.assertEqual(
            rebound.input_binding["processing_binding_v3"]["schema_version"],
            "processing_binding_v3",
        )

    def test_missing_attestation_fails_before_model(self) -> None:
        with self.assertRaises(SkillBindingError) as caught:
            bind_candidate_producer_attestations(self.config, self.candidate)
        self.assertEqual(caught.exception.code, "foreground_skill_binding_mismatch")

    def test_authoritative_installed_mismatch_fails_for_every_subject(self) -> None:
        for index, subject in enumerate(("math", "cs408", "english")):
            with self.subTest(subject=subject):
                source_fixture = _build_subject_fixture(
                    Path(self.temp.name).resolve(), subject, f"parity-source-{index}"
                )
                source = source_fixture["descriptor"]
                relative = _SUBJECT_FIXTURE_LAYOUT[subject]["descriptor"]
                repo = Path(self.temp.name).resolve() / f"parity-{index}"
                descriptor = repo / relative
                descriptor.parent.mkdir(parents=True)
                shutil.copyfile(source, descriptor)
                authoritative = repo / "authoritative-SKILL.md"
                installed = repo / "installed-SKILL.md"
                authoritative.write_text("authoritative\n", encoding="utf-8")
                installed.write_text("installed-drift\n", encoding="utf-8")

                def mutate(value: dict) -> None:
                    skill = value["foreground_skill"]
                    skill["authoritative_path"] = str(authoritative)
                    skill["authoritative_sha256"] = hashlib.sha256(
                        authoritative.read_bytes()
                    ).hexdigest()
                    skill["installed_path"] = str(installed)
                    skill["installed_sha256"] = hashlib.sha256(
                        installed.read_bytes()
                    ).hexdigest()

                self._rewrite_descriptor(descriptor, mutate)
                candidate = CandidateFixture(
                    subject=subject,
                    capture_id=f"CAP-PARITY-{index}",
                    recorded_at="2026-08-17T07:00:00+00:00",
                    input_binding={},
                    model_input={},
                    input_fingerprint="e" * 64,
                )
                with self.assertRaises(SkillBindingError) as caught:
                    bind_candidate_producer_attestations(
                        {
                            "execution_mode": "fixture",
                            "adapters": {subject: {"repo_root": str(repo)}},
                            "processing_plugin": {
                                "component_lock_path": str(repo / "unused.json")
                            },
                        },
                        candidate,
                    )
                self.assertEqual(
                    caught.exception.code,
                    "foreground_skill_binding_mismatch",
                )

    def test_producer_source_drift_fails_before_attestation(self) -> None:
        producer_copy = self.repo / "producer-copy.py"
        producer_copy.write_text("stable\n", encoding="utf-8")

        def mutate(value: dict) -> None:
            row = value["producer"]["source_files"][0]
            row["path"] = str(producer_copy)
            row["sha256"] = hashlib.sha256(producer_copy.read_bytes()).hexdigest()
            source_files = value["producer"]["source_files"]
            value["producer"]["source_closure_sha256"] = hashlib.sha256(
                (
                    json.dumps(
                        source_files,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()

        self._rewrite_descriptor(self.descriptor, mutate)
        producer_copy.write_text("drifted\n", encoding="utf-8")
        with self.assertRaises(SkillBindingError) as caught:
            bind_candidate_producer_attestations(self.config, self.candidate)
        self.assertEqual(caught.exception.code, "producer_contract_mismatch")

    def test_capture_contract_drift_fails_before_attestation(self) -> None:
        contract_copy = self.repo / "capture-contract.md"
        contract_copy.write_text("stable\n", encoding="utf-8")

        def mutate(value: dict) -> None:
            rows = [
                {
                    "path": str(contract_copy),
                    "sha256": hashlib.sha256(contract_copy.read_bytes()).hexdigest(),
                }
            ]
            value["capture_contract"]["files"] = rows
            value["capture_contract"]["files_sha256"] = hashlib.sha256(
                (
                    json.dumps(
                        rows,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()

        self._rewrite_descriptor(self.descriptor, mutate)
        contract_copy.write_text("drifted\n", encoding="utf-8")
        with self.assertRaises(SkillBindingError) as caught:
            bind_candidate_producer_attestations(self.config, self.candidate)
        self.assertEqual(caught.exception.code, "capture_contract_mismatch")

    def test_historical_candidate_is_not_backfilled(self) -> None:
        historical = CandidateFixture(
            **{
                **self.candidate.__dict__,
                "recorded_at": "2026-08-16T07:00:00+00:00",
            }
        )
        rebound = bind_candidate_producer_attestations(self.config, historical)
        self.assertIs(rebound, historical)
        sidecar_root = self.repo / "数学一回滚复习系统" / "快速入库生产绑定"
        self.assertFalse(sidecar_root.exists())

    def test_processing_binding_v3_closes_every_role(self) -> None:
        component_lock = {
            "foreground_capture_contracts": {"math": self.binding},
            "skills": {
                "background-math-processing": {"version": "3.1.1", "sha256": "3" * 64},
                "multi-agent-read-orchestrate": {"version": "1.1.0", "sha256": "4" * 64},
            },
            "multi_agent": {
                "roles": {
                    "orchestrator": {"model": "gpt-5.6-terra"},
                    "reader": {"model": "gpt-5.6-luna"},
                    "critical_reviewer": {"model": "gpt-5.6-terra"},
                }
            },
            "agent_configs": {"fixture": "5" * 64},
            "luna_mcp_servers": {"math": {"server_name": "kaoyan_math_read", "launch_mode": "subject-server"}},
        }
        value = build_processing_binding_v3(
            subject="math",
            component_lock=component_lock,
            fixture_contract={"profile": "math-v1", "sha256": "6" * 64},
            scanner_adapter={"id": "math-pending-v1", "sha256": "7" * 64},
        )
        self.assertEqual(value["schema_version"], "processing_binding_v3")
        self.assertEqual(value["terra_agent_contract"]["model"], "gpt-5.6-terra")
        self.assertEqual(value["luna_reader_contract"]["model"], "gpt-5.6-luna")
        self.assertEqual(value["formal_write_count"], 0)
        self.assertRegex(value["binding_sha256"], r"^[0-9a-f]{64}$")

    def test_cs408_and_english_post_threshold_scanner_bindings(self) -> None:
        cases = [
            {
                "subject": "cs408",
                "candidate_id": "CAP-20260817-fixture",
                "unit_id": "CAP-20260817-fixture",
                "content_hash": "7" * 64,
                "input_binding": {
                    "payload_sha256": "7" * 64,
                    "adapter_version": "cs408-awaiting-curation-v2",
                },
                "model_input": {},
                "mcp": "kaoyan_cs408_read",
            },
            {
                "subject": "english",
                "candidate_id": "EN-20260817-fixture",
                "unit_id": "EVT-20260817-FIXTURE",
                "content_hash": "8" * 64,
                "input_binding": {
                    "capture_event_sha256": {
                        "EVT-20260817-FIXTURE": "8" * 64
                    },
                    "adapter_version": "english-microbatch-v1",
                },
                "model_input": {
                    "batch_events": [
                        {
                            "event_id": "EVT-20260817-FIXTURE",
                            "occurred_at": "2026-08-17T07:00:00+00:00",
                        }
                    ]
                },
                "mcp": "kaoyan_english_read",
            },
        ]
        for index, case in enumerate(cases):
            with self.subTest(subject=case["subject"]):
                fixture = _build_subject_fixture(
                    Path(self.temp.name).resolve(),
                    case["subject"],
                    f"scanner-{index}",
                )
                source_descriptor = fixture["descriptor"]
                helper = fixture["helper"]
                repo = Path(self.temp.name).resolve() / f"repo-{index}"
                descriptor_relative = _SUBJECT_FIXTURE_LAYOUT[case["subject"]][
                    "descriptor"
                ]
                descriptor = repo / descriptor_relative
                descriptor.parent.mkdir(parents=True)
                shutil.copyfile(source_descriptor, descriptor)
                descriptor_value = json.loads(
                    descriptor.read_text(encoding="utf-8")
                )
                contract = {
                    "descriptor_path": str(descriptor),
                    "descriptor_sha256": hashlib.sha256(
                        descriptor.read_bytes()
                    ).hexdigest(),
                    "attestation_required_after": descriptor_value[
                        "attestation_required_after"
                    ],
                    "producer_source_closure_sha256": descriptor_value[
                        "producer"
                    ]["source_closure_sha256"],
                    "foreground_skill_sha256": descriptor_value[
                        "foreground_skill"
                    ]["installed_sha256"],
                }
                lock = repo / "component-lock.json"
                lock.write_text(
                    json.dumps(
                        {
                            "foreground_capture_contracts": {
                                case["subject"]: contract
                            },
                            "fixture_contracts": {
                                case["subject"]: {
                                    "profile": f"{case['subject']}-v1",
                                    "sha256": "9" * 64,
                                }
                            },
                            "skills": {
                                f"background-{case['subject']}-processing": {
                                    "version": "4.0.1",
                                    "sha256": "a" * 64,
                                },
                                "multi-agent-read-orchestrate": {
                                    "version": "1.1.0",
                                    "sha256": "b" * 64,
                                },
                            },
                            "multi_agent": {
                                "roles": {
                                    "orchestrator": {
                                        "model": "gpt-5.6-terra"
                                    },
                                    "reader": {"model": "gpt-5.6-luna"},
                                    "critical_reviewer": {
                                        "model": "gpt-5.6-terra"
                                    },
                                }
                            },
                            "agent_configs": {"fixture": "c" * 64},
                            "luna_mcp_servers": {
                                case["subject"]: {
                                    "server_name": case["mcp"],
                                    "launch_mode": "subject-server",
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                spec = importlib.util.spec_from_file_location(
                    f"attestation_{index}", helper
                )
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.publish_attestation(
                    descriptor_path=descriptor,
                    repo_root=repo,
                    subject=case["subject"],
                    capture_id=case["unit_id"],
                    capture_content_sha256=case["content_hash"],
                    recorded_at="2026-08-17T07:00:00+00:00",
                )
                candidate = CandidateFixture(
                    subject=case["subject"],
                    capture_id=case["candidate_id"],
                    recorded_at="2026-08-17T07:00:00+00:00",
                    input_binding=case["input_binding"],
                    model_input=case["model_input"],
                    input_fingerprint="d" * 64,
                )
                rebound = bind_candidate_producer_attestations(
                    {
                        "execution_mode": "fixture",
                        "adapters": {
                            case["subject"]: {"repo_root": str(repo)}
                        },
                        "processing_plugin": {
                            "component_lock_path": str(lock)
                        },
                    },
                    candidate,
                )
                self.assertEqual(
                    rebound.input_binding["processing_binding_v3"]["subject"],
                    case["subject"],
                )


if __name__ == "__main__":
    unittest.main()
