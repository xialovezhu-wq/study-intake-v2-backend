---
name: kaoyan-english-daily-intake-curation
description: "Use only when the user's trimmed message exactly matches 开始 YYYY-MM-DD 英语正式入库, with a real calendar date replacing YYYY-MM-DD. This one-shot command freezes that exact date's English Luna candidates, lets Sol produce schema-valid typed actions only, and submits safe actions to the deterministic writer without asking for a second confirmation. Keep ambiguous items as needs_user and report PARTIAL. Do not trigger for ordinary sentence study, 整篇结束, generic 正式入库, relative dates, nightly planning, schedules, automation, or standing authorization."
---

# Kaoyan English Daily Intake Curation

## Role

Curate one explicitly dated English intake batch after Luna preprocessing. Sol performs semantic judgment but never edits formal files directly: it emits typed actions, and the deterministic writer alone validates and applies them.

## Goal

Turn one exact date's immutable daytime captures into an auditable nightly result: close at most one bounded Luna coverage gap, freeze the eligible evidence and formal prehashes, let Sol emit typed actions only, and have the deterministic writer apply independently safe actions with receipts while isolating ambiguity.

## Trigger gate

Trim leading and trailing whitespace, then require this full-string form:

```text
开始 YYYY-MM-DD 英语正式入库
```

Validate the date as a real calendar date. Do not infer today, accept relative dates, extract a date from longer prose or trigger from similar wording. A valid invocation authorizes one run for that exact date and the resulting frozen `batch_id`; it is not a timer, recurring schedule or standing permission.

## Authorization

The exact trigger authorizes the one bounded preprocessing attempt, the final freeze, dry-run and deterministic apply for the resulting batch on that date. It also authorizes safe actions to proceed without a second confirmation. It does not authorize another date, a later refreeze after the bounded retry path, a recurring schedule, retained permission, direct Sol/Luna edits, or bypass of manifest, schema, lock, compare-and-swap and receipt gates.

## Success criteria

- The frozen manifest contains only Luna candidates whose `study_date` equals the requested date.
- Every effective capture for the requested date is either covered by a validated Luna candidate or explicitly reported as `needs_preprocess`; missing candidate files are never mistaken for an empty study day.
- Candidate paths, hashes, per-document runtime identity status, four-state runtime identity summary, capture IDs, source hashes and formal prehashes are frozen before Sol judgment.
- Luna candidates prove `formal_writeback=none` and schema validation `PASS`. Runtime identity may be `confirmed` or `requested_unverified`; preserve requested, observed and provenance exactly and never relabel requested-only evidence as confirmed. `mismatch` and `unavailable` are ineligible. Luna never supplies authorization or a formal result.
- Sol emits only the typed action document defined in references/typed-actions-contract.md and never writes bank, mastered or SP files itself.
- Safe actions are dry-run validated and then applied once with `--authorization <batch_id>` without a second confirmation.
- Ambiguous items remain `needs_user`; safe items still complete and the batch reports `PARTIAL`.
- The final receipt reports exact action outcomes, changed formal files, posthashes and validation.

## Read only what the task needs

Always read:

1. references/nightly-curation-contract.md
2. references/typed-actions-contract.md
3. references/output-contract.md
4. the frozen manifest and every candidate named by it
5. the formal snapshots and evidence refs allowed by the manifest

Read repository rules or formal files only through the manifest-bound validation flow. Do not scan unrelated articles, dates, review files, Dashboard state or conversation history.

## Workflow

1. Parse and validate the exact requested date.
2. Inspect the date-bounded capture inventory, then run `freeze-nightly` for that date using the canonical state directory.
3. If effective captures exist but the manifest reports missing Luna coverage, follow the single preprocessing retry in references/nightly-curation-contract.md: call the deployed worker `run-once --subject english --date <date>` once, then refreeze once. Do not start a daemon or loop.
4. If coverage is still missing after that one retry, mark those capture IDs `needs_preprocess`. Continue with independently covered candidates, but never report the missing captures as `NOOP`.
5. Treat the final manifest and `batch_id` as immutable. Verify every candidate path and hash, the frozen `runtime_identity_status`, `runtime_identity_summary`, `study_date`, capture/source binding, Luna producer, the exact requested/observed/provenance record, runtime status in `confirmed` or `requested_unverified`, validation `PASS`, and `formal_writeback=none`. Reject stale, mismatched, unavailable or cross-date input; never upgrade `requested_unverified` in reporting.
6. After the final manifest is immutable, read `../_shared/native-luna-parallel-contract.md`. When at least two covered candidates have disjoint manifest-bound evidence views, shard them by stable candidate ID and assign one or more read-only `explorer` Luna Max Fast leaf agents. Each candidate belongs to exactly one shard. Agents return typed-action proposals, evidence refs, duplicate hints, unresolved fields, hashes and `write_attempted=false`; they never call the background worker, write files, perform global deduplication, or authorize apply. If only one candidate is eligible, keep the review in Sol.
7. Wait for every shard, reject wrong-runtime or wrong-hash results, then let Sol review each covered item, perform cross-candidate and formal-snapshot deduplication, resolve conflicts once, and produce the only schema-valid typed action document:
   - put deterministic, evidence-complete operations in `actions`;
   - put semantic ambiguity, missing source evidence or unsafe mastery claims in `unresolved` with `status=needs_user`;
   - do not include paths, shell commands or direct-write instructions.
