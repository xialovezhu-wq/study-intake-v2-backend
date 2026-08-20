# Study Intake V2 Capture-Ready Execution Report

- Report date: 2026-08-21 Asia/Shanghai
- Feature branch: `feat/study-intake-v2-capture-ready-v1`
- Terminal status: `BLOCKED_SOURCE_BUNDLE`
- Real user Capture executed: no
- Production deployment changed: no
- Production data written: no

## Executive result

Portable source closure and the three producer admission contracts were
implemented and pushed. All current repository suites pass with synthetic,
repository-owned evidence. The required CS408 and English source bundles could
not be uniquely identified by their expected SHA-256 values, so no recovery was
guessed and no private/runtime source was imported.

The Phase 3 successor role assets and a single successor chain schema now bind:

1. durable Capture;
2. Terra Max analysis, read-only;
3. Luna Max analysis, read-only;
4. Terra Max critical review, read-only;
5. Sol formal write with global writer lease, commit receipt, and transaction
   receipt.

The production live driver is not integrated. `live_authorized` therefore
fails closed with `consumer_stage_chain_live_driver_not_integrated`; it cannot
silently fall back to the historical Luna/Luna chain. No actual model trial,
Commissioning, merge, activation, or deployment was attempted.

## Repository checkpoints

The backend `tested_head` is the code checkpoint tested before this report-only
commit. `origin_feature` matched each listed head at verification time.

| Repository | Fixed base | Base tree | Tested head | Tested tree |
|---|---|---|---|---|
| backend | `ab47517a137d04bcfbdf18968697bb678310eae6` | `697c13424f26481bd6513988c50e4c6a7fe252be` | `06ec22ceede3343a3c41c392b9f76b7eb0638670` | `0fc818c462a23f99a40a54d1dad7f7bb7bad9425` |
| shared MCP | `7e2071543adb3e4d6d016725f3e03448e329a34a` | `2cf28c84eface6021c63aac9c30f865300366ee1` | `37eab4e6638fabd6be15e31f0f278c8b6651dcf9` | `f7921bc499e01a4f4a62e999d760f272c2a5d596` |
| Math | `7c0205c74a2e28f23f52b631614f17a48b1bb7ad` | `4caf31cbec3f49339e411b99c7f5ac8684c720e5` | `7a86f828fca0357fc438755e16505c6a81002636` | `2c157bc1cbb0abbfce6966cf9af3207190b0278b` |
| CS408 | `b23f44a86adc89d04c0199e8adeba761c4e09a3c` | `aae0f15ddc64101b42532dd6d8abeff3f3035ff6` | `c9b403dd549e3ee703ec6fbba905ae7d517b80e4` | `d8468232acaaa30992a4ca188056281df5218d78` |
| English | `996fa5dd8454cf539b7ce8c1abd37da466db54ad` | `e81db99aea6b3e3c55518a86547c82145b67bc4c` | `e9f4bb2500d27bb063b13d1939c644cf99e6a32f` | `cb218368c73235b0581ef1f96db3f2b8966e39d2` |

## Commits pushed

### Backend

- `4c433b8` `feat: make backend source closure portable`
- `06ec22c` `feat: add fail-closed consumer stage contracts`

### Shared MCP

- `37eab4e` `feat: make read MCP source closure portable`

### Math

- `532818d` `feat: add portable math producer binding closure`
- `e5190aa` `feat: require explicit math capture phrase`
- `7a86f82` `fix: bind math capture authorization evidence`

### CS408

- `4fab8e4` `feat: restore portable 408 source closure`
- `5f35db8` `fix: enforce full 408 image integrity`
- `c9b403d` `feat: enforce 408 capture admission`

### English

- `0a523db` `feat: restore portable English pipeline closure`
- `492a234` `fix: normalize synthetic English fixture`
- `e9f4bb2` `feat: capture durable English raw turns`

## Changed-file summary

The authoritative lists are reproducible without a generated report family:

```sh
git diff --name-status <fixed-base>..<tested-head>
```

