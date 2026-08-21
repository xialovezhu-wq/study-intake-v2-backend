# Study Intake V2 Nightly Sol Alignment Execution Report

- Report date: 2026-08-21 Asia/Shanghai
- Branch: `feat/study-intake-v2-capture-ready-v1`
- Terminal status: `READY_FOR_GITHUB_REVIEW`
- Real Capture executed: no
- Real formal intake executed: no
- Merge or deployment executed: no

## Result

Study Intake V2 now stops the daytime chain at a durable
`AnalysisPackageV1` after Terra analysis, Luna analysis, and Terra final
synthesis. Nightly selection is based only on subject plus the frozen
`capture_intake_date`; `study_date` and `captured_at` remain distinct.

The three subject adapters bind an exact outer Capture set and the canonical
Skill source hash, then wrap the native subject receipt without interpreting
subject fields. Only an ambiguous real target, immutable source/fact conflict,
or materially different formal choice can become `awaiting_user`. The global
writer lease is released before the question, and resume is limited to the
conflicted Capture without rerunning Terra or Luna.

## Repository heads

| Repository | Starting head | Final code head before this report |
|---|---|---|
| Backend | `12382bad6a93b6335d36e99ebe3dde0a316bd5a8` | `f0724fe763fbc077e64dab4d5c01ff52b8323765` |
| Shared MCP | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | unchanged |
| Math | `7a86f828fca0357fc438755e16505c6a81002636` | `412fd8c4600a459bfc0e9d946f51824069b3595c` |
| CS408 | `c63bc20b316a4b725e963154ddb7149ced000a20` | `fd8459f8a26ece7495a981694b71088a1c6039db` |
| English | `7aa357700e4d34d9d0bc8a5fef043a79c7d535ce` | `d137509b9d0a7b348b4fb29692e8e8955a20bf81` |

The Backend feature head after the report-only commit is the commit containing
this file. Post-push verification records the exact remote head.

## Skill source identity

| Subject | Skill | Canonical source SHA-256 |
|---|---|---|
| Math | `kaoyan-math-nightly-qa` | `28404c5c470dfdb1761cb81cad7b6065dbaacf62fe0f88a2c22ae588e8aa144f` |
| CS408 | `kaoyan-408-daily-intake-curation` | `825e36206351a5e256c0a7d1d3dc1e350e5acbb5a6a73bbc228587533483dd8b` |
| English | `kaoyan-english-daily-intake-curation` | `5afceb83fa92869506139c0153b7d0069e4584abaefe047a12d72b169a4ba12b` |

The candidate plugin contains those same Skill bytes. CS408 keeps
`scripts/intake_apply_408.py` retired; its real batch engine is reachable only
through the internal nightly adapter path.

## Verification

### Full suites

| Scope | Result |
|---|---|
| Backend Core | 1179/1179 PASS in 1056.724s |
| Backend Dashboard | 122/122 PASS |
| Shared MCP | 78/78 PASS in the hash-locked environment |
| Math | 70/70 PASS |
| CS408 | 110/110 PASS |
| English | 57/57 PASS |

No skip or xfail was used.

### Hosted synthetic Analysis Package trials

All three subjects used isolated temporary repositories and the candidate
subject-scoped Shared MCP release. Each completed the exact requested model
order `gpt-5.6-terra -> gpt-5.6-luna -> gpt-5.6-terra` and produced a
`ready_for_nightly` Analysis Package:

| Subject | Package ID | Package SHA-256 |
|---|---|---|
| Math | `ANPKG-9AD15D452D77A2071F2D02AC` | `61a8d621e1d076d63eb239effd53e3d9f58f93e7f797aa68ca81c83b5409a2d8` |
| CS408 | `ANPKG-A179C53C77D195F70AB1315B` | `7c1ceccfe2923f194a0af16aac430889e687932aec7b89dae1f12b76be26bf84` |
| English | `ANPKG-39BFAB97244D7461CBD4CF95` | `9298909cab4d40787331a25fae4e6e9a2e4692a0384c075ef9e872faee5700c4` |

Totals: 9 hosted model stages, 0 real Captures, 0 production formal writes.

### Isolated subject writes

- Math synthetic closeout and identical no-op paths passed in the full suite.
- CS408 internal batch writer committed one isolated synthetic item, then
  returned `ALREADY_COMMITTED` on identical replay with unchanged repository
  bytes and receipt hash. The public apply entrypoint remained retired.
- English synthetic dry-run, committed apply, receipt/recovery, CAS, duplicate,
  and idempotent paths passed in the full suite.

## Candidate Build

- Shared MCP release: `4fbb3ee6abb6795f7711d970944542865f1ac6ce283ef378db7ee295ad526a37`
- Shared MCP manifest SHA-256:
  `433d9a9f514715288d2d4af22fed1bf0e1aef280a5bf9059a1818b3349e98e2e`
- Backend candidate release:
  `53c1e764997deb4bed99232a5a6fd58a8f815a427be5524495c385cb19f59c7f`
- Backend release gate: Core 143/143, Dashboard 122/122, no skips.
- Immutable verify: `verified`.
- Deployment preview: `planned`, `launchagent_changed=false`, no `--apply`.

## Production unchanged proof

- Active release remained
  `ad1807186ac277c6bacfb7fd8d83b9cbc86cf75027698e70f994f3228d815cc9`.
- The only active `current` link remained unchanged.
- Active config SHA-256 remained
  `f385409cd2b0b8ff512db549cae34f1ea3d3f9bd715f3e83d9a31f489ef08c40`.
- Active release manifest SHA-256 remained
  `b6e1eb335fd44a198eb7d8ad1bf04ba5c9bed93fe7bc43eb43847b321c10fcff`.
- Formal manifest remained
  `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352`
  with no differences.
- All four installed LaunchAgent hashes were unchanged.

## Not executed

- merge
- deployment or `current` switch
- LaunchAgent mutation
- real Capture
- real nightly formal intake
- Commissioning or cleanup

## GitHub review focus

1. Review the additive v2 model-stage receipt schemas while confirming every
   legacy v1 schema remains byte frozen.
2. Review `capture_intake_date` routing and exact-command parsing separately
   from `study_date` provenance.
3. Review the three thin adapter boundaries and CS408 internal-only apply path.
4. Review the exact historical allowlist that reopens active release
   `ad180...` only as a rollback/preview source, never as a new target.

`READY_FOR_GITHUB_REVIEW`
