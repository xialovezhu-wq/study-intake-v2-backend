from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from consumer_stage_chain_v1 import (  # noqa: E402
    ConsumerStageChainError,
    STAGE_CONTRACTS,
    STAGE_ORDER,
    TOOL_POLICY_SHA256,
    ZeroModelConsumerStageExecutor,
    execute_consumer_stage_chain,
    sha256_value,
    synthetic_capture_input,
    synthetic_sol_formal_write_output,
    validate_consumer_stage_chain,
)


JSONSCHEMA_PYTHON = Path("/opt/miniconda3/envs/dl/bin/python")


def _schema_errors(schema_path: Path, instance: object) -> list[dict]:
    request = {
        "schema": json.loads(schema_path.read_text(encoding="utf-8")),
        "instance": instance,
    }
    script = r"""
import json, sys
from jsonschema import Draft202012Validator, FormatChecker
request = json.load(sys.stdin)
Draft202012Validator.check_schema(request["schema"])
errors = sorted(
    Draft202012Validator(
        request["schema"], format_checker=FormatChecker()
    ).iter_errors(request["instance"]),
    key=lambda error: tuple(str(item) for item in error.absolute_path),
)
json.dump(
    [{"path": list(error.absolute_path), "message": error.message} for error in errors],
    sys.stdout,
    ensure_ascii=False,
    sort_keys=True,
)
"""
    completed = subprocess.run(
        [str(JSONSCHEMA_PYTHON), "-c", script],
        input=json.dumps(request, ensure_ascii=False, sort_keys=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or "schema_validation_failed")
    return json.loads(completed.stdout)


class ConsumerStageChainV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = synthetic_capture_input(
            "CAPTURE-SYN-001",
            {"subject": "cs408", "facts": ["immutable", "answer-safe"]},
        )
        self.chain = execute_consumer_stage_chain(
            self.capture,
            chain_id="CHAIN-SYN-001",
        )

    def assertCode(self, code: str, callback) -> None:
        with self.assertRaises(ConsumerStageChainError) as context:
            callback()
        self.assertEqual(context.exception.code, code)

    def test_happy_path_has_fixed_order_runtime_attestations_and_hash_links(self) -> None:
        self.assertEqual(
            [stage["stage_name"] for stage in self.chain["stages"]],
            list(STAGE_ORDER),
        )
        self.assertTrue(self.chain["durable_gate"])
        self.assertTrue(self.chain["sol_formal_write_complete"])
        self.assertEqual(self.chain["status"], "complete")
        self.assertEqual(
            self.chain["formal_write_count"],
            self.chain["stages"][-1]["formal_write_count"],
        )
        previous_output = None
        for index, stage_name in enumerate(STAGE_ORDER):
            stage = self.chain["stages"][index]
            contract = STAGE_CONTRACTS[stage_name]
            self.assertEqual(stage["runtime_identity"], contract.runtime_identity)
            self.assertEqual(stage["model"], contract.model)
            self.assertEqual(stage["effort"], "max" if contract.model_stage else "none")
            self.assertEqual(stage["sandbox"], contract.sandbox)
            self.assertEqual(stage["tool_mode"], contract.tool_mode)
            self.assertEqual(stage["tool_policy_sha256"], TOOL_POLICY_SHA256[stage_name])
            self.assertEqual(stage["previous_output_sha256"], previous_output)
            self.assertEqual(stage["output_sha256"], sha256_value(stage["output"]))
            if index == 0:
                self.assertEqual(stage["input_sha256"], self.capture["payload_sha256"])
            else:
                self.assertEqual(stage["input_sha256"], previous_output)
            self.assertEqual(
                stage["formal_write_count"],
                0 if index < 4 else self.chain["formal_write_count"],
            )
            self.assertTrue(stage["closed"])
            self.assertEqual(stage["complete"], index == 4)
            previous_output = stage["output_sha256"]
        self.assertEqual(
            self.chain["chain_sha256"],
            sha256_value({key: value for key, value in self.chain.items() if key != "chain_sha256"}),
        )

    def test_callback_executor_is_zero_model_and_calls_only_injected_callback(self) -> None:
        calls: list[tuple[str, object]] = []

        def callback(stage_name, input_payload, contract):
            calls.append((stage_name, input_payload))
            output = {
                "synthetic": True,
                "stage": stage_name,
                "input_sha256": sha256_value(input_payload),
            }
            if stage_name == "sol_formal_write":
                output = synthetic_sol_formal_write_output()
            return {
                "stage_name": stage_name,
                "sequence": contract.sequence,
                "runtime_identity": contract.runtime_identity,
                "model": contract.model,
                "effort": contract.effort,
                "sandbox": contract.sandbox,
                "tool_mode": contract.tool_mode,
                "tool_policy_sha256": contract.tool_policy_sha256,
                "output": output,
                "formal_write_count": output.get("formal_write_count", 0),
            }

        result = ZeroModelConsumerStageExecutor(callback).run(
            self.capture,
            chain_id="CHAIN-CALLBACK-001",
        )
        self.assertEqual([name for name, _ in calls], list(STAGE_ORDER))
        self.assertEqual(result["chain_id"], "CHAIN-CALLBACK-001")

    def test_schema_instances_are_valid_without_touching_legacy_schema_bytes(self) -> None:
        for deleted_name in (
            "consumer-stage-capture-durable-v1.json",
            "consumer-stage-capture-input-v1.json",
            "consumer-stage-luna-analysis-v1.json",
            "consumer-stage-sol-formal-write-v1.json",
            "consumer-stage-sol-handoff-v1.json",
            "consumer-stage-terra-analysis-v1.json",
            "consumer-stage-terra-critical-review-v1.json",
        ):
            self.assertFalse((ROOT / "schemas" / deleted_name).exists())
        schema = json.loads(
            (ROOT / "schemas" / "consumer-stage-chain-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(
                (
                    "capture_input",
                    "capture_durable",
                    "terra_analysis",
                    "luna_analysis",
                    "terra_critical_review",
                    "sol_formal_write",
                )
            ).difference(schema["$defs"]),
            set(),
        )
        self.assertEqual(
            _schema_errors(ROOT / "schemas" / "consumer-stage-chain-v1.json", self.chain),
            [],
        )

        tracked_schema_names = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD", "schemas"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.splitlines()
        for name in tracked_schema_names:
            if Path(name).name.startswith("consumer-stage-"):
                continue
            historical = subprocess.run(
                ["git", "show", f"HEAD:{name}"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout
            self.assertEqual((ROOT / name).read_bytes(), historical, name)

    def test_missing_duplicate_and_exchanged_stage_names_fail_closed(self) -> None:
        missing = copy.deepcopy(self.chain)
        missing["stages"].pop(3)
        self.assertCode("stage_missing", lambda: validate_consumer_stage_chain(missing))

        duplicate = copy.deepcopy(self.chain)
        duplicate["stages"][3] = copy.deepcopy(duplicate["stages"][2])
        self.assertCode("stage_duplicate", lambda: validate_consumer_stage_chain(duplicate))

        exchanged = copy.deepcopy(self.chain)
        exchanged["stages"][1], exchanged["stages"][2] = (
            exchanged["stages"][2],
            exchanged["stages"][1],
        )
        self.assertCode("stage_exchange_invalid", lambda: validate_consumer_stage_chain(exchanged))

    def test_order_and_sequence_mismatch_fail_closed(self) -> None:
        wrong_sequence = copy.deepcopy(self.chain)
        wrong_sequence["stages"][2]["sequence"] = 2
        self.assertCode(
            "stage_order_invalid",
            lambda: validate_consumer_stage_chain(wrong_sequence),
        )

    def test_model_effort_sandbox_and_tool_policy_are_verified(self) -> None:
        for field, code, value in (
            ("model", "stage_model_invalid", "gpt-5.6-luna"),
            ("effort", "stage_effort_invalid", "ultra"),
            ("sandbox", "stage_sandbox_invalid", "workspace-write"),
            ("tool_policy_sha256", "stage_tool_policy_digest_invalid", "f" * 64),
        ):
            mutated = copy.deepcopy(self.chain)
            mutated["stages"][1][field] = value
            self.assertCode(code, lambda mutated=mutated: validate_consumer_stage_chain(mutated))

        controlled_write_drift = copy.deepcopy(self.chain)
        controlled_write_drift["stages"][4]["sandbox"] = "read_only"
        self.assertCode(
            "stage_sandbox_invalid",
            lambda: validate_consumer_stage_chain(controlled_write_drift),
        )
        tool_mode_drift = copy.deepcopy(self.chain)
        tool_mode_drift["stages"][4]["tool_mode"] = "read_only"
        self.assertCode(
            "stage_tool_mode_invalid",
            lambda: validate_consumer_stage_chain(tool_mode_drift),
        )

    def test_input_and_output_hash_chain_is_verified(self) -> None:
        input_tampered = copy.deepcopy(self.chain)
        input_tampered["stages"][2]["input_sha256"] = "a" * 64
        self.assertCode(
            "stage_input_hash_invalid",
            lambda: validate_consumer_stage_chain(input_tampered),
        )

        output_tampered = copy.deepcopy(self.chain)
        output_tampered["stages"][1]["output"] = {"changed": True}
        self.assertCode(
            "stage_output_hash_invalid",
            lambda: validate_consumer_stage_chain(output_tampered),
        )

        chain_tampered = copy.deepcopy(self.chain)
        chain_tampered["chain_sha256"] = "b" * 64
        self.assertCode(
            "chain_hash_invalid",
            lambda: validate_consumer_stage_chain(chain_tampered),
        )

    def test_first_three_model_stages_and_chain_reject_formal_writes(self) -> None:
        for index in (1, 2, 3):
            mutated = copy.deepcopy(self.chain)
            mutated["stages"][index]["formal_write_count"] = 1
            self.assertCode(
                "stage_formal_write_count_invalid",
                lambda mutated=mutated: validate_consumer_stage_chain(mutated),
            )

        chain_mutated = copy.deepcopy(self.chain)
        chain_mutated["formal_write_count"] = 1
        self.assertCode(
            "chain_formal_write_count_mismatch",
            lambda: validate_consumer_stage_chain(chain_mutated),
        )

        def formal_write_callback(stage_name, _input_payload, contract):
            if stage_name == "sol_formal_write":
                output = synthetic_sol_formal_write_output(
                    action_count=2,
                    formal_write_count=2,
                )
            else:
                output = {"synthetic": True, "stage_name": stage_name}
            return {
                "output": output,
                "status": "complete" if contract.write_stage else "closed",
                "formal_write_count": output.get("formal_write_count", 0),
            }

        committed = execute_consumer_stage_chain(
            self.capture,
            formal_write_callback,
            chain_id="CHAIN-SYNTHETIC-WRITE-002",
        )
        self.assertEqual(committed["formal_write_count"], 2)
        self.assertEqual(committed["stages"][-1]["formal_write_count"], 2)

    def test_durable_gate_and_source_kind_are_explicit(self) -> None:
        not_durable = synthetic_capture_input(
            "CAPTURE-NOT-DURABLE",
            {"facts": []},
            durable=False,
        )
        self.assertCode(
            "capture_durable_gate_failed",
            lambda: execute_consumer_stage_chain(not_durable),
        )

        wrong_source = copy.deepcopy(self.capture)
        wrong_source["source_kind"] = "live"
        self.assertCode(
            "capture_source_kind_invalid",
            lambda: execute_consumer_stage_chain(wrong_source),
        )

        wrong_hash = copy.deepcopy(self.capture)
        wrong_hash["payload_sha256"] = "c" * 64
        self.assertCode(
            "capture_input_hash_invalid",
            lambda: execute_consumer_stage_chain(wrong_hash),
        )

    def test_sol_formal_write_requires_closed_bound_lease_commit_and_transaction(self) -> None:
        ineligible = copy.deepcopy(self.chain)
        ineligible["stages"][4]["commit_receipt"]["status"] = "open"
        ineligible["stages"][4]["output"]["commit_receipt"]["status"] = "open"
        ineligible["stages"][4]["output_sha256"] = sha256_value(
            ineligible["stages"][4]["output"]
        )
        self.assertCode(
            "sol_commit_receipt_invalid",
            lambda: validate_consumer_stage_chain(ineligible),
        )

        open_review = copy.deepcopy(self.chain)
        open_review["stages"][3]["closed"] = False
        self.assertCode(
            "stage_not_closed",
            lambda: validate_consumer_stage_chain(open_review),
        )

        writer_drift = copy.deepcopy(self.chain)
        writer_drift["stages"][4]["writer_identity"] = "other-writer"
        self.assertCode(
            "stage_write_binding_invalid",
            lambda: validate_consumer_stage_chain(writer_drift),
        )

        lease_drift = copy.deepcopy(self.chain)
        lease_drift["stages"][4]["global_writer_lease_receipt_sha256"] = "f" * 64
        self.assertCode(
            "stage_write_binding_invalid",
            lambda: validate_consumer_stage_chain(lease_drift),
        )

    def test_canonical_input_is_supported_but_other_modes_are_not(self) -> None:
        canonical = copy.deepcopy(self.capture)
        canonical["source_kind"] = "canonical"
        result = execute_consumer_stage_chain(canonical, chain_id="CHAIN-CANONICAL-001")
        self.assertEqual(result["source_kind"], "canonical")

        unsupported = copy.deepcopy(canonical)
        unsupported["source_kind"] = "production"
        self.assertCode(
            "capture_source_kind_invalid",
            lambda: execute_consumer_stage_chain(unsupported),
        )


if __name__ == "__main__":
    unittest.main()