Material changes are grouped as follows:

- Backend: portable config/template generation, release-root parameterization,
  synthetic Dashboard and replay fixtures, private-fixture replacement,
  successor role assets, one consumer-chain schema, model-role validation, and
  live fail-closed guard.
- Shared MCP: environment-only repository roots, portable build defaults,
  sealed launcher propagation, and temporary-directory tests.
- Math: producer-binding generation, exact NFKC `快速入库` admission,
  authorization digest persistence, source-bundle verification, Skill/eval
  updates, and synthetic tests.
- CS408: portable active closure, complete PNG/JPEG/WebP verification, exact
  phrase admission for ordinary study, morning ordered buffer and single final
  Capture, recovery/idempotency tests, and Skill/eval updates.
- English: portable pipeline closure, strict durable raw-turn event on the
  existing ledger, ordered messages and attachment digests/references,
  sentence-to-parent binding, CLI/schema/tests, and no second queue.

No resolved config, descriptor, `components.json`, component lock, `.mcp.json`,
runtime state, receipt, lease, secret, production Capture, formal bank, real
question, real attachment, or release/current generated object was committed.

## Source bundle status

Expected CS408 bundle SHA-256:

`8a0067b3b9d3aa3342fcdeb8186343181467d3daa98f7549924d8359b4848428`

Expected English bundle SHA-256:

`ca3e027286f3cc32e2fbb4c8f365d281fa4763f9987a70bf0690f3d26a86a356`

Search result: zero unique matching bundles in the authorized local candidate
locations and canonical source checkouts. No multi-match case was found. Per
the execution contract, no guessed recovery or per-file source manifest was
created. Both repositories continued only from canonical tracked source,
tests, and safely reconstructed minimal code.

Execution bundle SHA-256:

`d5b5cf6d2dc47461d826bb2b5aeaff3b42a228d7d34074e7871b9bc7f9f3ded9`

## Tests run

All commands used repository-owned synthetic evidence unless explicitly noted.

| Scope | Result |
|---|---|
| Backend Core after Phase 1 | 1150/1150 PASS, 732.015 s |
| Backend Core including Phase 3 contracts | 1166/1166 PASS, 745.706 s |
| Backend Dashboard | 122/122 PASS, 4.457 s |
| Backend Phase 3 targeted role/contract/release set | 238/238 PASS, 59.199 s |
| Shared MCP, hash-locked dependency environment | 78/78 PASS, 8.879 s |
| Math final suite | 66/66 PASS, 0.672 s |
| CS408 final suite | 99/99 PASS, 0.820 s |
| English final suite | 48/48 PASS, 1.176 s |
| Independent canary cleanup check with ResourceWarning as error | 1/1 PASS, 28.525 s |

No test was skipped or xfailed to hide a failure.

## Tests and actions not run

- Exact external/private historical replay lane: not run because the required
  uniquely matched, content-addressed source evidence is unavailable.
- Actual Terra/Luna/Terra/Sol trial: not run because the Phase 3 live driver is
  intentionally fail-closed and source closure is unresolved.
- Minimal Commissioning: not implemented or run.
- Production health check, queue/dispatcher check, writer-lease service check,
  and new-record comparison: not run because no deployment occurred.
- First real Capture: prohibited and not run.

## Build results

### Shared MCP

PASS in a fresh environment installed from `requirements.lock` with
`--require-hashes`.

- portable release ID:
  `4fbb3ee6abb6795f7711d970944542865f1ac6ce283ef378db7ee295ad526a37`
- manifest SHA-256:
  `433d9a9f514715288d2d4af22fed1bf0e1aef280a5bf9059a1818b3349e98e2e`
- status: `verified`
- formal write count: `0`

### Backend immutable production build

`NOT_RUN`. The documented build requires resolved subject source roots, a
formal-surface config/baseline, and an explicit historical input manifest.
Those inputs cannot be guessed or replaced by synthetic unit fixtures. Build,
immutable verify, canary preview, activation, and rollback artifact creation
remain blocked.

