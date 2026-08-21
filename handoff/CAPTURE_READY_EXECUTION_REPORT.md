# Study Intake V2 Capture-Ready Execution Report

- Report date: 2026-08-21 Asia/Shanghai
- Feature branch: `feat/study-intake-v2-capture-ready-v1`
- Terminal status: `BLOCKED_TEST_CONTRACT`
- Real user Capture executed: no
- Hosted model calls executed: 0
- Production deployment changed: no
- Production formal data written: no

## Executive result

The one required precheck passed. Phase 1 source closure and Build-input repair
were completed, tested, committed, and pushed. The trusted old production
formal config and formal-surface baseline were captured before any candidate
deployment and reverified as unchanged.

Phase 2 cannot safely complete under the frozen requirements. Direct source
inspection disproved one execution-brief assumption: the Backend contains the
strict SubjectSol control plane and three writer-adapter identifiers, but it
does not contain or invoke the three production subject writers. English has a
real subject writer in its canonical repository. Math and CS408 do not have a
callable formal writer contract; their current entrypoints are deliberately
capture-only or retired. The Sol review contract also carries decisions and
content hashes, not typed Math or CS408 formal operations.

Creating a new generic writer, treating Capture as a formal writer, reviving a
retired entrypoint, or inventing Math/CS408 card and rollback semantics would
violate the frozen requirements. The run therefore stopped before hosted-model
trial, Commissioning, immutable candidate Build, merge, or deployment.

## Required precheck

All five canonical worktrees matched the requested repository, branch, and
starting HEAD; all were clean and their remote feature refs had no drift.

Both historical bundles exactly matched the fixed raw-byte algorithm:

| Subject | Files | Total bytes | Bundle SHA-256 | Result |
|---|---:|---:|---|---|
| CS408 | 16 | 1,028,860 | `8a0067b3b9d3aa3342fcdeb8186343181467d3daa98f7549924d8359b4848428` | PASS |
| English | 20 | 251,077 | `ca3e027286f3cc32e2fbb4c8f365d281fa4763f9987a70bf0690f3d26a86a356` | PASS |

Production target identity was unique. The only active `current` pointer and
all four existing LaunchAgents resolved to:

`ad1807186ac277c6bacfb7fd8d83b9cbc86cf75027698e70f994f3228d815cc9`

The target was not modified during precheck or implementation.

## Repository checkpoints

These are the pushed Phase 1 or unchanged implementation heads before this
handoff-only commit:

| Repository | Head | Phase 1 disposition |
|---|---|---|
| Backend | `7fd442699c6c6604c4e16cc24710b159e5e80c3d` | Build lanes and formal gate repaired |
| Shared MCP | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | unchanged |
| Math | `7a86f828fca0357fc438755e16505c6a81002636` | unchanged |
| CS408 | `c63bc20b316a4b725e963154ddb7149ced000a20` | manifest and verifier added |
| English | `7aa357700e4d34d9d0bc8a5fef043a79c7d535ce` | manifest, verifier, and four active dependencies restored |

Phase 1 commits pushed in this run:

- Backend `7fd4426` `fix: separate portable build evidence lanes`
- CS408 `c63bc20` `feat: close historical 408 source provenance`
- English `7aa3577` `feat: close historical English source provenance`

## Source closure

CS408 added:

- `schema/study-intake-historical-source-closure-v1.json`
- `scripts/verify_historical_source_closure_408.py`
- `tests/test_historical_source_closure_408.py`

Classification: 15 `replaced_by_current`, one `evidence_only`, zero
`restore_required`. The evidence-only file is the historical
`question_source_attestation_408.py`; no active caller was found, so it was not
restored.

English added the same manifest/verifier boundary and restored only four
canonical files with explicit current Skill callers:

- `prompts/codex_prompt.md`
- `schema/schema.md`
- `schema/protected_exam_analysis.md`
- `schema/reference_grounded_examples.md`

Classification: four `restore_required`, 14 `replaced_by_current`, two
`evidence_only`. The old memory-curve builder and BBDC selector were not
restored. Neither manifest contains a personal absolute path. Canonical mode
does not read the historical roots; external mode requires an explicit root.

## Build inputs and validation

Backend Build behavior now matches its README:

- default test discovery uses repository-owned synthetic fixtures;
- the historical evidence manifest is explicit and optional;
- a non-skipped concurrent Build requires paired `formal_config` and
  `formal_baseline`;