8. Run the deterministic writer in dry-run mode against the same manifest and action document.
9. If dry-run validation passes, invoke apply with `--authorization` equal to the frozen `batch_id`. The exact trigger already supplied authorization, so do not ask again for safe actions.
10. Verify the writer receipt, action results, formal posthashes, schema checks and hash-chain journal.
11. Report `COMPLETE`, `PARTIAL`, `NEEDS_PREPROCESS`, `NOOP` or `FAILED` using references/output-contract.md. Ask one minimal question only when unresolved `needs_user` items exist.

## Semantic boundaries

- An explicit unknown, mistranslation or missed structure is intake evidence, not mastery.
- Guided understanding, a generated example and “我会了” are not independent active-use evidence.
- `mastered_insert` is allowed only when the frozen candidate carries explicit `independent_correct_use` evidence and the formal snapshot has no conflict.
- Existing-bank matches require an update action or skip; never insert a duplicate item.
- A reusable structure uses `sentence_pattern_merge` when an existing SP match is found and `sentence_pattern_append` only when merge-first comparison proves it is new. Concrete verb templates stay in the CSV candidate route.
- Generated examples never become source sentences and never update mastery, `appear_count` or `last_seen` by themselves.

## Failure and partial completion

- No effective capture events on the requested date: report `NOOP`; do not generate filler actions or call the worker.
- Effective captures exist but Luna coverage is missing: run the deployed worker once and refreeze once; remaining gaps are `needs_preprocess`, never `NOOP`.
- Candidate or manifest hash mismatch: report `FAILED` or stale input and do not apply.
- Formal prehash CAS mismatch: stop and require a fresh exact-date invocation before creating a new frozen batch; never override or silently reuse the writer guard.
- Some semantic items ambiguous: apply the safe action subset, retain exact receipts and report `PARTIAL` plus `needs_user`.
- Writer validation or apply failure: report the returned safe error and do not edit formal files as fallback.

## Tools

Use only the CLI commands and paths in references/nightly-curation-contract.md for freeze, dry-run and apply. Use a JSON Schema validator for the typed action document. Do not use patching, CSV libraries, Obsidian edits or shell redirection to mutate formal English data.

## Output

Use references/output-contract.md. Keep chat labels plain and concise. Report the exact date, batch and receipt IDs, applied/no-op/failed counts, changed file names, validation and any unresolved items. Do not repeat full candidate documents or model reasoning.

## Stop rules

- Trigger text is not an exact match: do not use this skill and do not freeze anything.
- Date is invalid: report the invalid date and stop.
- Capture inventory is empty for the exact date: report `NOOP` and stop.
- Freeze lacks Luna coverage for existing captures: perform exactly one preprocessing retry and one refreeze; remaining gaps stop at `needs_preprocess` without invented candidates.
- Manifest or evidence validation fails: do not generate or apply actions.
- Safe actions have a valid dry-run: apply without a second confirmation.
- Ambiguity remains: preserve safe receipts, report `PARTIAL`, ask only for the smallest decision that changes the unresolved actions.

## References

- references/nightly-curation-contract.md: exact freeze, dry-run and apply sequence
- references/typed-actions-contract.md: Sol output boundary and allowed targets
- references/output-contract.md: final receipt and partial-result format

## Local read-only MCP preflight

Eligibility is limited to the frozen English nightly inventory, manifest-bound candidate summaries, formal snapshots, duplicate checks, and named sentence-pattern identities. When eligible, the only allowed MCP binding is the English-only server `kaoyan_english_read`, tool `english_read_bundle`, exposed in Codex as `mcp__kaoyan_english_read__english_read_bundle`. Global namespaces `mcp__kaoyan_read__english_read_bundle`, `mcp__kaoyan_read_v2__english_read_bundle`, and `mcp__kaoyan_read_v3__english_read_bundle` are legacy and must not be called; math and 408 tools are cross-subject and must not be called. Freeze, worker run-once, typed action creation, dry-run, apply, recovery, receipt, and formal mutation remain canonical terminal-only operations.

Every MCP request must use `schema_version=study-read-mcp.v3` and route context for caller `kaoyan-english-daily-intake-curation`, Skill version `2.1.0`, the installed plugin version, a unique route request ID, one canonical evidence-scope hash, ordered chunk metadata, and `consumed_duplicate_read_count=0`. Any failed or mismatched chunk invalidates the complete MCP preparation route.

Before the first eligible call, check the callable task snapshot for `mcp__kaoyan_english_read__english_read_bundle`. If absent, call no MCP tool and record `read_route=terminal_fallback`, `required_mcp_tool=mcp__kaoyan_english_read__english_read_bundle`, `fallback_reason=tool_snapshot_missing`, `chunk_index=0`, and `chunk_count=0`, then run the complete canonical preflight. Other failure, drift, timeout, corruption, or limit responses also require one fresh terminal generation; never mix MCP and fallback evidence.

MCP never freezes a batch, invokes Luna, emits typed actions, applies formal data, or authorizes a write. It must report `formal_write_count=0` and `model_call_count=0`.
