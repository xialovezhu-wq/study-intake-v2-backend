# Paused Three-Subject Capture Repair Handoff

- Date: 2026-08-22, Asia/Shanghai
- Status: `PAUSED_FOR_LOCAL_WORKFLOW_ARCHITECTURE_AUDIT`
- Feature branch: `codex/three-subject-capture-unified-repair-20260822`
- Canonical baseline: `0b7b8da44b670bd901764e12752a1c4ba0da287a`
- Production Build: not run
- Deployment: not run
- Real Capture creation: 0
- Real Math recovery: not run
- Provider, MCP, Terra, or Luna calls caused by this repair: 0
- Formal writes: 0

## Pause reason

The user paused implementation while re-evaluating the validation model for the
entire local background workflow. The current WIP was built around a Release 1
task-capability compatibility layer. That direction may be replaced by a
lighter local JobStore workflow, so no further production logic, tests, Build,
deployment, or recovery may continue until the architecture audit concludes.

The pause is intentional. It is not a test failure, deployment rollback, or
production incident resolution.

## Original task objective

The original authorized task was to repair three production blockers without
creating new real Captures or performing formal writes:

1. issue a per-task JIT authorization from verified foreground evidence;
2. consume it atomically through the existing Dispatcher and `LeaseStore`;
3. normalize Dashboard technical failures through one execution/quality
   contract and support deterministic projection rebuild;
4. add a shared CS408 Producer finalizer for ordinary, managed-hot, and
   recovery paths;
5. preserve the existing English microbatch identity while binding every
   member receipt and attestation;
6. support a controlled new attempt for the existing Math Capture while
   preserving the original failure.

The user later narrowed the implementation to code, zero-model tests, full
regression, commit, and push only, stopping at `READY_FOR_GITHUB_REVIEW`.
Build, deployment, and real Math recovery were explicitly removed from scope.

## Preflight result

All five canonical repositories were clean at task start, matched their
expected baselines, and matched local tracking plus remote `main`:

| Repository | Baseline |
|---|---|
| Backend | `0b7b8da44b670bd901764e12752a1c4ba0da287a` |
| Shared MCP | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` |
| Math | `8dfe49dcb6e0784a716ac87248039212737ed63b` |
| CS408 | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` |
| English | `2121171cc59194e41f5a4863723295593c3d0c58` |

The verified production baseline during preflight was:

- current deployment:
  `da9b8831df0d78567bbd3edf2767ccef4f00c3e90a35a4942a016659df9a0efa`;
- formal-surface digest:
  `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352`;
- formal-write count: 0;
- original Math failed unit:
  `14be5454fa845f172a86d22ebf323d30de7e6d782a573e4ae3da3a186357fbf5`;
- original Math terminal error: `manual_live_authorization_missing`;
- original Math model, Provider, MCP, and formal counts: `0 / 0 / 0 / 0`;
- accepted Math package: absent;
- successful Math recovery: absent;
- Math Capture, source bundle, descriptor, and Producer attestation:
  independently revalidated;
- later independent Math preclaim unit:
  `64dff428786cc68f61db0e66957915ce1affb85fed35d241db4c26265ff54d57`;
- later preclaim error: `production_canary_producer_binding_mismatch`.

English preflight proved that the existing ordered event IDs, event hashes,
per-event Producer attestations, `content_processing_id`,
`frozen_payload_sha256`, and `unit_sha256` already close the microbatch
identity. No English production identity change was justified.

CS408 preflight proved that ordinary cold Capture publishes a Producer
attestation, while the managed-hot path can reach ready handoff without the
same shared finalization.

## Completed work

### Committed Backend tests

Commit `45768c6` added failing-reproduction coverage for:

- task execution authorization v2 shape and exact binding;
- capability claim under the existing `LeaseStore` lock;
- terminal consumption and legacy-v1 isolation;
- missing and cross-subject capability rejection;
- three-subject technical-failure projection mapping;
- English ordinary microbatch per-member receipt manifest.

