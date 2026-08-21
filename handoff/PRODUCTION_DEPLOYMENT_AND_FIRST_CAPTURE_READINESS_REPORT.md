# Study Intake V2 Production Deployment and First Capture Readiness Report

- Report date: 2026-08-22 Asia/Shanghai
- Deployment terminal status: `READY_FOR_FIRST_REAL_CAPTURE`
- Real Capture created in this run: 0
- Real subject formal intake executed in this run: 0
- Production formal write delta: 0
- Production deployment applied: yes
- Current release after deployment:
  `ab48738b3592ede2764cd87f9b6cfe4ebf862674d4b3cce6a5513f793d4fbf1c`

## Executive result

Study Intake V2 is merged, freshly tested, built from the merged default
branches, immutably verified, deployed through the canonical canary transaction,
and commissioned without creating a real Capture or running a real nightly
formal intake.

The active production configuration is `live_authorized`. A task still requires
its normal Capture-scoped manual authorization; no global authorization was
fabricated. The enabled production path routes a durable Capture through Terra
Max analysis, Luna Max analysis, Terra Max final review, tolerant normalization,
and `AnalysisPackageV1`. Subject formal curation remains disabled until the
user later sends the existing exact dated subject command.

All three subject canaries are armed, idle, post-activation-only, and waiting
for the first real Capture. The global formal writer is idle, active and claimed
lease counts are zero, and no continuation is paused.

## Repository heads and merge evidence

Feature branch in all five repositories:
`feat/study-intake-v2-capture-ready-v1`.

| Repository | Prompt starting feature HEAD | Final feature HEAD | Deployed or accepted default-branch code HEAD | Merge |
|---|---|---|---|---|
| Backend | `a4c5e9a7516b853eea50f456218bab38211f30e1` | `521d12d007c8b65c50f0fb8748e70eb1b707ab73` | `521d12d007c8b65c50f0fb8748e70eb1b707ab73` | direct fast-forward to `main` |
| Shared MCP | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | unchanged | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | direct fast-forward to `main` |
| Math | `8dfe49dcb6e0784a716ac87248039212737ed63b` | unchanged | `8dfe49dcb6e0784a716ac87248039212737ed63b` | direct fast-forward to `main` |
| CS408 | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` | unchanged | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` | direct fast-forward to `main` |
| English | `2121171cc59194e41f5a4863723295593c3d0c58` | unchanged | `2121171cc59194e41f5a4863723295593c3d0c58` | direct fast-forward to `main` |

The five private repositories had no existing PR for this feature. GitHub
reported that branch protection is unavailable for these private repositories
on the current plan, so each merge used `git merge --ff-only`, followed by
`git push`, `fetch --prune`, tracking-ref equality, and live `ls-remote`
verification. No squash, rebase, force-push, or history rewrite was used.

This report advances Backend `main` by one report-only commit. Git cannot embed
a commit's own hash in its tracked content; therefore the exact final remote
report commit is the commit containing this file and is recorded by the final
post-push operator verification. The deployed code head remains the exact
`521d12d...` commit above.

## Final test evidence

All final suites used the documented full discovery roots and reported no
skip, expected failure, or xfail.

| Scope | Final result |
|---|---|
| Backend Core | 1192/1192 PASS in 824.525 seconds |
| Backend Dashboard | 122/122 PASS in 7.917 seconds |
| Shared MCP | 78/78 PASS in a fresh hash-locked Python 3.13 environment |
| Math | 75/75 PASS |
| CS408 | 115/115 PASS |
| English pipeline | 61/61 PASS |
| Analysis Package, nightly Sol, exact-command and schema targeted set | 75/75 PASS |
| Final SubjectSol and hosted-scope diagnostics | 10/10 PASS |
| `git diff --check` | PASS in all five repositories |

The two legacy v1 files remained byte-frozen against their first committed
feature blobs:

- `schemas/analysis-stage-report-v1.json`:
  Git blob `9a00b02b249faeab293740efe10f999e10003eaa`
- `schemas/nightly-sol-batch-v1.json`:
  Git blob `b87b13d7dabd36ee43b89ed904dc08f349b732c2`

New tolerant model-output and nightly batch behavior remains isolated in v2
schemas.

## Hosted synthetic merged-head acceptance

Final evidence artifact SHA-256:
`3ccd43d3578004672f1921fff4529ff6d3a2298ca431cb02e4672e07432ca946`.

The isolated harness executed nine hosted model stages. It used only temporary
synthetic subject repositories and never opened production subject roots,
production Capture ledgers, `current`, LaunchAgents, or formal data.

