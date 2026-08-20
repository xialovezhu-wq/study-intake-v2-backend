"""Independent successor consumer stage-chain contract, version 1.

This module deliberately has no dependency on the existing v1/v2/v3
producer, queue, or model contracts.  It describes the successor consumer
chain used by the next integration phase:

    capture_durable -> terra_analysis -> luna_analysis
    -> terra_critical_review -> sol_formal_write

``ZeroModelConsumerStageExecutor`` is an in-memory executor.  A caller gives
it a callback for synthetic stage output; the executor never starts a model,
opens a subprocess, calls a network service, or writes a file.  The callback
is therefore a convenient seam for a later production driver without making
the production driver part of this contract.

The public validation functions return defensive copies.  All hashes are
SHA-256 over canonical UTF-8 JSON (sorted keys, compact separators, and one
trailing newline), matching the canonical representation used by the
successor repositories while remaining independent of older schemas.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = "study-intake-consumer-stage-chain-v1"
CAPTURE_INPUT_SCHEMA_VERSION = "study-intake-consumer-capture-input-v1"
STAGE_SCHEMA_VERSION = "study-intake-consumer-stage-v1"

STAGE_ORDER = (
    "capture_durable",
    "terra_analysis",
    "luna_analysis",
    "terra_critical_review",
    "sol_formal_write",
)
MODEL_STAGE_NAMES = (
    "terra_analysis",
    "luna_analysis",
    "terra_critical_review",
)
SOURCE_KINDS = frozenset({"canonical", "synthetic"})


def canonical_bytes(value: Any) -> bytes:
    """Return the only byte representation accepted for contract hashes."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    """Hash a JSON value using :func:`canonical_bytes`."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Hash bytes for callers that already hold canonical source bytes."""

    return hashlib.sha256(value).hexdigest()