### Committed Dashboard contract fix

Commit `a07b1d4` changed `lib/dashboard_projection.py` so v2 batch terminal
projection reuses `decide_execution_quality(...)` instead of maintaining a
second handwritten mapping. The targeted test now maps legal technical
failures to:

```text
execution_status = failed
quality_status = unchecked
report_disposition = technical_failure
sol_review_status = not_eligible
formal_write_eligible = false
production_accepted = false
```

This is the only completed reusable production fix in the Backend branch.

### Committed CS408 tests

Commit `3247ee6` added failing-reproduction tests for:

- a shared Producer finalizer that reopens the same sidecar and fails closed on
  conflicting bytes;
- a managed prepared-pack Capture that must publish a Producer sidecar before
  the handoff is ready.

No CS408 production implementation was written.

## Current repository state before this handoff file

### Backend

```text
branch: codex/three-subject-capture-unified-repair-20260822
baseline: 0b7b8da44b670bd901764e12752a1c4ba0da287a
HEAD: a07b1d4a894e70e181012e9d0cc0df3f74c02445
```

Committed delta from baseline:

```text
lib/dashboard_projection.py                   |  81 +++++---
tests/test_dashboard_projection_merge.py      |  42 ++++
tests/test_english_adapter.py                 | 100 +++++++++
tests/test_task_execution_authorization_v2.py | 279 ++++++++++++++++++++++++++
4 files changed, 478 insertions(+), 24 deletions(-)
```

Uncommitted WIP before adding this handoff:

```text
bin/preprocess_dispatcher.py    |  78 +++-
bin/preprocess_task_runner.py   |  47 ++-
config.example.json             |   5 +
lib/concurrent_dispatch.py      | 773 +++++++++++++++++++++++++++++++++++++++-
lib/core_dispatch_bridge.py     | 267 ++++++++++++++
lib/live_execution_gate.py      |  64 ++++
lib/manual_capture_admission.py | 248 +++++++++++++
lib/preprocessor_core.py        |  28 ++
8 files changed, 1506 insertions(+), 4 deletions(-)
```

### CS408

```text
branch: codex/three-subject-capture-unified-repair-20260822
baseline: 09e811e97f7e99ece7cab1751aca86ec92a7c41e
HEAD: 3247ee642d976af20bbf5a2cb788f93a6d7dc320
working tree: clean
```

Committed delta from baseline:

```text
tests/test_intake_fact_capture_408.py                         | 75 ++++++++++++++++++++++
tests/test_morning_review_prepared_pack_managed_hot_408.py   | 21 +++++-
2 files changed, 95 insertions(+), 1 deletion(-)
```

### Shared MCP, Math, and English

All three remained on `main`, at their exact baselines, with clean working
trees and no branch delta.

## File classification