| Subject | Analysis Package | Package SHA-256 | Stage order | Result |
|---|---|---|---|---|
| Math | `ANPKG-4C498A747AD0CEA3025305A8` | `855fe2284011b237d369fc9e762d0d7fc359fbd6b069ead74d411f19cdb1bf0c` | Terra, Luna, Terra final | `ready_for_nightly` |
| CS408 | `ANPKG-69518837FB7C686EDA9ACDDA` | `d66d6a494e9ad0f2849426a8b72da6439823de61ef438b8a132e7014323e8087` | Terra, Luna, Terra final | `ready_for_nightly` |
| English | `ANPKG-E28C5DC9C62A77826EEA459A` | `207c0afdf3956073d3357df6f9475ef3c555574a8cd3da1275dd4977f9804a14` | Terra, Luna, Terra final | `ready_for_nightly` |

Totals: hosted model stages 9, real Capture count 0, production formal write
count 0, and production surface touched false.

One earlier synthetic attempt correctly failed closed when a model made an MCP
tool call outside the allowed scope. The code was not weakened. The diagnostic
was made explicit, and later complete final-head trials passed under the same
strict scope.

## Immutable Build and source identity

### Shared MCP

- Source HEAD: `37eab4e6638fabd6be15e31f0f278c8b6651dcf9`
- Source tree: `f7921bc499e01a4f4a62e999d760f272c2a5d596`
- Release ID:
  `4fbb3ee6abb6795f7711d970944542865f1ac6ce283ef378db7ee295ad526a37`
- Manifest SHA-256:
  `433d9a9f514715288d2d4af22fed1bf0e1aef280a5bf9059a1818b3349e98e2e`
- Strict verify: `verified`

### Backend

- Source HEAD: `521d12d007c8b65c50f0fb8748e70eb1b707ab73`
- Source tree: `130637739227f4bf9c4eb651784d0fefae91cee4`
- Release ID:
  `ab48738b3592ede2764cd87f9b6cfe4ebf862674d4b3cce6a5513f793d4fbf1c`
- Release manifest SHA-256:
  `63dded8baa5ba74632625a44dfdd59c72e8b93c3ccaabf26fe5ef9b2f55fda46`
- Config SHA-256:
  `6b929249c8c23b91630481d99b20dc70eb3a7f27fd4d1cb11f0d45ecb83464c6`
- Build gate: Core 143/143, Dashboard 122/122, no skips
- Formal surface gate: passed and unchanged
- Strict immutable verify: `verified`

The Backend component lock binds the exact Shared MCP release and manifest.
The generated production config binds the stable MCP Python runtime, the three
production subject roots, `execution_mode=live_authorized`,
`analysis_package_v1.enabled=true`, and the enabled three-stage consumer chain.

The release Skill hashes equal the matching default-branch subject source
hashes:

- Math nightly QA:
  `28404c5c470dfdb1761cb81cad7b6065dbaacf62fe0f88a2c22ae588e8aa144f`
- CS408 daily intake curation:
  `825e36206351a5e256c0a7d1d3dc1e350e5acbb5a6a73bbc228587533483dd8b`
- English daily intake curation:
  `5afceb83fa92869506139c0153b7d0069e4584abaefe047a12d72b169a4ba12b`

## Preview, drain, activation and recovery receipts

- Final canary manifest SHA-256:
  `7434711486bcb1fd3e3a5c4a40cf9b06766e376bcbc00d5728924c8cc66deb65`
- Final preview status: `production_canary_planned`
- Final preview surface SHA-256:
  `68551dba791f58026c6c11c4d0718370ae646a021700b1384e53fedc0bfa03de`
- Preview non-mutation verified: true
- Final drain receipt SHA-256:
  `b41561db59ed7dab40a4063f57ec6d7cc99b3c12b1412d508624b58e36728fae`
- Activation prepare receipt SHA-256:
  `6d75cba065abb3d0984e2eee174172a6fc324fec8d1016f42ea6cbac048f42ae`
- Activation postcommit receipt SHA-256:
  `2317a8737e6cbf9fab9e3aa2ac782566650fc3ab34407beaaef76bb7a6feb243`
- Canary activation receipt SHA-256:
  `f37f63713138c7bde9ea672bae303647f050e048de9758d0bd4e3cedd42e8dcb`
- External profile apply proof SHA-256:
  `dfd8b99cffca1cd406cac485cb9bb2d7fba7684ae5d313015811dc69db532634`
