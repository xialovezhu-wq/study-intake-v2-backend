# Study Intake V2 Nightly Sol Alignment Targeted Repair Report

- Report date: 2026-08-21 Asia/Shanghai
- Branch: `feat/study-intake-v2-capture-ready-v1`
- Terminal status after this handoff commit is pushed: `READY_FOR_GITHUB_REVIEW`
- Real Capture executed: no
- Real formal intake executed: no
- Merge or deployment executed: no

## Result

The four post-push review findings are repaired without introducing another
writer platform. Fallible Terra/Luna output is now persisted before a tolerant
normalization boundary, while the resulting report and Analysis Package remain
strict and content-addressed. Exact subject-thread commands now create a bound
authorization, freeze the exact `subject + capture_intake_date` set, enter the
existing `SubjectSolRuntimeStore`, acquire the existing global claim/fencing
token, invoke the subject adapter's `execute` path, validate the subject-native
terminal receipt, and finish through the existing isolated review/apply receipt
publishers and SubjectSol commit state.

The former `NightlySolStore`, simplified `GlobalWriterLease`, parallel result
store, conflict store, and resolution store no longer exist. Hard conflicts are
sealed as SubjectSol continuation receipts. Safe A/C items commit before the
lease is released; a user answer requeues the same original batch and calls the
adapter only for B. A failed resumed attempt restores the partial-commit anchor
and remains retryable without rerunning analysis or changing A/C.

## Historical blocker and review findings

`handoff/CAPTURE_READY_EXECUTION_REPORT.md` is a historical report. Its
`BLOCKED_TEST_CONTRACT` conclusion described the earlier canonical source before
the subject-native flows and thin adapters were aligned. It is now explicitly
marked superseded.

The later GitHub review identified four current defects:

1. structurally incomplete model reports were rejected after durable output;
2. exact commands, batches, adapters and native flows were not connected;
3. `nightly_sol_alignment.py` introduced a parallel state/lease platform;
4. hard-conflict resume declared a one-item scope but did not execute it.

This report records the actual fixes for those findings. It does not reinterpret
the old blocker as a current result.

## Repository checkpoints

