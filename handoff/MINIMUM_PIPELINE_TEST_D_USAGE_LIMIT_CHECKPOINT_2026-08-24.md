# Study Intake V2 minimum-pipeline WIP checkpoint

Status: `WIP_CHECKPOINT_USAGE_LIMIT_BLOCKED`

This branch is intentionally published as an inspectable engineering checkpoint. It is not Build-ready, deployable, activated, or accepted for production.

## Source identity before this checkpoint commit

- Branch: `codex/minimum-pipeline-simplification-single-build-20260823`
- Base HEAD: `9f35a982940d5300f2840af2b08afee7c4017d93`
- Base tree: `86c654330f7c09ea0a1cae604317c8a2dc0c3a48`
- Pre-commit working diff SHA-256: `50ab829437d696f7cb45758d5aec0655991e10fbb37de1e4001af618d6b5b89e`
- Preserved original Phase 1 WIP diff SHA-256: `ef868e97a89799700bb2aa64043706f6647094ea7463dc9bcc38c538a1e07742`

## Implemented direction

- Added a thin direct source-acceptance path using the current Backend checkout, current dispatcher, current task runner, current Shared MCP, and per-Task temporary roots.
- Kept Backend `subject`, `capture_id`, and `unit_sha256` authoritative when model report bindings are wrong or incomplete.
- Kept raw Provider output first and normalized non-critical report defects to warning or needs-review state.
- Added deterministic three-subject by two-Task zero-model concurrency coverage.
- Added real-writer isolated Capture preparation for Math, CS408, and English.
- Removed the post-model generation rescan for an already frozen AnalysisPackage task.
- Preserved direct-task stderr, resolved config, frozen task input, and failure diagnostics in compact task evidence.
- Treated one content-equivalent duplicate MCP read, recoverable cursor-before-first-page usage, and safe `NOT_FOUND` exploration as review-quality conditions when required reads still complete. Provider non-zero exit, unreadable MCP, authority drift, malformed envelopes, and missing required reads remain technical failures.

No new persistent schema, store, controller, supervisor, dispatcher, authority framework, source mirror, historical driver, or per-subject Backend copy was introduced.

## Verified gates

- Test A: PASS
- Test B: PASS
- Test C: PASS
- Latest full real-writer Test Capture 1: PASS
  - Math: 2 Capture / 2 Task
  - CS408: 2 Capture / 2 Task
  - English: 2 Capture / 2 Task
  - Provider requests: 0
  - Formal writes: 0
  - Production writes: 0
- Affected targeted groups: PASS
  - 138 tests passed across source acceptance, real-producer, MCP architecture/replay, task-runner, review-terminal, and AnalysisPackage groups.
  - The latest source-acceptance group passed 23 tests after interval evidence repair.

## Real Test D history

Round 05 reached real six-Task concurrency and all three model roles. Its recorded summary contained:

- Total Tasks: 6
- Technical completed: 2
- Completed reports: 2, both `needs_review`
- Recorded Provider requests: 80
- Recorded model calls: 6
- Remaining first technical breakpoints:
  - CS408: `cs408_analysis_mcp_cursor_without_first_page`
  - CS408: `stale_input_superseded`
  - English: `english_critical_review_mcp_server_not_found`
  - Math: `math_luna_analysis_mcp_duplicate_read`

Exact regression-tested fixes for those four breakpoints are present in this checkpoint, but the next complete Test D could not exercise them because the Provider account limit was reached.

Round 06 created six fresh Tasks and stopped at the external Provider gate:

- Math: `math_analysis_usage_limit`
- CS408: `cs408_analysis_usage_limit`
- English: `english_analysis_usage_limit`
- Technical completed: 0
- Provider requests: 0
- Model calls: 0

An independent minimal Terra availability probe returned the same account-level response:

```text
You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Aug 27th, 2026 11:36 AM.
```

## Safety state

- Formal write count: 0
- Production write count: 0
- Production `current`: unchanged
- LaunchAgents: unchanged
- Deployment: not run
- Activation: not run
- Production Capture: not run
- Build count: 0
- Immutable verify: not run

Local Test D and Test Capture evidence trees are intentionally not committed because they contain machine-local paths and temporary test artifacts. Their compact, non-sensitive outcomes are recorded above.

## Resume action

After Provider credits are available, continue this exact branch and worktree:

1. Run a fresh six-Task Test D round without serializing subjects or same-subject Tasks.
2. Require all six Tasks to finish Terra analysis, Luna analysis, Terra final, and readable final report.
3. Run Test E against clean, warning, and needs-review reports.
4. Run final regression.
5. Only then create the source-freeze commit, Build, and strict immutable verify.

Do not deploy, activate, switch production `current`, mutate LaunchAgents, create a production Capture, or perform formal apply from this checkpoint.