- Activation status: `production_canary_active`
- Activation transaction model or Provider calls: 0
- Activation formal writes: 0

### Failed-attempt recovery

The first apply stopped before any production mutation because the installed
legacy LaunchAgent files were exact historical JSON ProgramArguments arrays,
not plist dictionaries. Old production remained running. The repository-owned
no-mutation recovery marker was:
`be7cec7bb9ad8c6aa6874181e25379e0198f1f7c55e069f93dc7d92191db30f1`.

The second apply entered service transition but the same legacy files could not
be booted out or bootstrapped by path. `current`, old PIDs, installed bytes,
external profiles, formal data, and Dashboard remained or were restored to the
old production preimage. Its exact no-change recovery marker was:
`ab384f01120fc322de020c7179571a930683a752c9287d621ac5b9e3a10bddf0`.

The final fix kept the old installed bytes strictly allowlisted, used the
launchd service target to stop the already-loaded old jobs, and used valid
plist bytes rendered from the verified old immutable release only for rollback
bootstrap. No nonzero launchctl result was relabeled as success. The third
apply completed successfully.

## Current before and after

- Before:
  `ad1807186ac277c6bacfb7fd8d83b9cbc86cf75027698e70f994f3228d815cc9`
- After:
  `ab48738b3592ede2764cd87f9b6cfe4ebf862674d4b3cce6a5513f793d4fbf1c`

The final installed LaunchAgent hashes are:

| Service | Plist SHA-256 | Commissioning state |
|---|---|---|
| Dashboard | `cca90057e030f402259aabe1255f405947da9259c431f7c572b0958ba2458ec0` | running |
| Math | `a08bd365d9316120d50884aaebcd009c0d7a9eb6f325e1e14c7537cb26d8254b` | running |
| CS408 | `6779b43e95d00a14b8661ab6f16faa39e2adae9f97865a5b69eab7604846cbd0` | running |
| English | `e08bd4512e3715d18646423f4e04483a24fac114da6643dfac12de979ed49506` | running |

## Commissioning

- `current` resolves to the verified Backend release.
- Exactly three Dispatcher processes and one Dashboard process are running.
- Dashboard owns `127.0.0.1:8767` and `/healthz` reports ready.
- All three heartbeats are fresh and bind the active release.
- No duplicate daemon or crash loop was observed.
- No Provider child process is running.
- The three MCP servers are distinct, subject-scoped, and expose only:
  `get_task_context`, `read_task_artifact`, `list_records`, `get_records`,
  `search_records`, and `query_relations`.
- No MCP write, apply, delete, arbitrary-path, shell, or cross-subject tool is
  enabled.
- The live route test proves an enabled `live_authorized` config enters
  `AnalysisPackageDriver` rather than the legacy fail-closed branch.
- The exact nightly command parser was exercised without dispatching a command.
- Global SubjectSol writer state is absent and idle.
- Active or claimed lease count is 0.
- Continuation file count is 0; `safe_paused` count is 0.
- All three canary gates are HMAC-authorized, armed, and have valid fencing and
  activation identity.
- Math has no historical eligible items. CS408 has 7 and English has 2; all 9
  are explicitly `pre_activation_frozen`. Pending and claimed queue counts are
  zero.
- Current canary model, Provider, MCP, terminal, and formal-write counters are
  zero.
- Durable Capture v1 count is 0.
- Analysis Package v1 count is 0.
- Real nightly formal intake count is 0.

## Formal-surface proof

- Before manifest SHA-256:
  `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352`
- After manifest SHA-256:
  `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352`
- Differences: none
- Formal write delta: 0

## Rollback readiness

The old release is still immutably reopenable under
`historical_three_role_target_runtime_v3`. A canonical rollback preview from
the new current to `ad180718...` returned `planned`, required a fresh drain
receipt for apply, made no mutation, and retained the new release as the
roll-forward target.

## First real Capture procedure

The user may now resume the existing foreground subject workflow and submit one
normal real learning item:

1. Math or ordinary CS408: while the actual current question is present, use the
   existing exact foreground phrase `快速入库`.
2. English: complete one normal full intensive-reading dialogue turn; the
   existing foreground English Capture rule applies.
3. CS408 morning review: use the existing morning flow; its already-defined
   first-final-correct Capture exception remains unchanged.
4. Observe the new item on Dashboard. Do not manually create a runtime file or
   fabricate a Capture.
5. Do not start a real nightly formal intake unless the user later sends the
   existing exact dated subject command.

`READY_FOR_FIRST_REAL_CAPTURE`