- the pair is passed to the real formal-surface verifier;
- portable config bindings are applied before checking the trusted production
  formal config;
- the component generator can run without an implicit historical fixture.

The old production formal inputs were frozen locally, outside Git:

- config file SHA-256:
  `f385409cd2b0b8ff512db549cae34f1ea3d3f9bd715f3e83d9a31f489ef08c40`
- baseline file SHA-256:
  `4f6dbd9f827bca01c2d2ac0536f2096e23edf36392917493a8d855e6e8bc901c`
- baseline manifest SHA-256:
  `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352`

Candidate `verify_formal_surfaces()` reopened that pair with the production
bindings and returned `passed / unchanged`, identical baseline and current
manifest hashes, and no differences.

Tests run in this execution:

| Scope | Result |
|---|---|
| CS408 historical closure verifier tests | 5/5 PASS |
| CS408 external and canonical verifier commands | PASS |
| English documented suite including closure tests | 53/53 PASS |
| English external and canonical verifier commands | PASS |
| Backend release-manager targeted set | 5/5 PASS |
| Backend formal config/baseline production-binding verification | PASS, unchanged |
| `git diff --check` for Phase 1 commits | PASS |

The previously recorded full suites were not rerun and are not represented as
current final acceptance. The implementation stopped before the main Phase 2
function existed, avoiding a redundant full-suite run that could not change
the blocker.

## Exact Phase 2 contract blocker

Backend facts:

- `lib/subject_sol_contract.py` maps subjects to
  `math_nightly_writer_v1`, `cs408_daily_intake_writer_v1`, and
  `english_daily_intake_writer_v1`.
- The same module explicitly owns control state and receipts, not formal writer
  execution.
- `lib/isolated_authority_publishers.py` can seal and reopen an external writer
  result directory, but no production adapter creates that directory.
- `schemas/sol-review-receipt-v2.json` records adopted/modified/rejected
  decisions and replacement hashes; it does not define executable Math or
  CS408 formal operations.

Subject facts:

- Math `quick_intake.py` is a pending-nightly Capture/freeze/closeout ledger
  writer with `formal_write_count=0`. The formal-card `wrongnet.py new`
  implementation is intentionally unreachable and returns failure.
- CS408 `scripts/intake_apply_408.py` is an explicitly retired negative
  entrypoint. Current Capture code fixes `formal_write_count=0`.
- English `english_pipeline.writer.apply_nightly()` is a real, test-store-safe
  formal writer, but no current Backend adapter translates its transaction
  artifacts into the SubjectSol execution-result contract.

Consequently, a real Terra -> Luna -> Terra -> Sol chain cannot close for all
three subjects without first supplying approved Math and CS408 typed writer
contracts and subject-specific implementations. This is a contract blocker,
not a routine missing import or test repair.

## Not run

- live successor driver integration into `CodexRunner.run()`
- pure-synthetic hosted Terra/Luna/Terra trial
- isolated synthetic SubjectSol writer commit
- four Commissioning actions
- Backend immutable candidate Build and strict verify
- PR merge
- deployment and successor health check
- first real Capture
- Commissioning cleanup

The existing `consumer_stage_chain_live_driver_not_integrated` fail-closed
guard remains in place. No fallback to an old Luna/Luna or subject chain was
added.

## Privacy and production attestation

- No real Capture was invoked.
- No hosted model received real or synthetic study content in this run.
- No production formal record, Capture ledger, queue, receipt, gate, service,
  LaunchAgent, or `current` pointer was changed.
- Historical roots were used only for the fixed source-bundle bytes.
- The required formal baseline tool read current formal surfaces locally to
  compute paths, sizes, and hashes. No formal content, question, article,
  attachment, database, or baseline artifact was uploaded or committed.

## Required next decision

Before capture-ready implementation can resume, approve or supply:

1. a typed `math_nightly_writer_v1` operation and transaction contract;
2. a typed `cs408_daily_intake_writer_v1` operation and transaction contract;
3. the exact bridge from Sol review replacement packages to those operations;
4. whether the existing English writer should be wrapped by the current
   SubjectSol result-directory contract.

After those business contracts are frozen, the existing lease, fencing,
receipt, source-closure, Build-input, and English writer components can be
reused. Until then, merge and deployment would falsely advertise a working Sol
write stage.

## Terminal decision

`BLOCKED_TEST_CONTRACT`

This report does not claim `READY_TO_MERGE`, `READY_TO_DEPLOY`, or
`READY_FOR_USER_CAPTURE`.
