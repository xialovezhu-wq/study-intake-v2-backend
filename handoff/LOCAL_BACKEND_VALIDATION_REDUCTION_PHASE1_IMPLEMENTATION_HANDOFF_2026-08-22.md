# Local Backend Validation Reduction Phase 1 Implementation Handoff

Generated: 2026-08-22, Asia/Shanghai

Status: `READY_FOR_GITHUB_REVIEW`

This handoff records source, test, and zero-model evidence only. No Build,
deployment preview, deployment, production Capture, Math recovery, real model,
Provider, production MCP, or formal write was performed.

## Repository heads

| repository | validated head | Phase 1 change |
|---|---|---|
| Backend | `c27f5509610cc03c74d8cc7299f739f5669b221a` | code and tests committed on the feature branch |
| Shared MCP | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | unchanged |
| Math | `8dfe49dcb6e0784a716ac87248039212737ed63b` | unchanged |
| CS408 | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` | unchanged; temporary detached `main` used for regression |
| English | `2121171cc59194e41f5a4863723295593c3d0c58` | unchanged |

Backend feature branch:

```text
codex/local-backend-validation-reduction-phase1-20260822
```

## Branch commits before this handoff

| commit | title | files changed |
|---|---|---:|
| `f1784d5` | `fix: reduce local capture validation gates` | 4 |
| `bb9a869` | `fix: isolate dashboard item failures` | 2 |
| `c27f550` | `test: cover real producer backend zero-model flow` | 7 |

The branch was created directly from Backend `main` at
`0b7b8da44b670bd901764e12752a1c4ba0da287a`. No paused-WIP commit was
cherry-picked.

## Scope metrics

Production code:

```text
production_files_changed=6
production_lines_added=1216
production_lines_deleted=263
production_net_lines_added=953
new_schema_families=0
new_persistent_roots=0
new_lifecycles=0
new_submit_apis=0
new_job_or_report_stores=0
```

Tests before documentation commit:

```text
test_files_changed=7
test_lines_added=1986
test_lines_deleted=23
```

The production delta remains below the Phase 1 stop boundaries: six production
files, 953 net added lines, no new schema family, no new persistence root, and
no second submit, lease, terminal, recovery, JobStore, or ReportStore lifecycle.

## Validation reduction matrix

| check | old behavior | new behavior | classification | file/function | test |
|---|---|---|---|---|---|
| local execution admission | claimed local Capture without manual authorization failed | exact task context reopens the current claimed lease and existing subject route | hard gate reduced | `lib/live_execution_gate.py::assert_external_launch_allowed` | `LocalTrustedExecutionGateTests` |
| submit path | normal daemon inherited production-canary and SubjectSol generation admission | normal daemon uses existing `ConcurrentDispatcher.submit` with ordinary `LeaseStore.claim` | control-plane gate removed | `bin/preprocess_dispatcher.py::ProductionDispatchRuntime.scan_and_submit` | `OrdinaryLocalSubmitTests` |
| historical backlog | non-canary cutoff was Math-only | existing release-bound producer high-water applies to all subjects by durable `recorded_at` | hard gate retained, scope corrected | `scan_eligible_candidates`, `_local_capture_recorded_after` | `ScannerReductionTests` and real-shape cutoff cases |
| invalid Capture time | invalid or absent time could be classified as historical | invalid time is visible as `needs_review` | item-local review | `scan_eligible_candidates` | `test_missing_recorded_at_is_needs_review_not_historical_exclusion` |
| Producer attestation absent | missing sidecar rejected the Capture | any missing member sidecar is a warning; an existing conflicting sidecar remains `needs_review` | hard gate reduced | `_required_producer_attestation_missing` | attestation missing, partial microbatch, and conflict tests |
| unknown Producer fields | actual extra fields could be rejected or unreported | bounded adapters ignore them for admission and attach `producer_unknown_fields_ignored` | warning | Math/CS408/English adapter paths | actual Producer unknown-field cases |
| CS408 managed first-turn shape | current trace digest and freeze receipt were interpreted as old shapes | current normalized trace digest and both verified first-turn receipt forms are accepted | bounded known-shape conversion | CS408 handoff validators in `lib/preprocessor_core.py` | managed 408 real-shape test |
| English raw parent | raw dialogue event and `parent_raw_capture_id` could reject the scan | raw dialogue remains immutable provenance and sentence parent identity is accepted | bounded known-shape conversion | English event loader/validator | English raw-turn to Capture test |
| raw spool durability | final raw object was durable, but the Provider spool itself was not fsynced | task spool is flushed and fsynced before raw publication; fsync failure blocks raw/Report success | durability gate retained | Provider finalization in `lib/preprocessor_core.py` | raw order and fsync failure tests |
| AnalysisPackage terminal | `analysis_package_ready` could fall through the old two-pass terminal contract | local ordinary runner bridges the existing AnalysisPackage into existing `publish_terminal` | existing lifecycle reused | `CoreCandidateRunner` | `AnalysisPackageTerminalBridgeTests` |
| optional Report fields | tolerant canonical normalization already existed | behavior retained and expanded regression verifies missing/malformed/unknown advisory fields | warning/default | `lib/analysis_package_v1.py` unchanged | `test_missing_optional_model_fields_normalize_to_warning_and_remain_ready` |
| terminal status mapping | legal technical failure could yield a null disposition | shared execution/quality contract yields `technical_failure` | shared mapping | `lib/dashboard_projection.py` | three-subject technical failure test |
| malformed Dashboard item | one malformed item could invalidate or disappear from the whole root | minimum diagnostic placeholder remains visible; siblings and health state remain available | item-local failure | `dashboard/server.py` | malformed Mapping, non-Mapping, release, axis, list/detail tests |

## Producer alignment matrix

| subject | actual entrypoint | raw shape used | manual fields added | Backend result | task materialized | claim count | runner boundary |
|---|---|---|---|---|---:|---:|---|
| Math | `quick_intake.py::cmd_record` writer-level foreground entry | actual v2 event ledger and `pending` projection | false | normalized; capture ID preserved | yes | 1 | reached |
| CS408 managed | `managed_408_current_turn.run_current_question_turn` through the hot Capture writer | actual managed bundle, handoff, receipt, sidecar and capture state | false | normalized; capture ID preserved | yes | 1 | reached |
| CS408 ordinary negative | `intake_fact_capture_408.capture` | actual ordinary fact Capture without required current-question evidence | false | visible `needs_review`; raw Capture retained | no, by design | 0 | not entered |
| English quick flush | `capture-raw-turn` then `capture --quick-flush` | actual raw-turn, sentence event, receipt and signed quick-flush intent | false | normalized; source event IDs preserved | yes | 1 | reached |
| English closed batch | `complete-article` | actual `article_completed` plus remaining immutable sentence events | false | normalized; source event IDs preserved | yes | 1 | reached |

The Math repository exposes the durable writer but not an independently
callable higher-level interactive payload producer; the acceptance claim is
therefore deliberately writer-level, not a claim that a separate Skill UI was
executed.

Each runner-bound task was submitted through two dispatcher instances while
active and once after completion. The test reopened task events and observed
exactly one claim event, `deduplicated_active` during the active lease, and
`deduplicated` after completion.

## Real-shape zero-model safety evidence

```text
real_terra_call_count=0
real_luna_call_count=0
real_provider_model_request_count=0
production_mcp_task_count=0
formal_write_count=0
production_runtime_write_count=0
```

Evidence basis:

- every Producer and Backend state root was a temporary directory;
- canonical Producer source bytes were checked against each repository's
  exact `main` ref before use;
- Provider and production MCP construction paths were patched as tripwires and
  remained uncalled;
- dispatcher claim events, rather than literal counters, proved claim-once;
- Producer ledgers/event trees and CS408 private evidence trees were hashed
  before and after Backend dispatch and remained byte-identical;
- no learning body or attachment content is reproduced in this handoff.

## Raw and Report evidence

```text
raw_before_normalization=true
spool_created_before_provider=true
spool_fsync_verified=true
raw_publish_before_exit_receipt=true
normalization_failure_model_retry_count=0
optional_summary_defaulted=true
optional_proposals_defaulted=true
optional_duplicates_defaulted=true
optional_warnings_defaulted=true
unknown_advisory_fields_warned=true
technical_report_publishable=true
analysis_package_terminal_publishable=true
report_ref_status=present_and_reopened_in_isolated_completion
```

Temporary content-addressed refs were verified during tests and intentionally
were not copied into source control or this handoff.

## Dashboard evidence

```text
technical_failure_visible=true
needs_review_visible=true
placeholder_visible=true
other_items_visible=true
healthz_item_failure_delta=none
root_corruption_behavior=strict_degraded
placeholder_detail_reads_private_task_detail=false
```

Top-level shape, dispatcher topology, subject topology, root freshness and
service loss remain fail-closed. Only task-item contract errors are isolated.

## Test results

| suite | command | passed | failed | skipped | duration | head SHA |
|---|---|---:|---:|---:|---:|---|
| Backend Core full discovery | `python3 -m unittest discover -s tests -p 'test_*.py' -q` | 1208 | 0 | 0 | 807.893 s | `c27f5509610cc03c74d8cc7299f739f5669b221a` |
| Dashboard full discovery | `python3 -m unittest discover -s dashboard/tests -p 'test_*.py' -q` | 128 | 0 | 0 | 4.405 s | `c27f5509610cc03c74d8cc7299f739f5669b221a` |
| frontend view-model | `node --test dashboard/tests/task_view_model_test.js` | 1 test / 18 assertions | 0 | 0 | 0.080 s | `c27f5509610cc03c74d8cc7299f739f5669b221a` |
| final targeted safety and real-shape | `python3 -m unittest tests.test_local_backend_validation_reduction tests.test_real_producer_backend_zero_model tests.test_formal_surface_manifest -q` | 18 | 0 | 0 | 1.245 s | `c27f5509610cc03c74d8cc7299f739f5669b221a` |
| Math foreground | `python3 -m unittest tests.test_quick_intake -q` | 66 | 0 | 0 | 0.834 s | `8dfe49dcb6e0784a716ac87248039212737ed63b` |
| CS408 foreground and managed on detached main | `python3 -m unittest tests.test_intake_fact_capture_408 tests.test_morning_review_prepared_pack_managed_hot_408 -q` | 66 | 0 | 0 | 0.837 s | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` |
| English Capture, microbatch, quick flush and raw turn | `python3 -m unittest tests.english_pipeline.test_pipeline tests.english_pipeline.test_quick_flush tests.english_pipeline.test_raw_dialogue_turn -q` | 48 | 0 | 0 | 1.393 s | `2121171cc59194e41f5a4863723295593c3d0c58` |
| Shared MCP full | `python -m unittest discover -q` | 78 | 0 | 0 | 8.184 s | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` |

The Shared MCP suite used its existing local dependency venv with its editable
source pointer temporarily rebound to the canonical checkout; the pointer was
restored immediately after the run. No Shared MCP source file changed.

## Math recovery readiness

```text
capture_id=MFI-CAP-7b84f5dee8bc2846f5431c58
original_unit_sha256=14be5454fa845f172a86d22ebf323d30de7e6d782a573e4ae3da3a186357fbf5
original_terminal=manual_live_authorization_missing
original_model_call_count=0
original_provider_request_count=0
original_mcp_tool_call_count=0
original_formal_write_count=0
accepted_report_exists=false
recovery_readiness=MATH_RECOVERY_READY
```

No recovery was attempted. The original failed terminal remains unchanged.

## Deferred Phase 1 items

- CS408 diagnostic finalizer;
- partial-response crash recovery;
- explicit reprocess;
- multi-attempt identity;
- broad legacy migration;
- missing Capture ID alias;
- production Build and immutable verify;
- deployment preview or deployment;
- actual Math recovery;
- live shadow;
- real Terra/Luna/Provider/MCP acceptance;
- production Capture acceptance.

## Final classification

```text
READY_FOR_GITHUB_REVIEW
```

This does not claim `READY_FOR_FIRST_REAL_CAPTURE`,
`PRODUCTION_CAPTURE_ACCEPTED`, or `FULLY_DEPLOYED`.
