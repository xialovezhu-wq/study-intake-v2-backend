# Phase 2 Code Review Closure and Build Readiness Handoff

- Requested handoff date: 2026-08-22, Asia/Shanghai
- Recovery authorization resumed: 2026-08-23, Asia/Shanghai
- Status: `CODE_REVIEW_PASS`
- Static readiness: `PRECAPTURE_STATIC_PASS`
- Producer authority: `PRODUCER_AUTHORITY_DECIDED`
- Protected production surfaces unchanged: `true`
- Production Capture created or consumed by this session: `0`
- Real production Provider or MCP requests: `0`
- Formal writes by this session: `0`

## Exact source identity

| Item | Value |
|---|---|
| Feature branch | `codex/local-backend-validation-reduction-phase1-20260822` |
| Recovery starting HEAD | `ef479c7a2792e63cfd0a0e01e603f6fcd583af85` |
| Tested code HEAD after isolated-acceptance repairs | `d69d1f0c1503606888d77ee2150a4b2c1d896f86` |
| Implementation commit | `791fe66` |
| Portable regression commit | `ea90baf` |
| English isolated-acceptance repair | `876552a` |
| Inline artifact canonical LF repair | `e341cb8` |
| Three-stage task supervisor event repair | `d69d1f0` |
| Shared MCP `main` | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` |
| Math canonical `main` | `8dfe49dcb6e0784a716ac87248039212737ed63b` |
| CS408 canonical `main` | `09e811e97f7e99ece7cab1751aca86ec92a7c41e` |
| English canonical `main` | `2121171cc59194e41f5a4863723295593c3d0c58` |

The handoff commit is necessarily a successor of the exact tested code HEAD
because a commit cannot include its own hash. The final branch HEAD is verified
after the documentation commit and push.

## Accepted production baseline

The user explicitly accepted both the 20 pre-deployment Math Capture events and
the five Math closeouts that already existed before this recovery session. This
acceptance is a baseline classification only; it is not Backend production
Capture acceptance.

| Surface | Accepted value |
|---|---|
| Active Backend release | `da9b8831df0d78567bbd3edf2767ccef4f00c3e90a35a4942a016659df9a0efa` |
| Release manifest SHA-256 | `674657939785793ac2949d1b806e7d5bb28df576f5ddced2ab5e698481799447` |
| Active release config SHA-256 | `37d3860fc65c24e38fb60ab178d5846d5cc6ecef30d2bd32fe546880c61fb2a6` |
| Runtime config SHA-256 | `ef13a722dfc52281d1ff55fffc43aba12768866e4f8064c0789c820c4080af36` |
| Component lock SHA-256 | `098247f44fed39a153f04574b47a0f7105286d54e815ae7a65115b2e80d669d1` |
| Installed LaunchAgents group SHA-256 | `5c0f20f1f6f26b9103534c253c2a2bd867c63250f616872c22c3b18ab5f7fb6f` |
| Producer source group SHA-256 | `55abfe960fc522f2f7b620eb443ea14ec8834fbe4c4e044342b9c3c2e31a88c2` |
| Producer protected trees SHA-256 | `8191f2e22ec5141f952572198ba25e985d5cf84dfd27d8187da06404d3a33aac` |
| Math Capture ID set SHA-256 | `799975826f53ce9609af0e4384da479e0c2f8815769423485b8925717c128588` |
| CS408 Capture ID set SHA-256 | `6b18c6737ffe42cdadae159fefa286d996d9b32d438c33718a8b7e722d60e535` |
| English Capture ID set SHA-256 | `89d10767cfd4ed237fddaf1607f770fa6b6520169d53a5cc75f64d003c153402` |
| Backend task ID set SHA-256 | `cae4b4ec0b5e373fdf50df76fa2b70ed0a27820394dccff2376f1b4345291456` |
| Backend terminal ID set SHA-256 | `55a11417301f784d5d8107a845d2358c2c763c922c6f337eb66db90c80b48c48` |
| Backend package ID set SHA-256 | `2eacdfa9cde0f040d7a684e3d138bfaeffa0c23bfc37e250d10fe1ff8b086c8` |
| Backend report ID set SHA-256 | `dd734428023b5ce9825fe2020d6fb9c9fa3e6b24b3a25e41797e0b13acbf4f7a` |
| Signed control SHA-256 | `bd326e9f764b57f61fcd09dacfd6fa424c3c99cb0df393df2491a3a0fce87a20` |
| Formal surface manifest | `165e19ee3629e2bb8ff2d915eee68f50efe56a71a0a800cc8e0d9b853a734d63` |
| Frozen formal baseline artifact SHA-256 | `961bd7a791490002ab54026314baa5c5aef51bb5282b84161ab203687bb64559` |

Snapshot A ended at `2026-08-22T22:54:39Z`. Snapshot B started at
`2026-08-22T23:00:44Z`, satisfying the five-minute silence requirement, and
ended at `2026-08-22T23:01:23Z`. The post-regression Snapshot C ended at
`2026-08-23T00:28:46Z`. A/B and B/C normalized comparisons had zero changes.

## Authorized pre-deployment Math Capture metadata

The source ledger has no field literally named `source_event_hash`. The exact
event hash recorded below is its immutable `content_hash`. No question body,
image, solution, or dialogue is included.

| Capture ID | recorded_at | content_hash |
|---|---|---|
| `MFI-CAP-b7bb5b4cb3ad809ea9cb2a4a` | `2026-08-22T22:01:08+08:00` | `f4be60f151996e21ea2ab20b46373ce02433a678abffe6032abc2aa47c759f33` |
| `MFI-CAP-64d4519e8f2897d79fb45e9c` | `2026-08-22T22:01:08+08:00` | `d9294a5c9f11da8e665a28765e22eddab5c25877c000695ecfbea7ad5f34a19a` |
| `MFI-CAP-e2efaa75b13f952b24ebdd15` | `2026-08-22T22:01:08+08:00` | `dd14b068e110c047c215d9a1ba9257fb0ce9f7509549d044d57e243a6a73fd69` |
| `MFI-CAP-e28735343174e9bd6c11ce62` | `2026-08-22T22:01:09+08:00` | `cbe0dcb9785438253c37765ebf2ae5a0bff7bcb4ea60ee0c7969b906c1b0b91f` |
| `MFI-CAP-62b08b140cc3b7d0fc909c88` | `2026-08-22T22:01:09+08:00` | `67d038a8164c763375b79df61f45a6de8870983ebc5780c4dd52c30aa9988fc3` |
| `MFI-CAP-16ba3b7bc78b30e4abfc2da2` | `2026-08-22T22:01:09+08:00` | `2123c270ddaee11bd46d8a6093e50ae624a0d2371ea3a1c93a036c6efc6c9b2b` |
| `MFI-CAP-02cc067a1aa29f2b180f8447` | `2026-08-22T22:01:09+08:00` | `8c49e5255741bc2aea5a5b1186fea7035420da039da98aa9cec2ea0e9ff8fe07` |
| `MFI-CAP-e2ba8778425b256af48069cd` | `2026-08-22T22:01:09+08:00` | `49d1668e7d909c1f36fe5625ac28759d250a5601d0d494e08518024655b0e7d2` |
| `MFI-CAP-5591d5bcde4db4d726ec0f16` | `2026-08-22T22:01:09+08:00` | `b377c41dceb348bf2757641fe2e36c731c8f8684e1946e0521e4fe4ae9c11d47` |
| `MFI-CAP-8e05b4423efd74e789a17afc` | `2026-08-22T22:01:09+08:00` | `7f158d407df60acf587b4d03292cc073c067be0bf005565f4c57692e83b65afa` |
| `MFI-CAP-3f0ef46b43848461932479bd` | `2026-08-22T22:01:09+08:00` | `addc6564b02ecb8ae0b1d61d7d1e084af6c8cb76eea47e69c76da2c79d6a86d7` |
| `MFI-CAP-66f39cbfd3fd8288522329a8` | `2026-08-22T22:01:09+08:00` | `64457a011574640ad17313e0b3ab414534ce29b8d18c40a171a35717693376f4` |
| `MFI-CAP-e2daffb7c81f524dae6515d8` | `2026-08-22T22:01:09+08:00` | `374e0f5e8862e259806180b0cb82ab996fa2a1a39ec0dced73f10581ed7b82b3` |
| `MFI-CAP-700f640ea1adc81343a3da02` | `2026-08-22T22:01:09+08:00` | `d51ec1f0c1e6e4458ea6033acaf9f875744f772a5ffe9403bd9a560e6cc83585` |
| `MFI-CAP-73c32f924be816d052f7fba5` | `2026-08-22T22:01:10+08:00` | `9d05a5d104f92908e4e73fa161b56530f27cacfb6abbfcc8a335b6fe3461519a` |
| `MFI-CAP-4610aff5d1453dcc8585772b` | `2026-08-22T22:01:10+08:00` | `691ad6f278d4d200a983485a7cc0c8093dd230bb2e12965e042778c1dd6603f8` |
| `MFI-CAP-eb7c5bc790943bb3b8820f09` | `2026-08-22T22:01:10+08:00` | `21cf583385e33777be3e70ef84015a41613717952d06cd8389c793e0687bd73a` |
| `MFI-CAP-1fabe1e1eb83cc794c2ba6cd` | `2026-08-22T22:01:10+08:00` | `09a95a7a10af34b9801e9d263888b637b22e3d190c8f5a96330626578091ca2b` |
| `MFI-CAP-01945a891c2e74e634dd3a1b` | `2026-08-22T22:01:10+08:00` | `f92309411083d5a32eb3518c0c07f19a6637f6a09dbe3169c94c2cda766c4a91` |
| `MFI-CAP-935553342b7ea48964bc60ec` | `2026-08-22T22:01:10+08:00` | `1feaaea99f7fa644edcb8893a884c49b87954787e1953b4a44b8e4fc135ebbb6` |

## Authorized Math closeout metadata

| Closeout | closed_at | Formal result |
|---|---|---|
| `MFI-CLOSE-8a8ca59884481eda55e525ab` | `2026-08-22T22:11:42+08:00` | created `GS-744`, `GS-745` |
| `MFI-CLOSE-78e57fc0d85e242f9f4b5fb6` | `2026-08-22T22:14:30+08:00` | created `GS-746` through `GS-750` |
| `MFI-CLOSE-af3ff140074205df014ac1bf` | `2026-08-22T22:20:35+08:00` | updated `GS-694`; created `GS-751` through `GS-755` |
| `MFI-CLOSE-54f9e2c5b35040e9ab299c92` | `2026-08-22T22:22:43+08:00` | created `GS-756` through `GS-760` |
| `MFI-CLOSE-4765955577582021e5ad3dfa` | `2026-08-22T22:33:12+08:00` | updated `GS-732`; created `GS-761` |

The accepted formal delta was Math-only: 36 files added, 19 files changed,
and zero files removed. CS408 and English formal subject hashes were unchanged.

## Findings closure matrix

| Finding | Classification | Closure |
|---|---|---|
| B1 ordinary daemon versus signed control | `CONFIRMED_AND_FIXED` | Ordinary scan and claim obey existing signed canary/drain control without entering the legacy canary queue or clearing drain. Legacy canary daemon behavior remains separate. |
| B2 AnalysisPackage terminal authority | `CONFIRMED_AND_FIXED` | Terminal publication preserves Terra analysis, Luna analysis, Terra final, requested/runtime identity, Provider/MCP counts, raw/report refs, normalization status, process closure, and AnalysisPackage authority. |
| H1 Provider pre-stdin lease race | `CONFIRMED_AND_FIXED` | The lease, task detail, context root, executable, argv, and provider process identity are reopened under the current fence immediately before stdin bytes are sent. |
| H2 Math invalid/equal cutoff classification | `CONFIRMED_AND_FIXED` | Equal timestamps are historical cutoff; invalid timestamps remain visible as `needs_review`; exact allowlist filtering occurs before unrelated candidate errors. |
| H3 malformed Dashboard count closure | `CONFIRMED_AND_FIXED` | Malformed items are normalized to diagnostic placeholders before subject counts are recomputed. |
| H4 actual Producer bytes ordinary runtime | `CONFIRMED_AND_FIXED` | Math, managed CS408, and English actual production descriptor/source closures each generated a synthetic Capture and completed exactly one zero-model ordinary claim in temporary roots. |
| H5 source-only identity/high-water | `CONFIRMED_AND_FIXED` | Source-only exact run-once binds loaded core identity, existing producer high-water, and one exact Capture allowlist without canary queue materialization. |
| M1 Dashboard v4/v5 axis separation | `CONFIRMED_AND_FIXED` | v4 rejects v5-only review/report axes; v5 accepts and validates them. |
| M2 required Producer field typo | `CONFIRMED_AND_FIXED` | Missing or misspelled required canary selection fields fail closed. |
| L2 direct candidate release | `CONFIRMED_AND_FIXED` | `_direct_controlled_candidates` is released in a terminal `finally` path. |

## Test evidence

All final suites used repository-owned synthetic builders or canonical source
archives. No user question body, image, solution, or dialogue was copied into
Git or the handoff.

| Suite | Passed | Failed | Skipped | Duration |
|---|---:|---:|---:|---:|
| Backend Core full discovery | 1216 | 0 | 0 | 815.263 s |
| Dashboard full discovery | 129 | 0 | 0 | 4.526 s |
| Frontend view-model | 18 assertions | 0 | 0 | 0.014 s |
| Math Producer full | 75 | 0 | 0 | 0.784 s |
| CS408 Producer full from clean `main` archive | 115 | 0 | 0 | 1.268 s |
| English Producer full | 61 | 0 | 0 | 1.638 s |
| Shared MCP full | 78 | 0 | 0 | 8.738 s |
| Three-stage task-runner event regression | 8 | 0 | 0 | 0.077 s |
| Inline artifact affected set | 127 | 0 | 0 | 33.748 s |
| Final affected combined | 144 | 0 | 0 | 74.108 s |
| Final ordinary/canary combined | 59 | 0 | 0 | 8.872 s |
| Actual production-byte zero-model | 3 | 0 | 0 | 1.931 s |
| Source-only exact run-once | 1 | 0 | 0 | 0.052 s |

Shared MCP used its existing 3.13 dependency runtime with the editable source
pointer temporarily rebound to the canonical checkout. The original pointer
was restored byte-for-byte at SHA-256
`9ba02dbf9194b6c7948286f7823a0f77f78830b7f5550195b689021a430b5cb5`.

The full Core environment binds
`STUDY_READ_MCP_TEST_PYTHON` to that same portable 3.13 runtime. Test-only MCP
venv builders now honor this documented binding instead of attempting a 3.14
wheel against a 3.13-generated hash lock. Legacy v1 schemas remain byte-frozen.

## Producer Authority Decision

The Build must bind the current production descriptor closure, not canonical
`main` and not a synchronized successor:

| Subject | Descriptor SHA-256 | Production source closure SHA-256 |
|---|---|---|
| Math | `e77e4e070e384f2379c232560e4e21c9eedf536209551db1321990f02a609df1` | `efdbd8330fea934bd7cb37c54e84ddba3284059fab03c7bb27e48334a4c12712` |
| CS408 | `2df5dcf0e627b2f80ece50761f32518d3c8c0f05ac58dbe5c185fedc3afbf3ea` | `a9d8669faa13d6ef43df6eccd9e6aa584e1aa3b79807696397154122f7513b08` |
| English | `2276c37251543e8cf044b4cdd6c2feba7b47ea86fb531fe9333e2daaaef37b87` | `22e4074021c919bd5f1d688fb43469bda7f2e509f08e45be01623735a63d8392` |

Deferred Producer sync remains separate and non-blocking. Compared with each
canonical `main`, production differs in Math 2/2 declared source files, CS408
3/5, and English 3/4. No Producer repository was modified in this closure.

## Original Math failure

`MFI-CAP-7b84f5dee8bc2846f5431c58` remains an existing historical Backend
failure with `manual_live_authorization_missing` / `failed_drained`. It had
zero model, Provider, MCP, and formal writes. No replay or recovery was
attempted in this phase.

## Pre-build code-review zero-call and no-mutation proof

```text
real_terra_call_count=0
real_luna_call_count=0
real_provider_model_request_count=0
production_mcp_task_count=0
production_capture_created_count=0
production_capture_consumed_count=0
formal_write_count=0
PROTECTED_PRODUCTION_SURFACES_UNCHANGED=true
```

After immutable candidates were built, the isolated synthetic acceptance lane
completed two non-production Terra model calls. Across those two calls it
observed 12 MCP attempts: 10 succeeded and two returned business-level
`NOT_FOUND` against an initially incomplete synthetic catalog. It completed no
Luna or Terra-final call. Every artifact remained below an ephemeral isolated
runtime, formal writes stayed zero, and production Provider, MCP, Capture, task,
package, report, and formal surfaces remained unchanged.

## Build readiness

The first immutable candidate
`3127bf2c91bf953400cfe7b9700cb35dbb5954f6cde0373696183ec731af5bb7`
was strictly verified but rejected during the first English isolated synthetic
Capture. The actual Producer reached the candidate dispatcher and failed before
Provider or MCP invocation because the English inline article artifact used
`source_text` while the ProcessingPlugin contract accepts `article_text`.
Candidate `3127bf2c...` was never activated. The minimal source correction and
repeatable H4 temporary-runtime isolation are committed at `876552a`; Backend
Core 1215/1215 and every final suite passed again.

The successor candidate
`a64649f67e697995742c6179a83206181d5ecc463cc5b8684f783de6725937fd`
passed strict verify and advanced farther through the same isolated English
Capture, but was also rejected before Provider invocation: inline article
content was hashed without the canonical trailing LF required by the
ProcessingPlugin freezer. The same defect existed in the fallback inline Math
solution-text artifact. Both digests now use canonical JSON plus LF and have
exact English and Math regressions at `e341cb8`. Candidate `a64649f6...` was
never activated. Backend Core 1215/1215, the 127-test affected set, and all six
final suites passed on the corrected source. A third immutable candidate is
therefore required; neither rejected candidate may be deployed.

The third candidate
`adb573627936f1eb23f3f56fd2463d170a51962e0be6dc3b141c4118db7ab3cf`
also passed strict immutable verification and was never activated. After the
acceptance harness was aligned with the content-addressed runtime, task
supervisor, and complete synthetic English catalog, its English Terra-analysis
stage completed with 5/5 MCP calls, a zero Provider return code, sealed task and
Provider process closure, and zero formal writes. The child then failed before
starting Luna analysis because `_StageEventRecorder` did not recognize the new
`english_luna_analysis` stage introduced by the three-stage AnalysisPackage
path. The same missing mapping affected Math and CS408. Commit `d69d1f0` maps
each subject's Luna stage to the authenticated `model_submitted` task event and
adds a three-subject regression. Backend Core now passes 1216/1216. Candidate
`adb57362...` is rejected; a fourth immutable candidate is required.

The feature worktree is ready to be pushed and built as a fourth immutable candidate.
Build must use the frozen authorized formal baseline, explicit production
Math/CS408/English roots, the current immutable Shared MCP release, and the
repository-owned `release_manager.py`. It must not switch `current`, change
LaunchAgents, process the authorized Math set, or create a production Capture.

`CODE_REVIEW_PASS`

`PRECAPTURE_STATIC_PASS`