class ConsumerStageChainError(ValueError):
    """A fail-closed contract error with a stable machine-readable code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class StageContract:
    """Expected immutable runtime identity for one chain stage."""

    stage_name: str
    sequence: int
    runtime_identity: str
    model: str
    effort: str
    sandbox: str
    tool_mode: str
    tool_policy_sha256: str
    model_stage: bool = False
    write_stage: bool = False


def _policy_digest(stage_name: str) -> str:
    # The policy descriptor is intentionally a small, stable value rather
    # than a path into an existing runtime.  A production driver can attest
    # its own policy bytes and compare the resulting digest to this value.
    return sha256_value(
        {
            "contract": "study-intake-consumer-stage-tool-policy-v1",
            "stage_name": stage_name,
            "allowed_mode": "read_only",
            "model_calls": False,
            "formal_writes": False,
        }
    )


# The three model-stage values are the SHA-256 digests of the dedicated
# read-only Phase 3 policy assets.  Capture has no model policy file and Sol
# has a distinct controlled-write policy descriptor.  Keeping these values in
# the independent contract prevents a legacy reader/reviewer policy from
# being silently substituted for a successor one.
TOOL_POLICY_SHA256 = {
    "capture_durable": _policy_digest("capture_durable"),
    "terra_analysis": "1b4ef95de1fe3dc13a412d7a3c3692a3e31dc72ce0acda0626d44db925511206",
    "luna_analysis": "7493f9049acf4ef9108ee3ad619acd63a659b2bd082a40d56bf2f33750b04b80",
    "terra_critical_review": "4a3d7e6b4fe4448142ec44642f94a346b2efe3c71ba9fc6f310ee8797a650e69",
    "sol_formal_write": sha256_value(
        {
            "contract": "study-intake-consumer-stage-tool-policy-v1",
            "stage_name": "sol_formal_write",
            "allowed_mode": "controlled_write",
            "model_calls": False,
            "formal_writes": True,
        }
    ),
}


STAGE_CONTRACTS = {
    "capture_durable": StageContract(
        stage_name="capture_durable",
        sequence=1,
        runtime_identity="canonical",
        model="none",
        effort="none",
        sandbox="read_only",
        tool_mode="read_only",
        tool_policy_sha256=TOOL_POLICY_SHA256["capture_durable"],
    ),
    "terra_analysis": StageContract(
        stage_name="terra_analysis",
        sequence=2,
        runtime_identity="gpt-5.6-terra",
        model="gpt-5.6-terra",
        effort="max",
        sandbox="read_only",
        tool_mode="read_only",
        tool_policy_sha256=TOOL_POLICY_SHA256["terra_analysis"],
        model_stage=True,
    ),
    "luna_analysis": StageContract(
        stage_name="luna_analysis",
        sequence=3,
        runtime_identity="gpt-5.6-luna",
        model="gpt-5.6-luna",
        effort="max",
        sandbox="read_only",
        tool_mode="read_only",
        tool_policy_sha256=TOOL_POLICY_SHA256["luna_analysis"],
        model_stage=True,
    ),
    "terra_critical_review": StageContract(
        stage_name="terra_critical_review",
        sequence=4,
        runtime_identity="gpt-5.6-terra",
        model="gpt-5.6-terra",
        effort="max",
        sandbox="read_only",
        tool_mode="read_only",
        tool_policy_sha256=TOOL_POLICY_SHA256["terra_critical_review"],
        model_stage=True,
    ),
    "sol_formal_write": StageContract(
        stage_name="sol_formal_write",
        sequence=5,
        runtime_identity="sol",
        model="none",
        effort="none",
        sandbox="controlled_write",
        tool_mode="controlled_write",
        tool_policy_sha256=TOOL_POLICY_SHA256["sol_formal_write"],
        write_stage=True,
    ),
}


GLOBAL_WRITER_LEASE_SCHEMA_VERSION = "study-intake-global-writer-lease-receipt-v1"
COMMIT_RECEIPT_SCHEMA_VERSION = "study-intake-sol-commit-receipt-v1"
TRANSACTION_RECEIPT_SCHEMA_VERSION = "study-intake-sol-transaction-receipt-v1"
DEFAULT_SYNTHETIC_WRITER_IDENTITY = "sol_formal_writer_synthetic_v1"
CONTROLLED_WRITE_MODE = "controlled_write"


StageCallback = Callable[..., Mapping[str, Any] | Any]


def _require_safe_id(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConsumerStageChainError(code)
    if not value.isascii() or len(value) > 160 or any(
        not (character.isalnum() or character in "._:-") for character in value
    ):
        raise ConsumerStageChainError(code)
    return value


def _require_sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.isascii() or len(value) != 64:
        raise ConsumerStageChainError(code)
    try:
        int(value, 16)
    except ValueError as exc:
        raise ConsumerStageChainError(code) from exc
    if value.lower() != value:
        raise ConsumerStageChainError(code)
    return value


def build_capture_input(
    capture_id: str,
    payload: Any,
    *,
    source_kind: str = "synthetic",
    durable: bool = True,
) -> dict[str, Any]:
    """Build the canonical or synthetic input envelope for a chain.

    ``source_kind`` is deliberately limited to ``canonical`` and
    ``synthetic``.  The latter is for deterministic tests only; it is not a
    claim that a live capture was observed.  ``durable`` must be explicitly
    true before any consumer stage can run.
    """

    _require_safe_id(capture_id, "capture_id_invalid")
    if source_kind not in SOURCE_KINDS:
        raise ConsumerStageChainError("capture_source_kind_invalid")
    if not isinstance(durable, bool):
        raise ConsumerStageChainError("capture_durable_flag_invalid")
    return {
        "schema_version": CAPTURE_INPUT_SCHEMA_VERSION,
        "capture_id": capture_id,
        "source_kind": source_kind,
        "durable": durable,
        "payload": copy.deepcopy(payload),
        "payload_sha256": sha256_value(payload),
    }


def synthetic_capture_input(
    capture_id: str,
    payload: Any,
    *,
    durable: bool = True,
) -> dict[str, Any]:
    """Explicit convenience constructor for a synthetic fixture."""

    return build_capture_input(
        capture_id,
        payload,
        source_kind="synthetic",
        durable=durable,
    )


def canonical_capture_input(
    capture_id: str,
    payload: Any,
    *,
    durable: bool = True,
) -> dict[str, Any]:
    """Explicit convenience constructor for a canonical fixture."""

    return build_capture_input(
        capture_id,
        payload,
        source_kind="canonical",
        durable=durable,
    )


def validate_capture_input(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy a canonical/synthetic durable input envelope."""

    if not isinstance(value, Mapping):
        raise ConsumerStageChainError("capture_input_shape_invalid")
    expected_keys = {
        "schema_version",
        "capture_id",
        "source_kind",
        "durable",
        "payload",
        "payload_sha256",
    }
    if set(value) != expected_keys:
        raise ConsumerStageChainError("capture_input_shape_invalid")
    if value.get("schema_version") != CAPTURE_INPUT_SCHEMA_VERSION:
        raise ConsumerStageChainError("capture_input_schema_invalid")
    capture_id = _require_safe_id(value.get("capture_id"), "capture_id_invalid")
    if value.get("source_kind") not in SOURCE_KINDS:
        raise ConsumerStageChainError("capture_source_kind_invalid")
    if value.get("durable") is not True:
        raise ConsumerStageChainError("capture_durable_gate_failed")
    payload_sha = _require_sha(value.get("payload_sha256"), "capture_input_hash_invalid")
    if payload_sha != sha256_value(value.get("payload")):
        raise ConsumerStageChainError("capture_input_hash_invalid")
    return copy.deepcopy(
        {
            "schema_version": CAPTURE_INPUT_SCHEMA_VERSION,
            "capture_id": capture_id,
            "source_kind": value["source_kind"],
            "durable": True,
            "payload": value["payload"],
            "payload_sha256": payload_sha,
        }
    )


def _callback_arity(callback: StageCallback) -> int | None:
    """Return positional arity where introspection is possible."""

    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return None
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    ):
        return 3
    return len(positional)