| File | Classification | Assessment |
|---|---|---|
| `tests/test_dashboard_projection_merge.py` | `reusable_test` | Reproduces the three-subject terminal mapping defect. It is not yet a full producer-to-validator integration test. |
| `tests/test_english_adapter.py` | `reusable_test` | Tests an ordered per-member source-receipt manifest without changing English task identity. The current reader assertions may need simplification after the architecture audit. |
| `tests/test_task_execution_authorization_v2.py` | `reusable_test` and `transitional_implementation` contract test | Useful for exact identity, single claim, and formal-write denial. The capability artifact details are tied to the current Release 1 proposal and may need replacement. |
| `lib/dashboard_projection.py` | `reusable_contract_fix` | Eliminates duplicate execution/quality mapping and reuses the existing pure shared contract. Likely permanent. |
| `tests/test_intake_fact_capture_408.py` | `reusable_test` | Failing test for shared finalizer idempotency and no-clobber conflict. |
| `tests/test_morning_review_prepared_pack_managed_hot_408.py` | `reusable_test` | Failing test for managed sidecar before ready handoff. |
| `lib/manual_capture_admission.py` | `transitional_implementation`, `incomplete_or_unsafe` | Adds a large strict v2 capability shape. It has not been reconciled with the latest tolerant-reader and minimal-hard-gate requirements. |
| `lib/concurrent_dispatch.py` | `transitional_implementation`, `incomplete_or_unsafe` | Adds Release 1 capability issue/claim/terminal artifacts around existing leases. It is large, not fully integrated, and not regression-tested after the latest edits. Review for deletion or major reduction. |
| `lib/core_dispatch_bridge.py` | `likely_obsolete_after_audit`, `incomplete_or_unsafe` | Adds a strict subject receipt-manifest reader. It currently imposes path and metadata checks beyond the latest allowed hard gates and should not be treated as safe. |
| `lib/live_execution_gate.py` | `transitional_implementation`, `incomplete_or_unsafe` | Adds v2 capability validation for provider launch. The end-to-end claim context was not tested. |
| `lib/preprocessor_core.py` | `transitional_implementation`, `incomplete_or_unsafe` | Adds ephemeral capability plumbing into provider launch. The last change was applied immediately before pause and was not tested. |
| `bin/preprocess_dispatcher.py` | `transitional_implementation`, `incomplete_or_unsafe` | Adds JIT issuer/submission wiring. It is not fully tested and may be replaced by local JobStore orchestration. |
| `bin/preprocess_task_runner.py` | `transitional_implementation`, `incomplete_or_unsafe` | Adds claimed-capability reopen and task identity injection. Not regression-tested. |
| `config.example.json` | `likely_obsolete_after_audit`, `incomplete_or_unsafe` | Adds a Release 1 compatibility flag. Keep only if the audit retains this seam. |

No Math recovery safety test was created. No Dashboard producer-to-validator
integration test was created. Those requested test categories remain absent.

## Test evidence

Only targeted zero-model tests were run. No full suite was run.

### Expected red results before implementation

| Test | Result |
|---|---|
| `tests.test_task_execution_authorization_v2` | ImportError: v2 builder absent |
| three-subject technical failure mapping | 3 failures: `report_disposition` was `null` |
| English authorization manifest test | ImportError: manifest builder absent |
| CS408 shared finalizer test | AttributeError: finalizer absent |
| CS408 managed sidecar test | failure: sidecar absent |

### Later targeted observations

| Test | Last observed result |
|---|---|
| Dashboard technical failure plus quality-finding tests | 2/2 PASS |
| `SemanticReuseProjectionTests` | 7/7 PASS |
| task execution authorization v2 unit tests | 4/4 PASS at an intermediate WIP state |
| English ordinary microbatch manifest test | 1/1 PASS after path normalization repair |
| CS408 shared finalizer test | still red; no production implementation |
| CS408 managed sidecar test | still red; no production implementation |

The authorization, English, and task-runner WIP changed again after some of
these targeted observations. They are not evidence that the current frozen WIP
passes. Per the pause instruction, no new tests were run during closeout.

## Work not completed

- no stable Release 1 capability interface was accepted;
- no tolerant-reader review of the WIP authorization manifest was completed;
- no end-to-end issuer to `LeaseStore.claim` to task runner to terminal test
  was completed;
- no crash-window or orphaned capability proof was completed;
- no deterministic Dashboard projection rebuild entrypoint was implemented;
- no Dashboard producer-to-validator integration test was implemented;
- no offending-item diagnostic was implemented;
- no CS408 shared Producer finalizer was implemented;
- no managed-hot handoff schema was updated;
- no English quick-flush capability integration test was completed;
- no Math controlled-recovery implementation or safety test was created;
- no targeted cross-repo compatibility run was completed;
- no full regression suite was run;
- no final release descriptor or component lock was updated;
- no Build, preview, deploy, projection rebuild, or real recovery occurred.

## Generated and runtime file audit