| Repository | Starting head | Final code head before this report |
|---|---|---|
| Backend | `a1902dd6d7819ef01d76851a9eb7420f78e95209` | `fce4107` |
| Shared MCP | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | unchanged |
| Math | `412fd8c4600a459bfc0e9d946f51824069b3595c` | `8dfe49dcb6e0784a716ac87248039212737ed63b` |
| CS408 | `fd8459f8a26ece7495a981694b71088a1c6039db` | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` |
| English | `d137509b9d0a7b348b4fb29692e8e8955a20bf81` | `2121171cc59194e41f5a4863723295593c3d0c58` |

The Backend feature head after this report-only commit is the commit containing
this file. Post-push verification records the exact local and remote heads.

## Targeted repairs

### Tolerant model-output normalization

- exact raw model output and its raw reference are required before normalization;
- the package publishes a separate execution receipt and normalization receipt;
- missing or malformed summary/proposals/duplicates/warnings/evidence fields
  become safe defaults plus explicit normalization warnings;
- no learning fact is synthesized to fill a missing model field;
- unusable semantic output keeps the durable raw reference and is marked
  `normalization_status=incomplete`;
- Terra/Luna disagreement remains advisory warning evidence;
- only missing or invalid durable raw, receipt, or hash bindings fail the stage;
- legacy v1 report and nightly-batch schemas remain byte-identical, while the
  new canonical contracts use independent v2 schemas.

### Exact command to SubjectSol commit

- all three absolute commands and all three resolved-today commands enter the
  matching subject adapter;
- ordinary phrases such as `快速入库`, `记录一下`, and `今天做过` are rejected;
- the normalized absolute command is bound by SHA-256 and `NAUTH-*` identity;
- the exact Analysis Package set is admitted as the existing
  `subject_luna_batch_v2` state;
- the existing user-intent publisher, global FIFO claim, fencing token,
  isolated review/apply publishers, and commit receipt are reused;
- adapter exceptions, malformed terminals, or post-review apply-publication
  failures produce a failed review receipt and release the active claim.

### Subject adapters

- `MathNightlySolAdapter.execute(...)` validates the real Math closeout shape,
  freeze identity, closed Capture set, hashes and process/transaction evidence;
- `CS408NightlySolAdapter.execute(...)` validates exact per-Capture terminal
  outcomes, formal IDs, receipt/verification bindings, aggregate close hashes,
  write count and process/transaction evidence; the retired public writer stays
  retired;
- `EnglishNightlySolAdapter.execute(...)` validates the actual
  `english_apply_receipt_v1` shape, authorization, action counts, formal
  pre/post hashes, recovery binding and process/transaction evidence;
- permissive legacy wrap results cannot bypass the strict terminal schemas.

### Hard-conflict continuation

The synthetic A-safe/B-conflict/C-safe scenario proves:

- A and C commit through SubjectSol before the question;
- the global writer is released and the original queue is `safe_paused`;
- the continuation and resolution bind conflict ID, batch, subject, B, user
  option and current Skill identity;
- the adapter call sequence is full set, B-only retry, B-only success;
- failed B retry restores the continuation anchor;
- A/C bytes and receipts remain unchanged;
- final B success moves the original queue to `committed` with cumulative write
  count exactly once.

## Skill source identity

| Subject | Skill | Canonical source SHA-256 |
|---|---|---|
| Math | `kaoyan-math-nightly-qa` | `28404c5c470dfdb1761cb81cad7b6065dbaacf62fe0f88a2c22ae588e8aa144f` |
| CS408 | `kaoyan-408-daily-intake-curation` | `825e36206351a5e256c0a7d1d3dc1e350e5acbb5a6a73bbc228587533483dd8b` |
| English | `kaoyan-english-daily-intake-curation` | `5afceb83fa92869506139c0153b7d0069e4584abaefe047a12d72b169a4ba12b` |

## Verification

### Full suites

| Scope | Result |
|---|---|
| Backend Core | 1187/1187 PASS in 1230.477s |
| Backend Dashboard | 122/122 PASS |
| Shared MCP | 78/78 PASS in a fresh hash-locked Python 3.13 environment |
| Math | 75/75 PASS |
| CS408 | 115/115 PASS |
| English pipeline | 61/61 PASS |

No skip or xfail was used.

### Hosted synthetic Analysis Packages

The isolated hosted trial completed 9 model stages in the exact order
`gpt-5.6-terra -> gpt-5.6-luna -> gpt-5.6-terra`:

| Subject | Package ID | Package SHA-256 |
|---|---|---|
| Math | `ANPKG-43CB6AB45266970BACFFEBC1` | `a080b582e9f89a38eb51100d1a3eb85c0227e555ca8c1305fed24e2a9ac265dc` |
| CS408 | `ANPKG-4BEE646525F46ED03B6FFC56` | `4b90a54603fa1bfb3b518c436a050277c1f4e3d480cc59de1beaa988cdaf3019` |
| English | `ANPKG-425204B8D724F80B7D131A80` | `0a620aa22b345ee299cf3457c5601b81b85bc1151ea01a205946dea6433983dd` |

All three ended `ready_for_nightly`. Trial totals: 9 hosted model calls,
0 real Captures, 0 production formal writes, and production surface untouched.
The trial result SHA-256 is
`02df7d419269eaa6244bf9d8e0a2121cb4ad975c92564e904cf9d93eb8541ac3`.

### SubjectSol synthetic execution

- nine Backend alignment tests pass, including the six exact command routes;
- the real Math, CS408 and English adapter classes each feed the existing
  SubjectSol authorization/claim/review/apply/commit control plane;
- duplicate command replay returns `ALREADY_COMMITTED` without a second native
  write;
- adapter failure and post-review apply-publication failure both release the
  writer claim;
- A/C+B continuation and B-only resume pass with exact write-count checks.

## Candidate Build

- Shared MCP release:
  `4fbb3ee6abb6795f7711d970944542865f1ac6ce283ef378db7ee295ad526a37`
- Shared MCP manifest SHA-256:
  `433d9a9f514715288d2d4af22fed1bf0e1aef280a5bf9059a1818b3349e98e2e`
- Backend candidate release:
  `0e482f438ba37230276508e2d2059992cf6f057fcfad2a3ccb2559a4aca1c98e`
- Backend release gate: Core 143/143, Dashboard 122/122, no skips.
- Immutable verify: `verified`.
- Deployment preview: `planned`, `launchagent_changed=false`, no `--apply`.

## Production unchanged proof

- Active release remains
  `ad1807186ac277c6bacfb7fd8d83b9cbc86cf75027698e70f994f3228d815cc9`.
- Active config SHA-256 remains
  `f385409cd2b0b8ff512db549cae34f1ea3d3f9bd715f3e83d9a31f489ef08c40`.
- Active release manifest SHA-256 remains
  `b6e1eb335fd44a198eb7d8ad1bf04ba5c9bed93fe7bc43eb43847b321c10fcff`.
- Formal manifest remains
  `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352`
  with no differences and `formal_write_count=0`.
- The four installed LaunchAgent hashes remain unchanged:
  - Dashboard: `7d0664803a55580739ba431a13ce0c59078a43bfb2bce91a8b1d1f07520a559a`
  - Math: `4fe3d7c8113185f65efc8f9711fd5a1ac793ea29418a3eac010d0da3fcc2a760`
  - CS408: `ff440648a68ebf2f1e5815784f9ca7320a218e7362699a09aa7ce5acf0ac4529`
  - English: `4297635144ee2dc3a19bfd5ccb3430b224b0e4c4886a781b7f881a1fd242ebf6`

## Not executed

- real Capture
- real subject formal intake
- merge
- deployment or `current` switch
- LaunchAgent mutation
- Commissioning
- cleanup of production runtime or ledgers
- force-push

## GitHub review focus

1. Confirm v1 schema bytes are unchanged and the new report/batch capability is
   isolated in v2 schemas.
2. Review the raw-output-first normalization boundary and its strict canonical
   report/package hashing.
3. Review `admit_nightly_analysis_batch`, the existing SubjectSol receipt chain,
   and failed-publication claim compensation.
4. Review each subject adapter's strict native terminal validation without
   importing subject semantics into Backend.
5. Review the continuation/resolution binding and B-only retry path.

`READY_FOR_GITHUB_REVIEW`