### Producer repositories

No separate repository-level build command is documented. Their complete
unittest suites passed as listed above.

## Synthetic model and Sol results

- Consumer-chain zero-model callback executor: PASS.
- Exact order, role, Max effort, sandbox, tool-policy hash, output-chain hash,
  global writer lease, Sol commit receipt, and transaction receipt validators:
  PASS.
- Actual hosted model calls: `0`.
- Actual Sol formal writes: `0`.
- Production acceptance: not claimed.

## Independent audit disposition

The independent read-only audit correctly rejected deployment because the
source bundles, live successor driver, real Sol lease/commit execution, Build,
Commissioning, merge, and deployment are incomplete. It also found a Math v2
verification gap: a tampered v2 event with no source bundle could be skipped by
`verify`. That finding was fixed and pushed in `7a86f82`; the final Math suite
passes 66/66.

Findings based only on an older WORKLOG snapshot, including a stale 1144-test
failure count and a claim that expected source hashes were absent, were not
accepted. Current command output and the supplied execution contract are the
authority for this report.

## Deployment and health state

- Existing target identity: the single pre-existing local Study Intake current
  target with Dashboard and three subject dispatcher LaunchAgents, recorded
  here only as a sanitized alias.
- Deployed version from this branch: `NOT_DEPLOYED`.
- Existing current target changed: no.
- LaunchAgents changed: no.
- Capture gate changed: no; no successor deployment exists.
- Production health status for successor: `NOT_RUN`.
- Rollback reference created: no. The pre-task current target remains untouched.

## Commissioning controls

Required temporary actions are exactly:

1. release Terra;
2. confirm Terra;
3. release Luna;
4. confirm Luna.

Current result: `NOT_IMPLEMENTED`. No abandon, cancel, skip, hidden auto-confirm,
approval database, or new control platform was added. No endpoint is claimed to
exist.

## Privacy and generated-artifact attestation

- No private data was uploaded or committed.
- No real Capture was invoked.
- No production record was created or changed.
- No model-private reasoning was requested or stored.
- One early legacy test invocation, before synthetic migration, opened local
  English authority/rollout files through hard-coded legacy defaults. Its
  output exposed no private content; nothing was copied, uploaded, staged, or
  committed. The default lane was then replaced with hermetic synthetic
  fixtures and the affected suite passed 24/24. This incident prevents any
  claim that private files were never read during the entire execution.

## Known blockers and risks

1. CS408 and English source bundles are unresolved.
2. The successor live consumer driver is not integrated; live authorization
   fails closed.
3. No actual Terra/Luna/Terra/Sol isolated trial has run.
4. Minimal Commissioning is absent.
5. Backend immutable Build and verify have not run.
6. Feature branches are pushed but not merged.
7. No successor deployment or health check exists.

## Exact wake-up steps

1. Do not authorize or run a real Capture yet.
2. Provide one uniquely identifiable, sanitized local CS408 source bundle whose
   SHA-256 equals the expected CS408 hash, and one English bundle whose SHA-256
   equals the expected English hash.
3. Resume this feature branch task so Codex can create the path/hash/size/caller
   manifests without reading private/runtime content.
4. Complete and test the live Terra→Luna→Terra→Sol driver, then run the isolated
   hosted-model trial, minimal Commissioning, independent audit, immutable
   Build, merge, and deployment.
5. Confirm the deployed Capture gate is closed and production remains idle.
6. Only then give a separate explicit authorization for the first real Capture.

## Cleanup after the first real Capture

Cleanup is outside this execution and must not run early. After the first real
Capture passes its separately authorized acceptance, physically remove the four
temporary Commissioning actions, their API/UI exposure, and any waiting state.
Do not leave abandon/cancel/skip routes or a long-lived approval state machine.

## Terminal decision

`BLOCKED_SOURCE_BUNDLE`

This report does not claim `READY_FOR_USER_CAPTURE`, `READY_TO_MERGE`, or
`READY_TO_DEPLOY`.
