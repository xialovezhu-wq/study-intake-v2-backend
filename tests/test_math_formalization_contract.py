#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

import preprocessor_core as core  # noqa: E402
import math_shadow_replay as replay  # noqa: E402
from tests.test_math_v2_core import FakeMathRunner  # noqa: E402

from tests.synthetic_math_formalization_fixture import (  # noqa: E402
    SyntheticMathFormalizationFixture,
)


def _find_jsonschema_python() -> Path:
    candidates = (
        Path(sys.executable),
        Path("/opt/miniconda3/envs/dl/bin/python"),
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import jsonschema"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if probe.returncode == 0:
            return candidate
    return Path(sys.executable)


JSONSCHEMA_PYTHON = _find_jsonschema_python()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MathFormalizationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._fixture_temp = tempfile.TemporaryDirectory(
            prefix="math-formalization-contract-"
        )
        cls.fixture = SyntheticMathFormalizationFixture.create(
            Path(cls._fixture_temp.name), ROOT / "schemas"
        )
        cls.output_document = {"payload": cls.fixture.payload}
        cls.payload = cls.fixture.payload
        cls.transport = cls.fixture.transport
        cls.transcript = cls.fixture.transcript
        cls.read_session = cls.fixture.read_session
        cls.failure_receipt = cls.fixture.failure_receipt
        cls.provider_schema = cls.fixture.provider_schema
        cls.invalid_provider_schema = cls.fixture.invalid_provider_schema
        cls.evidence_refs = cls.fixture.evidence_refs

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fixture_temp.cleanup()

    def source_candidate(self) -> core.Candidate:
        binding = copy.deepcopy(self.fixture.source_binding)
        return core.Candidate(
            subject="math",
            capture_id=self.fixture.capture_id,
            study_date="2026-08-20",
            recorded_at="2026-08-20T00:01:00+08:00",
            input_fingerprint=core.sha256_value(
                {"synthetic_candidate": "source"}
            ),
            input_binding=binding,
            model_input={
                "source_bundle": {"source_kind": binding["source_route"]}
            },
            allowed_evidence_refs=self.evidence_refs,
            image_paths=(),
            target_label="synthetic-source",
            canonical_state="awaiting_background_analysis",
            sol_state="not_authorized",
        )

    def no_source_candidate(self) -> core.Candidate:
        binding = copy.deepcopy(self.fixture.source_binding)
        for key in (
            "source_route",
            "evidence_manifest_sha256",
            "evidence_bundle_sha256",
        ):
            binding.pop(key, None)
        return core.Candidate(
            subject="math",
            capture_id=self.fixture.capture_id,
            study_date="2026-08-20",
            recorded_at="2026-08-20T00:01:00+08:00",
            input_fingerprint=core.sha256_value(
                {"synthetic_candidate": "no-source"}
            ),
            input_binding=binding,
            model_input={"source_bundle": None},
            allowed_evidence_refs=self.evidence_refs,
            image_paths=(),
            target_label="no-source-bundle",
            canonical_state="awaiting_background_analysis",
            sol_state="not_authorized",
        )

    @staticmethod
    def role_claim(source: dict[str, object], *, text: str) -> dict[str, object]:
        claim = copy.deepcopy(source)
        claim["text"] = text
        claim["counterevidence_or_boundary"] = (
            "仅整理 synthetic capture 已证明的业务事实，不据此确认正式库身份、知识节点或关系。"
        )
        claim["sol_verification_action"] = (
            "Sol 后续独立重开 synthetic evidence；正式身份与关系另查正式库。"
        )
        return claim

    def corrected_capture_overlay(self) -> dict[str, object]:
        payload = copy.deepcopy(self.payload)
        payload["formalization_candidates"].update(
            {
                "safe_summary": [
                    self.role_claim(
                        payload["evidence_assessment"]["observed_facts"][0],
                        text=(
                            "Synthetic capture records a bounded learner event; it is not independent mastery."
                        ),
                    )
                ],
                "question_body": [
                    self.role_claim(
                        payload["question_structure"]["asked_task"][0],
                        text=(
                            "Synthetic question-body evidence is preserved without inventing a formal library identity."
                        ),
                    )
                ],
                "source_and_answer": [
                    self.role_claim(
                        payload["question_structure"]["source_answer"][0],
                        text=(
                            "Synthetic source-answer evidence remains a candidate and still requires Sol reopening."
                        ),
                    )
                ],
                "wrong_point": [
                    self.role_claim(
                        payload["reasoning_diagnosis"]["first_break"],
                        text=(
                            "The synthetic first-break claim is capture-bound and must not be promoted as a library fact."
                        ),
                    )
                ],
                "methods": [
                    self.role_claim(
                        payload["question_structure"]["objects"][0],
                        text=(
                            "Synthetic method evidence is retained only as a capture-backed candidate."
                        ),
                    )
                ],
            }
        )
        return payload

    def test_synthetic_transport_and_original_shape_reproduce_failure(self) -> None:
        self.assertEqual(
            self.transport["mcp_item_count"], len(self.transport["mcp_items"])
        )
        self.assertGreater(len(self.transcript["calls"]), 0)
        self.assertGreater(len(self.read_session["artifact_ids"]), 0)
        last = self.transport["mcp_items"][-1]["item"]
        self.assertEqual(last["tool"], "search_records")
        self.assertEqual(
            last["arguments"], {"query": "synthetic-query", "page_size": 48}
        )
        self.assertEqual(
            last["result"]["structured_content"]["error"]["code"],
            "OUTPUT_LIMIT",
        )
        self.assertEqual(
            self.failure_receipt["error_code"],
            "math_analysis_formal_field_coverage_failed",
        )
        self.assertEqual(self.failure_receipt["formal_write_count"], 0)
        with self.assertRaisesRegex(
            core.PreprocessorError,
            "math_analysis_formal_field_coverage_failed",
        ):
            core.validate_math_semantic_gates(
                copy.deepcopy(self.payload), self.source_candidate()
            )

    def test_corrected_capture_ref_overlay_passes_without_identity_invention(self) -> None:
        overlay = self.corrected_capture_overlay()
        validated = core.validate_math_semantic_gates(
            overlay, self.source_candidate()
        )
        for field in core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS:
            self.assertEqual(len(validated["formalization_candidates"][field]), 1)
        self.assertEqual(
            validated["formalization_candidates"]["relationship_proposals"],
            [],
        )
        self.assertEqual(
            validated["target_identity"]["formal_target"][0]["claim_type"],
            "unresolved",
        )
        used = {
            ref
            for field in core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS
            for claim in validated["formalization_candidates"][field]
            for ref in claim["evidence_refs"]
        }
        self.assertTrue(used)
        self.assertLessEqual(used, set(self.evidence_refs))

    def test_missing_source_bundle_does_not_force_capture_fields(self) -> None:
        self.assertFalse(
            core._math_verified_source_bundle_formalization_mode(
                self.no_source_candidate().input_binding,
                self.no_source_candidate().model_input,
            )
        )
        validated = core.validate_math_semantic_gates(
            copy.deepcopy(self.payload), self.no_source_candidate()
        )
        self.assertTrue(
            all(
                not validated["formalization_candidates"][field]
                for field in core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS
            )
        )

    def test_unverified_source_binding_fails_closed(self) -> None:
        candidate = self.source_candidate()
        binding = copy.deepcopy(candidate.input_binding)
        binding.pop("evidence_bundle_sha256")
        with self.assertRaisesRegex(
            core.PreprocessorError,
            "math_source_bundle_formalization_binding_invalid",
        ):
            core._math_verified_source_bundle_formalization_mode(
                binding, candidate.model_input
            )

    def test_synthetic_shadow_replay_keeps_original_publish_signature(self) -> None:
        protected_before = self.fixture.source_snapshot()
        manifest = self.fixture.manifest
        config = copy.deepcopy(self.fixture.config)
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "synthetic-replay"
            config["runtime_root"] = str(runtime)
            config["worker"]["log_path"] = str(runtime / "logs/worker.log")
            config["worker"]["lock_path"] = str(
                runtime / "state/worker.lock"
            )
            candidate = replay.build_replay_candidate(
                manifest=manifest,
                item=manifest["items"][0],
                config=config,
            )
            self.assertIsNone(candidate.input_binding.get("source_route"))
            self.assertIsInstance(candidate.model_input.get("source_bundle"), dict)
            self.assertFalse(
                core._math_verified_source_bundle_formalization_mode(
                    candidate.input_binding,
                    candidate.model_input,
                )
            )
            with self.assertRaisesRegex(
                core.PreprocessorError,
                "math_critical_review_dynamic_schema_mismatch",
            ):
                class ReplayRunner:
                    def run_math_v2(self, replay_candidate):
                        result = FakeMathRunner().run_math_v2(replay_candidate)
                        generation = "synthetic-generation"
                        collection = "relationships"
                        stable_id = "SYNTHETIC-REL-001"
                        source_hash = core.sha256_value(
                            {"synthetic": "grounding-source"}
                        )
                        evidence_ref = core.model_mcp_item_ref(
                            subject="math",
                            generation=generation,
                            collection=collection,
                            stable_id=stable_id,
                            source_hash=source_hash,
                        )
                        manifest_core = {
                            "schema_version": "model_mcp_grounding_manifest_v1",
                            "items": [
                                {
                                    "evidence_ref": evidence_ref,
                                    "subject": "math",
                                    "generation": generation,
                                    "collection": collection,
                                    "stable_id": stable_id,
                                    "source_hash": source_hash,
                                    "data_role": "relationship",
                                    "consumed_in": ["analysis"],
                                }
                            ],
                            "item_count": 1,
                            "host_semantic_prefetch": False,
                            "formal_write_count": 0,
                        }
                        manifest = {
                            **manifest_core,
                            "manifest_sha256": core.sha256_value(manifest_core),
                        }
                        for stage_name in ("analysis", "critical_review"):
                            result.stage_receipts[stage_name].update(
                                {
                                    "mcp_grounding_manifest": copy.deepcopy(manifest),
                                    "mcp_grounding_manifest_sha256": manifest[
                                        "manifest_sha256"
                                    ],
                                }
                            )
                        result.stage_receipts["read_session"] = {
                            "status": "complete"
                        }
                        result.stage_receipts["critical_review"]["schema_sha256"] = (
                            core.sha256_value({"synthetic": "tampered-schema"})
                        )
                        return result

                worker = core.Worker(config, model_runner=ReplayRunner())
                worker.publish_math_shadow_candidate(candidate)
            self.assertFalse(
                worker.store.latest_path("math", candidate.capture_id).exists()
            )
            self.assertFalse(
                any(
                    (runtime / "shadow" / "historical" / "packages").rglob(
                        "*.json"
                    )
                )
            )
        self.assertEqual(protected_before, self.fixture.source_snapshot())

    def test_unbound_live_source_bundle_still_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            core.PreprocessorError,
            "math_source_bundle_formalization_mode_invalid",
        ):
            core._math_verified_source_bundle_formalization_mode(
                {},
                {
                    "source_bundle": {
                        "manifest_hash": core.sha256_value(
                            {"synthetic": "unbound-source"}
                        ),
                    }
                },
            )

    def test_unknown_evidence_ref_is_not_filled_or_normalized(self) -> None:
        overlay = self.corrected_capture_overlay()
        overlay["formalization_candidates"]["safe_summary"][0][
            "evidence_refs"
        ] = ["capture.synthetic.unknown"]
        with self.assertRaisesRegex(
            core.PreprocessorError,
            "math_analysis_evidence_refs_invalid",
        ):
            core.validate_math_semantic_gates(
                overlay, self.source_candidate()
            )

    def test_dynamic_provider_schema_binds_only_verified_source_mode(self) -> None:
        analysis_schema = ROOT / "schemas/luna-math-analysis-v2.json"
        review_schema = ROOT / "schemas/luna-math-critical-review-v2.json"
        static_before = {
            analysis_schema: analysis_schema.read_bytes(),
            review_schema: review_schema.read_bytes(),
        }
        overlay = self.corrected_capture_overlay()
        analysis_refs = core._analysis_review_refs(overlay)
        correction_paths = core._math_writable_correction_paths(overlay)

        bound_analysis, _ = core.CodexRunner._bound_output_schema_bytes(
            analysis_schema,
            stage_name="math_analysis",
            allowed_evidence_refs=(),
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=True,
        )
        bound_review, _ = core.CodexRunner._bound_output_schema_bytes(
            review_schema,
            stage_name="math_critical_review",
            allowed_evidence_refs=(),
            allowed_analysis_refs=analysis_refs,
            allowed_correction_paths=correction_paths,
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=True,
        )
        unbound_analysis, _ = core.CodexRunner._bound_output_schema_bytes(
            analysis_schema,
            stage_name="math_analysis",
            allowed_evidence_refs=(),
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=False,
        )
        for payload in (bound_analysis, bound_review):
            schema = json.loads(payload)
            required_claims = schema["$defs"][
                "source_bundle_required_claims"
            ]
            self.assertEqual(required_claims["type"], "array")
            self.assertEqual(
                required_claims["items"], {"$ref": "#/$defs/claim"}
            )
            self.assertEqual(required_claims["minItems"], 1)
            self.assertEqual(required_claims["maxItems"], 1)
            properties = schema["$defs"]["formalization_candidates"][
                "properties"
            ]
            for field in core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS:
                self.assertEqual(
                    properties[field],
                    {"$ref": "#/$defs/source_bundle_required_claims"},
                )
            self.assertNotIn("minItems", properties["relationship_proposals"])
            core._validate_provider_schema_ref_siblings(
                schema,
                stage_name="math_test",
            )
        unbound = json.loads(unbound_analysis)
        self.assertNotIn("source_bundle_required_claims", unbound["$defs"])
        for field in core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS:
            self.assertEqual(
                unbound["$defs"]["formalization_candidates"]["properties"][field],
                {"$ref": "#/$defs/claims"},
            )
        for path, before in static_before.items():
            self.assertEqual(path.read_bytes(), before)

    def test_synthetic_invalid_provider_ref_siblings_fail_before_provider(self) -> None:
        sibling_paths: list[tuple[str, tuple[str, ...]]] = []

        def visit(value: object, path: str = "$") -> None:
            if isinstance(value, dict):
                if "$ref" in value and len(value) > 1:
                    sibling_paths.append(
                        (
                            path,
                            tuple(sorted(key for key in value if key != "$ref")),
                        )
                    )
                for key in sorted(value):
                    visit(value[key], f"{path}/{key}")
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    visit(nested, f"{path}/{index}")

        visit(self.invalid_provider_schema)
        expected_paths = [
            (
                "$/$defs/formalization_candidates/properties/" + field,
                ("maxItems", "minItems"),
            )
            for field in sorted(core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS)
        ]
        self.assertEqual(sibling_paths, expected_paths)

        with self.assertRaises(core.PreprocessorError) as direct:
            core._validate_provider_schema_ref_siblings(
                self.invalid_provider_schema,
                stage_name="math_analysis",
            )
        self.assertEqual(
            direct.exception.code,
            "math_analysis_output_schema_ref_sibling_unsupported",
        )
        self.assertEqual(
            direct.exception.diagnostic,
            {
                "schema_path": (
                    "$/$defs/formalization_candidates/properties/methods"
                ),
                "sibling_keywords": "maxItems,minItems",
            },
        )

        with self.assertRaises(core.PreprocessorError) as integrated:
            core.CodexRunner._bound_output_schema_bytes(
                self.fixture.invalid_provider_schema_path,
                stage_name="math_analysis",
                allowed_evidence_refs=(),
                allow_empty_predeclared_evidence_refs=True,
            )
        self.assertEqual(integrated.exception.code, direct.exception.code)
        self.assertEqual(
            integrated.exception.diagnostic,
            direct.exception.diagnostic,
        )

    def test_corrected_bound_schema_rejects_empty_capture_fields(self) -> None:
        self.assertTrue(JSONSCHEMA_PYTHON.is_file())
        payload, _ = core.CodexRunner._bound_output_schema_bytes(
            ROOT / "schemas/luna-math-analysis-v2.json",
            stage_name="math_analysis",
            allowed_evidence_refs=(),
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=True,
        )
        schema = json.loads(payload)
        validator_script = """
import json
import sys
from jsonschema import Draft202012Validator

schema = json.loads(open(sys.argv[1], encoding="utf-8").read())
instance = json.loads(open(sys.argv[2], encoding="utf-8").read())
errors = sorted(
    Draft202012Validator(schema).iter_errors(instance),
    key=lambda error: tuple(str(part) for part in error.absolute_path),
)
print(json.dumps([
    {
        "instance_path": "/" + "/".join(
            str(part) for part in error.absolute_path
        ),
        "validator": error.validator,
    }
    for error in errors
], sort_keys=True))
raise SystemExit(1 if errors else 0)
"""

        def validate(instance: dict[str, object]) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                schema_path = root / "schema.json"
                instance_path = root / "instance.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                instance_path.write_text(json.dumps(instance), encoding="utf-8")
                return subprocess.run(
                    [
                        str(JSONSCHEMA_PYTHON),
                        "-c",
                        validator_script,
                        str(schema_path),
                        str(instance_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

        rejected = validate(copy.deepcopy(self.payload))
        self.assertEqual(rejected.returncode, 1, rejected.stderr)
        self.assertEqual(
            json.loads(rejected.stdout),
            [
                {
                    "instance_path": (
                        "/formalization_candidates/" + field
                    ),
                    "validator": "minItems",
                }
                for field in sorted(core.MATH_CAPTURE_BACKED_FORMALIZATION_FIELDS)
            ],
        )

        overlay = self.corrected_capture_overlay()
        accepted = validate(overlay)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout), [])
        core.validate_math_semantic_gates(overlay, self.source_candidate())

    def test_stage_and_reopen_schema_hashes_use_identical_mode(self) -> None:
        overlay = self.corrected_capture_overlay()
        profile = {
            "analysis_output_schema": str(
                ROOT / "schemas/luna-math-analysis-v2.json"
            ),
            "critical_review_output_schema": str(
                ROOT / "schemas/luna-math-critical-review-v2.json"
            ),
        }
        expected = core._expected_math_dynamic_schema_sha256s(
            profile,
            allowed_evidence_refs=(),
            image_evidence_refs=(),
            draft_analysis=overlay,
            relationship_context={},
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=True,
        )
        _, direct_analysis = core.CodexRunner._bound_output_schema_bytes(
            Path(profile["analysis_output_schema"]),
            stage_name="math_analysis",
            allowed_evidence_refs=(),
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=True,
        )
        _, direct_review = core.CodexRunner._bound_output_schema_bytes(
            Path(profile["critical_review_output_schema"]),
            stage_name="math_critical_review",
            allowed_evidence_refs=(),
            allowed_analysis_refs=core._analysis_review_refs(overlay),
            allowed_correction_paths=core._math_writable_correction_paths(overlay),
            allow_empty_predeclared_evidence_refs=True,
            math_source_bundle_formalization_mode=True,
        )
        self.assertEqual(expected, {
            "analysis": direct_analysis,
            "critical_review": direct_review,
        })
        self.assertNotEqual(
            direct_analysis,
            file_sha256(self.fixture.invalid_provider_schema_path),
        )
        for callable_object in (
            core.CodexRunner.run_math_v2,
            core.CodexRunner._resume_math_critical,
            core.CodexRunner.load_analysis_checkpoint,
            core.validate_math_v2_artifact_closure,
            core.Worker._validate_math_group_result,
            core.Worker._write_math_v2_artifacts,
        ):
            with self.subTest(callable=callable_object.__qualname__):
                self.assertIn(
                    "math_source_bundle_formalization_mode",
                    inspect.getsource(callable_object),
                )

    def test_only_completeness_change_exposes_depth_gate(self) -> None:
        complete = self.corrected_capture_overlay()
        complete["evidence_assessment"]["completeness"] = "complete"
        with self.assertRaisesRegex(
            core.PreprocessorError,
            "math_analysis_depth_gate_failed",
        ):
            core.validate_math_semantic_gates(
                complete, self.source_candidate()
            )

    def test_prompt_separates_capture_fields_from_library_identity(self) -> None:
        self.assertIn(
            "capture_backed_formalization_mode=true",
            core.MATH_ANALYSIS_PROMPT_TEMPLATE,
        )
        self.assertIn("48、24、12、6、3、1", core.MATH_ANALYSIS_PROMPT_TEMPLATE)
        self.assertIn("library-dependent", core.MATH_ANALYSIS_PROMPT_TEMPLATE)
        runner = core.CodexRunner(
            {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
            ROOT,
        )
        prompt = runner._math_analysis_prompt(
            self.source_candidate(),
            {"analysis_prompt_version": "synthetic-contract-test"},
        )
        self.assertIn('"capture_backed_formalization_mode": true', prompt)


if __name__ == "__main__":
    unittest.main()