At freeze time:

- Backend had no untracked files before this handoff was added;
- CS408 had no untracked files;
- Shared MCP, Math, and English had no untracked files;
- no `.pyc`, cache, release, build, runtime, queue, receipt, or Capture file was
  present in the canonical worktree diffs;
- no production runtime path was modified by the implementation;
- this handoff Markdown file is the only closeout file added after the frozen
  diff snapshot above.

## Permanent changes

The strongest candidate for a permanent change is:

- `lib/dashboard_projection.py`: reuse of
  `decide_execution_quality(...)` for terminal projection.

The following tests are likely reusable regardless of the final local job
architecture:

- the technical-failure mapping test;
- the CS408 managed-sidecar-before-ready test;
- the CS408 no-clobber finalizer test;
- the English ordered member/receipt test after reducing it to the final hard
  gates.

## Transitional compatibility code

The uncommitted Backend WIP in these files is explicitly transitional:

- `lib/manual_capture_admission.py`;
- `lib/concurrent_dispatch.py`;
- `lib/live_execution_gate.py`;
- `lib/core_dispatch_bridge.py`;
- `lib/preprocessor_core.py`;
- `bin/preprocess_dispatcher.py`;
- `bin/preprocess_task_runner.py`;
- `config.example.json`.

It was intended to bridge the current Dispatcher to a one-shot task capability
without creating a second lease/heartbeat/worker/terminal lifecycle. The
integration is incomplete and must not be deployed.

## Future replacement seam

If the architecture audit retains a thin authorization bridge, the intended
seam is limited to three Backend-owned operations:

1. issue an immutable capability from Backend-derived task and Producer proof;
2. validate and consume that capability inside the existing
   `LeaseStore.claim` lock;
3. bind the capability digest to the existing completion and terminal.

Foreground Capture remains release-neutral. Adapters must not depend on the
capability artifact directory layout. Existing lease, fence, heartbeat,
terminal, and recovery remain the only lifecycle state.

## Files removable after local JobStore migration

If local JobStore becomes the authoritative task and authorization boundary,
the following WIP should be reviewed for removal rather than completion:

- all uncommitted additions in `lib/manual_capture_admission.py`;
- all uncommitted task-execution-authorization additions in
  `lib/concurrent_dispatch.py`;
- all uncommitted v2 branches in `lib/live_execution_gate.py`;
- `task_authorization_capture_manifest` and related strict receipt helpers in
  `lib/core_dispatch_bridge.py`;
- ephemeral authorization injection in `lib/preprocessor_core.py`;
- JIT issuer wiring in `bin/preprocess_dispatcher.py`;
- claimed-capability reopen wiring in `bin/preprocess_task_runner.py`;
- the `task_execution_authorization_v2` block in `config.example.json`;
- capability-layout-specific assertions in
  `tests/test_task_execution_authorization_v2.py`.

The Dashboard shared-contract fix and subject-behavior tests should be reviewed
separately and need not be removed with the compatibility layer.

## Resume conditions

Do not resume implementation until all of the following are explicitly
decided:

1. whether local JobStore or the current Dispatcher owns task authorization;
2. the minimal permanent hard gates for Capture admission;
3. the tolerant-reader policy for old and extra Capture fields;
4. whether source receipt identity is derived by Backend or persisted by the
   foreground workflow;
5. whether a capability artifact is still needed after local JobStore
   migration;
6. the exact permanent seam between foreground Capture, Producer proof,
   task claim, and terminal;
7. whether the current WIP should be reduced, replaced, or deleted in a new
   clean worktree;
8. fresh confirmation that production, canonical branches, and formal surface
   have not drifted.

Resume only under a new explicit execution specification. Begin with a
read-only architecture audit in an independent clean worktree. Do not use this
WIP branch as production-ready evidence.

`PAUSED_FOR_LOCAL_WORKFLOW_ARCHITECTURE_AUDIT`
