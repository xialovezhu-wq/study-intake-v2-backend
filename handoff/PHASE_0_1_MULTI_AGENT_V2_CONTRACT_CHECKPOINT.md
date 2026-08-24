# Study Intake V2 Multi-Agent V2 Phase 0–1 checkpoint

Date: 2026-08-24 Asia/Shanghai

Status: contract freeze complete; production wiring not started

## Source base

- Branch: `codex/multi-agent-v2-contract-freeze-20260824`
- Base: GitHub `main` at `0b7b8da44b670bd901764e12752a1c4ba0da287a`
- Base tree: `19a94557174cc021d2d6f59857b48b018afd30ee`
- Production `current` remained `da9b8831df0d78567bbd3edf2767ccef4f00c3e90a35a4942a016659df9a0efa`.
- The active release bytes matched the base for the current Capture entrypoint,
  dispatcher, task runner, AnalysisPackage, and Multi-Agent V2 core modules.

No existing worktree, branch, release, LaunchAgent, Capture, or formal study data
was changed while establishing this base.

## Frozen successor contract

The successor contract requires exactly three or four required, independent
Luna investigation branches for one Capture. Each successful branch produces a
separate content-addressed Luna investigation report containing its purpose,
MCP call trail, evidence, findings, conflicts, missing evidence, and confidence.

Terra final input contains every full Luna report body in sealed plan order.
The Terra final report records an explicit disposition for every Luna report,
keeps subject analysis, conflicts, evidence gaps, and the Sol checklist, and
remains proposal-only with `formal_write_count=0`.

The Sol handoff binds the exact same ordered Luna report set plus the Terra final
report. It allows `adopt`, `modify`, `reject`, or `request_more_evidence`; it
never authorizes formal apply.

Existing v1 schemas and runtime behavior remain unchanged in this checkpoint.

## Subject Skill alignment

The three background processing Skills were advanced from version 4.0.0 to
4.0.1. Their collection lists now exactly match Shared MCP focused collections.
Relation reads are routed through `query_relations`; the stale pseudo-collection
names were removed. Foreground learning Skills were not changed.

## Verification completed

- `tests.test_multi_agent_report_contract`: 7 passed.
- Skill and processing contract set: 96 passed.
- Skill quick validation: Math, CS408, and English passed.
- Current version-binding focused set: 11 passed.
- New JSON Schemas passed Draft 2020-12 meta-schema validation.
- Manifest generation and `--check` passed with current explicit component
  bindings; only the three generated plugin Schema mirrors are retained in Git.
- `git diff --check`: passed.

No Provider, real MCP session, Capture, Build, deployment, activation, or formal
write was executed in Phase 0–1.

## Next implementation boundary

Phase 2 may now wire the existing Multi-Agent V2 scheduler into the single
ordinary Capture route. It must preserve this checkpoint's full-report closure,
one authoritative completion, and zero-formal-write boundary.
