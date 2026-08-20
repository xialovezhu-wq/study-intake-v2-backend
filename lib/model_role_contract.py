"""Role-separated Terra/Luna model contract for Multi-Agent V2."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


LEGACY_ROLE_SPECS = {
    "orchestrator": {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "ultra",
        "agents_enabled": True,
        "fresh_context": True,
        "sandbox_mode": "workspace-write",
    },
    "reader": {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "agents_enabled": False,
        "fresh_context": True,
        "sandbox_mode": "read-only",
    },
    "critical_reviewer": {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "ultra",
        "agents_enabled": False,
        "fresh_context": True,
        "sandbox_mode": "read-only",
    },
}

# Phase 3 deliberately gives the two analysis passes and the fresh review
# separate identities.  The legacy roles above stay valid for old releases and
# fixture configurations, but none of them may be used as a consumer-stage
# alias.  In particular, the legacy orchestrator is not an analysis role and
# the Sol MCP preflight is not a writer role.
CONSUMER_STAGE_ROLE_SPECS = {
    "terra_analysis": {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "max",
        "agents_enabled": False,
        "fresh_context": True,
        "sandbox_mode": "read-only",
    },
    "luna_analysis": {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "agents_enabled": False,
        "fresh_context": True,
        "sandbox_mode": "read-only",
    },
    "terra_critical_review": {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "max",
        "agents_enabled": False,
        "fresh_context": True,
        "sandbox_mode": "read-only",
    },
}

# ``ROLE_SPECS`` is the active Phase 3 surface.  Keep the historical map
# separately so callers that need compatibility can opt into it explicitly.
ROLE_SPECS = CONSUMER_STAGE_ROLE_SPECS
ALL_ROLE_SPECS = {**LEGACY_ROLE_SPECS, **CONSUMER_STAGE_ROLE_SPECS}
LEGACY_ROLE_NAMES = frozenset(LEGACY_ROLE_SPECS)
CONSUMER_STAGE_ROLE_NAMES = frozenset(CONSUMER_STAGE_ROLE_SPECS)
ACTIVE_ROLE_NAMES = frozenset(ALL_ROLE_SPECS)
CONSUMER_STAGE_ORDER = (
    "terra_analysis",
    "luna_analysis",
    "terra_critical_review",
)
CONSUMER_STAGE_CHAIN_KEYS = frozenset({"enabled", "stages", "formal_write_count"})


def _role_specs_for_names(names: set[str]) -> Mapping[str, Mapping[str, Any]]:
    """Return the accepted role layout without weakening legacy configs."""

    if names == set(LEGACY_ROLE_NAMES):
        return LEGACY_ROLE_SPECS
    if names == set(CONSUMER_STAGE_ROLE_NAMES):
        return ROLE_SPECS
    if names == set(ACTIVE_ROLE_NAMES):
        return ALL_ROLE_SPECS
    raise ModelRoleContractError("multi_agent_model_roles_invalid")


def validate_consumer_stage_chain(
    value: Mapping[str, Any], *, available_roles: set[str] | None = None
) -> dict[str, Any]:
    """Validate the active, role-bound Phase 3 consumer chain.

    The chain is intentionally kept in configuration rather than folded into
    the historical model-contract schema.  That lets old releases continue to
    validate while making the active three-stage binding explicit.
    """

    if not isinstance(value, Mapping) or set(value) != CONSUMER_STAGE_CHAIN_KEYS:
        raise ModelRoleContractError("consumer_stage_chain_shape_invalid")
    if value.get("enabled") is not True or value.get("formal_write_count") != 0:
        raise ModelRoleContractError("consumer_stage_chain_disabled_or_writable")
    stages = value.get("stages")
    if not isinstance(stages, list) or len(stages) != len(CONSUMER_STAGE_ORDER):
        raise ModelRoleContractError("consumer_stage_chain_stages_invalid")
    expected_roles = set(CONSUMER_STAGE_ROLE_NAMES)
    if available_roles is not None and not expected_roles.issubset(available_roles):
        raise ModelRoleContractError("consumer_stage_chain_role_missing")
    observed_roles: list[str] = []
    for expected_stage, raw in zip(CONSUMER_STAGE_ORDER, stages):
        if not isinstance(raw, Mapping) or set(raw) != {"stage", "role"}:
            raise ModelRoleContractError("consumer_stage_chain_stage_invalid")
        if raw.get("stage") != expected_stage or raw.get("role") != expected_stage:
            raise ModelRoleContractError("consumer_stage_chain_role_binding_invalid")
        observed_roles.append(str(raw["role"]))
    if set(observed_roles) != expected_roles or len(set(observed_roles)) != len(observed_roles):
        raise ModelRoleContractError("consumer_stage_chain_role_binding_invalid")
    return {
        "enabled": True,
        "stages": [
            {"stage": stage, "role": stage} for stage in CONSUMER_STAGE_ORDER
        ],
        "formal_write_count": 0,
    }


class ModelRoleContractError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_contract_from_config(config: Mapping[str, Any]) -> dict[str, Any]:
    models = config.get("models")
    if not isinstance(models, Mapping):
        raise ModelRoleContractError("multi_agent_model_roles_invalid")
    try:
        role_specs = _role_specs_for_names(set(models))
    except ModelRoleContractError:
        raise
    normalized: dict[str, dict[str, Any]] = {}
    for role, required in role_specs.items():
        supplied = models.get(role)
        if not isinstance(supplied, Mapping):
            raise ModelRoleContractError("multi_agent_model_role_invalid")
        expected_keys = set(required) | {"agent_config_path", "tool_policy_path"}
        if set(supplied) != expected_keys or any(supplied.get(key) != value for key, value in required.items()):
            raise ModelRoleContractError(f"multi_agent_{role}_contract_invalid")
        assets: dict[str, str] = {}
        for field in ("agent_config_path", "tool_policy_path"):
            raw = supplied.get(field)
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ModelRoleContractError(f"multi_agent_{role}_asset_path_invalid")
            path = Path(raw)
            if path.is_symlink() or not path.is_file():
                raise ModelRoleContractError(f"multi_agent_{role}_asset_missing")
            assets[field] = raw
            assets[field.replace("_path", "_sha256")] = sha256_file(path)
        normalized[role] = {**copy.deepcopy(dict(required)), **assets}
    chain = config.get("consumer_stage_chain")
    if chain is not None:
        validate_consumer_stage_chain(chain, available_roles=set(models))
    core = {
        "schema_version": "study-intake-multi-agent-model-contract-v1",
        "roles": normalized,
        "strict_config": True,
        "ephemeral": True,
        "ignore_user_config": True,
        "requested_service_tier": None,
        "formal_write_count": 0,
    }
    return {**core, "contract_sha256": hashlib.sha256(canonical_bytes(core)).hexdigest()}


def validate_model_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {
        "schema_version", "roles", "strict_config", "ephemeral",
        "ignore_user_config", "requested_service_tier", "formal_write_count",
        "contract_sha256",
    }:
        raise ModelRoleContractError("multi_agent_model_contract_shape_invalid")
    core = {key: copy.deepcopy(value[key]) for key in value if key != "contract_sha256"}
    if (
        value.get("schema_version") != "study-intake-multi-agent-model-contract-v1"
        or value.get("strict_config") is not True
        or value.get("ephemeral") is not True
        or value.get("ignore_user_config") is not True
        or value.get("requested_service_tier") is not None
        or value.get("formal_write_count") != 0
        or value.get("contract_sha256") != hashlib.sha256(canonical_bytes(core)).hexdigest()
    ):
        raise ModelRoleContractError("multi_agent_model_contract_invalid")
    roles = value.get("roles")
    if not isinstance(roles, Mapping):
        raise ModelRoleContractError("multi_agent_model_roles_invalid")
    try:
        role_specs = _role_specs_for_names(set(roles))
    except ModelRoleContractError:
        raise
    for role, required in role_specs.items():
        supplied = roles.get(role)
        if not isinstance(supplied, Mapping) or any(supplied.get(key) != expected for key, expected in required.items()):
            raise ModelRoleContractError(f"multi_agent_{role}_contract_invalid")
        for field in ("agent_config_sha256", "tool_policy_sha256"):
            digest = supplied.get(field)
            if not isinstance(digest, str) or len(digest) != 64:
                raise ModelRoleContractError(f"multi_agent_{role}_asset_invalid")
    return copy.deepcopy(dict(value))