def _invoke_callback(
    callback: StageCallback,
    contract: StageContract,
    input_payload: Any,
) -> Any:
    """Invoke a callback without making callback signature a hidden gate.

    The documented form is ``callback(stage_name, input_payload, contract)``.
    Two-argument and one-argument forms are accepted for small synthetic
    fixtures.  A one-argument callback receives the immutable StageContract.
    """

    arity = _callback_arity(callback)
    if arity is None or arity >= 3:
        return callback(contract.stage_name, copy.deepcopy(input_payload), contract)
    if arity == 2:
        return callback(contract.stage_name, copy.deepcopy(input_payload))
    if arity == 1:
        return callback(contract)
    if arity == 0:
        return callback()
    raise ConsumerStageChainError("stage_callback_signature_invalid")


def _callback_mapping(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        # A scalar/list is a valid synthetic stage payload.  It is wrapped so
        # stage metadata cannot be confused with domain output.
        return {"output": copy.deepcopy(raw)}
    result = copy.deepcopy(dict(raw))
    if "output" not in result:
        # Allow the short callback form that returns a domain object directly.
        # Reserved contract keys are interpreted as an envelope only when an
        # explicit output key is present.
        return {"output": result}
    return result


def _require_nonnegative_count(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConsumerStageChainError(code)
    return value


def _synthetic_sol_formal_write_output(
    *,
    writer_identity: str = DEFAULT_SYNTHETIC_WRITER_IDENTITY,
    action_count: int = 0,
    formal_write_count: int = 0,
) -> dict[str, Any]:
    """Build a closed, zero-action synthetic Sol write result.

    The receipts are intentionally real contract-shaped values even when no
    formal action is applied.  Each outer digest hashes the corresponding
    receipt object, avoiding circular self-hashes while retaining a strict
    binding chain.
    """

    _require_safe_id(writer_identity, "writer_identity_invalid")
    action_count = _require_nonnegative_count(action_count, "action_count_invalid")
    formal_write_count = _require_nonnegative_count(
        formal_write_count, "stage_formal_write_count_invalid"
    )
    lease = {
        "schema_version": GLOBAL_WRITER_LEASE_SCHEMA_VERSION,
        "lease_id": "synthetic-global-writer-lease-v1",
        "writer_identity": writer_identity,
        "scope": "study-intake-consumer-stage-chain-v1",
        "mode": CONTROLLED_WRITE_MODE,
        "status": "closed",
        "action_count": action_count,
        "formal_write_count": formal_write_count,
    }
    lease_sha256 = sha256_value(lease)
    commit = {
        "schema_version": COMMIT_RECEIPT_SCHEMA_VERSION,
        "commit_id": "synthetic-sol-commit-v1",
        "transaction_id": "synthetic-sol-transaction-v1",
        "lease_id": lease["lease_id"],
        "global_writer_lease_receipt_sha256": lease_sha256,
        "writer_identity": writer_identity,
        "mode": CONTROLLED_WRITE_MODE,
        "status": "closed",
        "action_count": action_count,
        "formal_write_count": formal_write_count,
    }
    commit_sha256 = sha256_value(commit)
    transaction = {
        "schema_version": TRANSACTION_RECEIPT_SCHEMA_VERSION,
        "transaction_id": commit["transaction_id"],
        "commit_id": commit["commit_id"],
        "commit_receipt_sha256": commit_sha256,
        "writer_identity": writer_identity,
        "mode": CONTROLLED_WRITE_MODE,
        "status": "closed",
        "action_count": action_count,
        "formal_write_count": formal_write_count,
    }
    transaction_sha256 = sha256_value(transaction)
    return {
        "global_writer_lease_receipt": lease,
        "global_writer_lease_receipt_sha256": lease_sha256,
        "writer_identity": writer_identity,
        "commit_receipt": commit,
        "commit_receipt_sha256": commit_sha256,
        "transaction_receipt": transaction,
        "transaction_receipt_sha256": transaction_sha256,
        "action_count": action_count,
        "formal_write_count": formal_write_count,
        "complete": True,
    }


def synthetic_sol_formal_write_output(
    *,
    writer_identity: str = DEFAULT_SYNTHETIC_WRITER_IDENTITY,
    action_count: int = 0,
    formal_write_count: int = 0,
) -> dict[str, Any]:
    """Public constructor for a deterministic synthetic Sol write result."""

    return _synthetic_sol_formal_write_output(
        writer_identity=writer_identity,
        action_count=action_count,
        formal_write_count=formal_write_count,
    )


def _validate_sol_formal_write_output(
    output: Mapping[str, Any],
    *,
    stage_formal_write_count: int,
) -> dict[str, Any]:
    required = {
        "global_writer_lease_receipt",
        "global_writer_lease_receipt_sha256",
        "writer_identity",
        "commit_receipt",
        "commit_receipt_sha256",
        "transaction_receipt",
        "transaction_receipt_sha256",
        "action_count",
        "formal_write_count",
        "complete",
    }
    if set(output) != required:
        raise ConsumerStageChainError("sol_formal_write_receipt_shape_invalid")
    if output.get("complete") is not True:
        raise ConsumerStageChainError("sol_commit_receipt_not_closed")
    writer_identity = _require_safe_id(
        output.get("writer_identity"), "writer_identity_invalid"
    )
    action_count = _require_nonnegative_count(
        output.get("action_count"), "action_count_invalid"
    )
    if output.get("formal_write_count") != stage_formal_write_count:
        raise ConsumerStageChainError("stage_formal_write_count_invalid")
    lease = output.get("global_writer_lease_receipt")
    if not isinstance(lease, Mapping) or set(lease) != {
        "schema_version",
        "lease_id",
        "writer_identity",
        "scope",
        "mode",
        "status",
        "action_count",
        "formal_write_count",
    }:
        raise ConsumerStageChainError("sol_lease_receipt_shape_invalid")
    if (
        lease.get("schema_version") != GLOBAL_WRITER_LEASE_SCHEMA_VERSION
        or lease.get("scope") != "study-intake-consumer-stage-chain-v1"
        or lease.get("mode") != CONTROLLED_WRITE_MODE
        or lease.get("status") != "closed"
        or lease.get("writer_identity") != writer_identity
        or lease.get("action_count") != action_count
        or lease.get("formal_write_count") != stage_formal_write_count
    ):
        raise ConsumerStageChainError("sol_lease_receipt_invalid")
    lease_id = _require_safe_id(lease.get("lease_id"), "sol_lease_receipt_invalid")
    lease_sha256 = _require_sha(
        output.get("global_writer_lease_receipt_sha256"),
        "sol_lease_receipt_hash_invalid",
    )
    if lease_sha256 != sha256_value(lease):
        raise ConsumerStageChainError("sol_lease_receipt_hash_invalid")

    commit = output.get("commit_receipt")
    if not isinstance(commit, Mapping) or set(commit) != {
        "schema_version",
        "commit_id",
        "transaction_id",
        "lease_id",
        "global_writer_lease_receipt_sha256",
        "writer_identity",
        "mode",
        "status",
        "action_count",
        "formal_write_count",
    }:
        raise ConsumerStageChainError("sol_commit_receipt_shape_invalid")
    if (
        commit.get("schema_version") != COMMIT_RECEIPT_SCHEMA_VERSION
        or commit.get("lease_id") != lease_id
        or commit.get("global_writer_lease_receipt_sha256") != lease_sha256
        or commit.get("writer_identity") != writer_identity
        or commit.get("mode") != CONTROLLED_WRITE_MODE
        or commit.get("status") != "closed"
        or commit.get("action_count") != action_count
        or commit.get("formal_write_count") != stage_formal_write_count
    ):
        raise ConsumerStageChainError("sol_commit_receipt_invalid")
    commit_id = _require_safe_id(commit.get("commit_id"), "sol_commit_receipt_invalid")
    transaction_id = _require_safe_id(
        commit.get("transaction_id"), "sol_transaction_receipt_invalid"
    )
    commit_sha256 = _require_sha(
        output.get("commit_receipt_sha256"), "sol_commit_receipt_hash_invalid"
    )
    if commit_sha256 != sha256_value(commit):
        raise ConsumerStageChainError("sol_commit_receipt_hash_invalid")

    transaction = output.get("transaction_receipt")
    if not isinstance(transaction, Mapping) or set(transaction) != {
        "schema_version",
        "transaction_id",
        "commit_id",
        "commit_receipt_sha256",
        "writer_identity",
        "mode",
        "status",
        "action_count",
        "formal_write_count",
    }:
        raise ConsumerStageChainError("sol_transaction_receipt_shape_invalid")
    if (
        transaction.get("schema_version") != TRANSACTION_RECEIPT_SCHEMA_VERSION
        or transaction.get("transaction_id") != transaction_id
        or transaction.get("commit_id") != commit_id
        or transaction.get("commit_receipt_sha256") != commit_sha256
        or transaction.get("writer_identity") != writer_identity
        or transaction.get("mode") != CONTROLLED_WRITE_MODE
        or transaction.get("status") != "closed"
        or transaction.get("action_count") != action_count
        or transaction.get("formal_write_count") != stage_formal_write_count
    ):
        raise ConsumerStageChainError("sol_transaction_receipt_invalid")
    transaction_sha256 = _require_sha(
        output.get("transaction_receipt_sha256"),
        "sol_transaction_receipt_hash_invalid",
    )
    if transaction_sha256 != sha256_value(transaction):
        raise ConsumerStageChainError("sol_transaction_receipt_hash_invalid")
    return copy.deepcopy(dict(output))


def _verify_callback_metadata(
    envelope: Mapping[str, Any],
    contract: StageContract,
    expected_input_sha256: str,
    previous_output_sha256: str | None,
) -> int:
    checks = (
        ("stage_name", contract.stage_name, "stage_exchange_invalid"),
        ("sequence", contract.sequence, "stage_order_invalid"),
        ("runtime_identity", contract.runtime_identity, "stage_identity_invalid"),
        ("identity", contract.runtime_identity, "stage_identity_invalid"),
        ("agent_identity", contract.runtime_identity, "stage_identity_invalid"),
        ("model", contract.model, "stage_model_invalid"),
        ("requested_model", contract.model, "stage_model_invalid"),
        ("runtime_model", contract.model, "stage_model_invalid"),
        ("effort", contract.effort, "stage_effort_invalid"),
        ("reasoning_effort", contract.effort, "stage_effort_invalid"),
        ("sandbox", contract.sandbox, "stage_sandbox_invalid"),
        ("sandbox_mode", contract.sandbox, "stage_sandbox_invalid"),
        ("tool_mode", contract.tool_mode, "stage_tool_mode_invalid"),
        (
            "tool_policy_sha256",
            contract.tool_policy_sha256,
            "stage_tool_policy_digest_invalid",
        ),
        (
            "tool_policy_digest",
            contract.tool_policy_sha256,
            "stage_tool_policy_digest_invalid",
        ),
        ("input_sha256", expected_input_sha256, "stage_input_hash_invalid"),
        (
            "previous_output_sha256",
            previous_output_sha256,
            "stage_input_hash_invalid",
        ),
        ("closed", True, "stage_not_closed"),
    )
    for field, expected, code in checks:
        if field in envelope and envelope[field] != expected:
            raise ConsumerStageChainError(code)

    expected_status = "complete" if contract.write_stage else "closed"
    if "status" in envelope and envelope["status"] != expected_status:
        raise ConsumerStageChainError("stage_status_invalid")
    if "durable" in envelope and envelope["durable"] is not True:
        raise ConsumerStageChainError("stage_durable_invalid")
    if "eligible" in envelope and envelope["eligible"] is not True:
        raise ConsumerStageChainError("stage_ineligible")
    formal_write_count = envelope.get("formal_write_count", 0)
    if (
        isinstance(formal_write_count, bool)
        or not isinstance(formal_write_count, int)
        or formal_write_count < 0
        or (not contract.write_stage and formal_write_count != 0)
    ):
        raise ConsumerStageChainError("stage_formal_write_count_invalid")
    return formal_write_count


def _build_stage_receipt(
    contract: StageContract,
    chain_id: str,
    capture_id: str,
    input_payload: Any,
    previous_output_sha256: str | None,
    callback: StageCallback,
) -> dict[str, Any]:
    input_sha256 = sha256_value(input_payload)
    raw = _invoke_callback(callback, contract, input_payload)
    envelope = _callback_mapping(raw)
    formal_write_count = _verify_callback_metadata(
        envelope,
        contract,
        input_sha256,
        previous_output_sha256,
    )
    output = copy.deepcopy(envelope["output"])
    output_sha256 = sha256_value(output)
    if contract.write_stage:
        if not isinstance(output, Mapping):
            raise ConsumerStageChainError("sol_formal_write_receipt_shape_invalid")
        output = copy.deepcopy(dict(output))
        if output.get("formal_write_count", formal_write_count) != formal_write_count:
            raise ConsumerStageChainError("stage_formal_write_count_invalid")
        output["formal_write_count"] = formal_write_count
        output.setdefault("complete", True)
        output = _validate_sol_formal_write_output(
            output,
            stage_formal_write_count=formal_write_count,
        )
        output_sha256 = sha256_value(output)
    if "output_sha256" in envelope:
        supplied_output_sha256 = _require_sha(
            envelope["output_sha256"], "stage_output_hash_invalid"
        )
        if supplied_output_sha256 != output_sha256:
            raise ConsumerStageChainError("stage_output_hash_invalid")

    return {
        "schema_version": STAGE_SCHEMA_VERSION,
        "chain_id": chain_id,
        "capture_id": capture_id,
        "stage_name": contract.stage_name,
        "sequence": contract.sequence,
        "status": "complete" if contract.write_stage else "closed",
        "runtime_identity": contract.runtime_identity,
        "model": contract.model,
        "effort": contract.effort,
        "reasoning_effort": contract.effort,
        "sandbox": contract.sandbox,
        "tool_mode": contract.tool_mode,
        "tool_policy_sha256": contract.tool_policy_sha256,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "previous_output_sha256": previous_output_sha256,
        "output": output,
        "durable": True,
        "closed": True,
        "eligible": True,
        "formal_write_count": formal_write_count,
        "complete": contract.write_stage,
        "writer_identity": (
            output.get("writer_identity") if contract.write_stage else None
        ),
        "global_writer_lease_receipt": (
            output.get("global_writer_lease_receipt") if contract.write_stage else None
        ),
        "global_writer_lease_receipt_sha256": (
            output.get("global_writer_lease_receipt_sha256")
            if contract.write_stage
            else None
        ),
        "commit_receipt": output.get("commit_receipt") if contract.write_stage else None,
        "commit_receipt_sha256": (
            output.get("commit_receipt_sha256") if contract.write_stage else None
        ),
        "transaction_receipt": (
            output.get("transaction_receipt") if contract.write_stage else None
        ),
        "transaction_receipt_sha256": (
            output.get("transaction_receipt_sha256") if contract.write_stage else None
        ),
    }


def _validate_stage_receipt(
    stage: Mapping[str, Any],
    contract: StageContract,
    *,
    chain_id: str,
    capture_id: str,
    expected_input_sha256: str,
    expected_previous_output_sha256: str | None,
) -> dict[str, Any]:
    if not isinstance(stage, Mapping):
        raise ConsumerStageChainError("stage_shape_invalid")
    required = {
        "schema_version",
        "chain_id",
        "capture_id",
        "stage_name",
        "sequence",
        "status",
        "runtime_identity",
        "model",
        "effort",
        "reasoning_effort",
        "sandbox",
        "tool_mode",
        "tool_policy_sha256",
        "input_sha256",
        "output_sha256",
        "previous_output_sha256",
        "output",
        "durable",
        "closed",
        "eligible",
        "formal_write_count",
        "complete",
        "writer_identity",
        "global_writer_lease_receipt",
        "global_writer_lease_receipt_sha256",
        "commit_receipt",
        "commit_receipt_sha256",
        "transaction_receipt",
        "transaction_receipt_sha256",
    }
    if set(stage) != required:
        raise ConsumerStageChainError("stage_shape_invalid")
    if stage.get("schema_version") != STAGE_SCHEMA_VERSION:
        raise ConsumerStageChainError("stage_schema_invalid")
    if stage.get("chain_id") != chain_id or stage.get("capture_id") != capture_id:
        raise ConsumerStageChainError("stage_binding_invalid")
    if stage.get("stage_name") != contract.stage_name:
        raise ConsumerStageChainError("stage_exchange_invalid")
    if stage.get("sequence") != contract.sequence:
        raise ConsumerStageChainError("stage_order_invalid")
    expected_status = "complete" if contract.write_stage else "closed"
    if stage.get("status") != expected_status or stage.get("closed") is not True:
        raise ConsumerStageChainError("stage_not_closed")
    for field, expected, code in (
        ("runtime_identity", contract.runtime_identity, "stage_identity_invalid"),
        ("model", contract.model, "stage_model_invalid"),
        ("effort", contract.effort, "stage_effort_invalid"),
        ("reasoning_effort", contract.effort, "stage_effort_invalid"),
        ("sandbox", contract.sandbox, "stage_sandbox_invalid"),
        ("tool_mode", contract.tool_mode, "stage_tool_mode_invalid"),
        (
            "tool_policy_sha256",
            contract.tool_policy_sha256,
            "stage_tool_policy_digest_invalid",
        ),
        ("input_sha256", expected_input_sha256, "stage_input_hash_invalid"),
        (
            "previous_output_sha256",
            expected_previous_output_sha256,
            "stage_input_hash_invalid",
        ),
    ):
        if stage.get(field) != expected:
            raise ConsumerStageChainError(code)
    _require_sha(stage.get("input_sha256"), "stage_input_hash_invalid")
    _require_sha(stage.get("output_sha256"), "stage_output_hash_invalid")
    if stage.get("previous_output_sha256") is not None:
        _require_sha(
            stage.get("previous_output_sha256"), "stage_input_hash_invalid"
        )
    if stage.get("durable") is not True:
        raise ConsumerStageChainError("stage_durable_invalid")
    if stage.get("eligible") is not True:
        raise ConsumerStageChainError("stage_ineligible")
    formal_write_count = _require_nonnegative_count(
        stage.get("formal_write_count"), "stage_formal_write_count_invalid"
    )
    if not contract.write_stage and formal_write_count != 0:
        raise ConsumerStageChainError("stage_formal_write_count_invalid")
    if stage.get("complete") is not contract.write_stage:
        raise ConsumerStageChainError("stage_completion_invalid")
    output_sha256 = sha256_value(stage.get("output"))
    if stage.get("output_sha256") != output_sha256:
        raise ConsumerStageChainError("stage_output_hash_invalid")
    receipt_fields = (
        "writer_identity",
        "global_writer_lease_receipt",
        "global_writer_lease_receipt_sha256",
        "commit_receipt",
        "commit_receipt_sha256",
        "transaction_receipt",
        "transaction_receipt_sha256",
    )
    if not contract.write_stage:
        if any(stage.get(field) is not None for field in receipt_fields):
            raise ConsumerStageChainError("stage_write_binding_invalid")
    else:
        output = stage.get("output")
        if not isinstance(output, Mapping):
            raise ConsumerStageChainError("sol_formal_write_receipt_shape_invalid")
        normalized_output = _validate_sol_formal_write_output(
            output,
            stage_formal_write_count=formal_write_count,
        )
        if any(stage.get(field) != normalized_output.get(field) for field in receipt_fields):
            raise ConsumerStageChainError("stage_write_binding_invalid")
        if stage.get("writer_identity") != normalized_output["writer_identity"]:
            raise ConsumerStageChainError("writer_identity_invalid")
    return copy.deepcopy(dict(stage))


def validate_consumer_stage_chain(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete, committed five-stage successor chain."""

    if not isinstance(value, Mapping):
        raise ConsumerStageChainError("chain_shape_invalid")
    required = {
        "schema_version",
        "chain_id",
        "capture_id",
        "source_kind",
        "input_sha256",
        "durable_gate",
        "stages",
        "sol_formal_write_complete",
        "status",
        "formal_write_count",
        "chain_sha256",
    }
    if set(value) != required:
        raise ConsumerStageChainError("chain_shape_invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ConsumerStageChainError("chain_schema_invalid")
    chain_id = _require_safe_id(value.get("chain_id"), "chain_id_invalid")
    capture_id = _require_safe_id(value.get("capture_id"), "capture_id_invalid")
    if value.get("source_kind") not in SOURCE_KINDS:
        raise ConsumerStageChainError("capture_source_kind_invalid")
    input_sha256 = _require_sha(value.get("input_sha256"), "chain_input_hash_invalid")
    if value.get("durable_gate") is not True:
        raise ConsumerStageChainError("capture_durable_gate_failed")
    if value.get("sol_formal_write_complete") is not True:
        raise ConsumerStageChainError("sol_formal_write_incomplete")
    if value.get("status") != "complete":
        raise ConsumerStageChainError("chain_incomplete")
    chain_formal_write_count = _require_nonnegative_count(
        value.get("formal_write_count"), "chain_formal_write_count_invalid"
    )
    if chain_formal_write_count < 0:
        raise ConsumerStageChainError("chain_formal_write_count_invalid")
    stages = value.get("stages")
    if not isinstance(stages, Sequence) or isinstance(stages, (str, bytes)):
        raise ConsumerStageChainError("stage_shape_invalid")
    if len(stages) != len(STAGE_ORDER):
        names = [item.get("stage_name") for item in stages if isinstance(item, Mapping)]
        if len(names) != len(set(names)):
            raise ConsumerStageChainError("stage_duplicate")
        missing = [name for name in STAGE_ORDER if name not in names]
        if missing:
            raise ConsumerStageChainError("stage_missing")
        raise ConsumerStageChainError("stage_order_invalid")
    names = [item.get("stage_name") for item in stages if isinstance(item, Mapping)]
    if len(names) != len(set(names)):
        raise ConsumerStageChainError("stage_duplicate")
    if names != list(STAGE_ORDER):
        if set(names) == set(STAGE_ORDER):
            raise ConsumerStageChainError("stage_exchange_invalid")
        missing = [name for name in STAGE_ORDER if name not in names]
        if missing:
            raise ConsumerStageChainError("stage_missing")
        raise ConsumerStageChainError("stage_order_invalid")

    previous_output_sha256: str | None = None
    normalized_stages: list[dict[str, Any]] = []
    for index, (raw_stage, stage_name) in enumerate(zip(stages, STAGE_ORDER), start=1):
        contract = STAGE_CONTRACTS[stage_name]
        expected_input = input_sha256 if index == 1 else previous_output_sha256
        if expected_input is None:
            raise ConsumerStageChainError("stage_input_hash_invalid")
        normalized = _validate_stage_receipt(
            raw_stage,
            contract,
            chain_id=chain_id,
            capture_id=capture_id,
            expected_input_sha256=expected_input,
            expected_previous_output_sha256=(
                None if index == 1 else previous_output_sha256
            ),
        )
        previous_output_sha256 = normalized["output_sha256"]
        normalized_stages.append(normalized)

    # The first stage is the durable gate, the next three are read-only model
    # stages, and Sol is eligible only after its closed commit/transaction
    # receipts have been bound.
    if normalized_stages[0]["stage_name"] != "capture_durable":
        raise ConsumerStageChainError("capture_durable_gate_failed")
    if any(
        normalized_stages[index]["formal_write_count"] != 0
        for index in (0, 1, 2, 3)
    ):
        raise ConsumerStageChainError("stage_formal_write_count_invalid")
    if normalized_stages[-1]["formal_write_count"] != chain_formal_write_count:
        raise ConsumerStageChainError("chain_formal_write_count_mismatch")
    if normalized_stages[-1]["complete"] is not True:
        raise ConsumerStageChainError("sol_formal_write_incomplete")

    core = {key: copy.deepcopy(value[key]) for key in value if key != "chain_sha256"}
    expected_chain_sha256 = sha256_value(core)
    supplied_chain_sha256 = _require_sha(
        value.get("chain_sha256"), "chain_hash_invalid"
    )
    if supplied_chain_sha256 != expected_chain_sha256:
        raise ConsumerStageChainError("chain_hash_invalid")
    normalized = copy.deepcopy(dict(value))
    normalized["stages"] = normalized_stages
    return normalized


def _default_zero_model_callback(
    stage_name: str,
    input_payload: Any,
    contract: StageContract,
) -> dict[str, Any]:
    """Deterministic callback used when a caller does not supply one."""

    if stage_name == "sol_formal_write":
        output = _synthetic_sol_formal_write_output()
    else:
        output = {
            "synthetic": True,
            "stage_name": stage_name,
            "input_sha256": sha256_value(input_payload),
        }
    return {
        "stage_name": stage_name,
        "sequence": contract.sequence,
        "runtime_identity": contract.runtime_identity,
        "model": contract.model,
        "effort": contract.effort,
        "reasoning_effort": contract.effort,
        "sandbox": contract.sandbox,
        "tool_mode": contract.tool_mode,
        "tool_policy_sha256": contract.tool_policy_sha256,
        "input_sha256": sha256_value(input_payload),
        "status": "complete" if contract.write_stage else "closed",
        "closed": True,
        "eligible": True,
        "formal_write_count": output.get("formal_write_count", 0),
        "output": output,
    }


class ZeroModelConsumerStageExecutor:
    """Run the successor chain with an injected, model-free callback."""

    def __init__(self, callback: StageCallback | None = None) -> None:
        self.callback = callback or _default_zero_model_callback

    def execute(
        self,
        capture: Mapping[str, Any],
        *,
        chain_id: str = "synthetic-chain-v1",
    ) -> dict[str, Any]:
        capture_input = validate_capture_input(capture)
        _require_safe_id(chain_id, "chain_id_invalid")
        input_payload = capture_input["payload"]
        input_sha256 = capture_input["payload_sha256"]
        stages: list[dict[str, Any]] = []
        previous_output_sha256: str | None = None
        for stage_name in STAGE_ORDER:
            contract = STAGE_CONTRACTS[stage_name]
            stage = _build_stage_receipt(
                contract,
                chain_id,
                capture_input["capture_id"],
                input_payload,
                previous_output_sha256,
                self.callback,
            )
            stages.append(stage)
            input_payload = stage["output"]
            previous_output_sha256 = stage["output_sha256"]
        chain = {
            "schema_version": SCHEMA_VERSION,
            "chain_id": chain_id,
            "capture_id": capture_input["capture_id"],
            "source_kind": capture_input["source_kind"],
            "input_sha256": input_sha256,
            "durable_gate": True,
            "stages": stages,
            "sol_formal_write_complete": stages[-1]["complete"],
            "status": "complete" if stages[-1]["complete"] else "incomplete",
            "formal_write_count": stages[-1]["formal_write_count"],
        }
        chain["chain_sha256"] = sha256_value(chain)
        return validate_consumer_stage_chain(chain)

    def run(
        self,
        capture: Mapping[str, Any],
        *,
        chain_id: str = "synthetic-chain-v1",
    ) -> dict[str, Any]:
        """Alias for :meth:`execute` used by driver adapters."""

        return self.execute(capture, chain_id=chain_id)


# Short names make the contract easy to discover while retaining the explicit
# class name for callers that want to make the zero-model boundary obvious.
ConsumerStageChainExecutor = ZeroModelConsumerStageExecutor


def execute_consumer_stage_chain(
    capture: Mapping[str, Any],
    callback: StageCallback | None = None,
    *,
    chain_id: str = "synthetic-chain-v1",
) -> dict[str, Any]:
    """Functional entry point for the callback-based zero-model executor."""

    return ZeroModelConsumerStageExecutor(callback).execute(
        capture,
        chain_id=chain_id,
    )


def validate_stage_chain(value: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility alias for callers that use the shorter contract name."""

    return validate_consumer_stage_chain(value)


__all__ = [
    "CAPTURE_INPUT_SCHEMA_VERSION",
    "COMMIT_RECEIPT_SCHEMA_VERSION",
    "ConsumerStageChainError",
    "ConsumerStageChainExecutor",
    "CONTROLLED_WRITE_MODE",
    "DEFAULT_SYNTHETIC_WRITER_IDENTITY",
    "GLOBAL_WRITER_LEASE_SCHEMA_VERSION",
    "MODEL_STAGE_NAMES",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "STAGE_CONTRACTS",
    "STAGE_ORDER",
    "STAGE_SCHEMA_VERSION",
    "StageCallback",
    "StageContract",
    "TOOL_POLICY_SHA256",
    "ZeroModelConsumerStageExecutor",
    "build_capture_input",
    "canonical_bytes",
    "canonical_capture_input",
    "execute_consumer_stage_chain",
    "sha256_bytes",
    "sha256_value",
    "synthetic_capture_input",
    "synthetic_sol_formal_write_output",
    "TRANSACTION_RECEIPT_SCHEMA_VERSION",
    "validate_capture_input",
    "validate_consumer_stage_chain",
    "validate_stage_chain",
]
