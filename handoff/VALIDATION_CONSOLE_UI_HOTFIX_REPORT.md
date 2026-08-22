# Validation Console UI Hotfix Report

- Report date: 2026-08-22, Asia/Shanghai
- Terminal status: `READY_FOR_FIRST_REAL_CAPTURE_AFTER_VALIDATION_CONSOLE_UI_HOTFIX`
- Scope: Backend-only Validation Console UI and tests
- Real Capture created or consumed: 0
- Study Intake production model, Provider, or MCP task calls: 0
- Production formal write delta: 0

## Result

The Validation Console false error banner and operator-facing state semantics
were corrected without changing its production control contract. The hotfix was
tested, merged by fast-forward, built as a new immutable Backend release,
strictly verified, transactionally activated, and independently commissioned.

Validation Console is not the production Capture authorization gate.
Its production OFFLINE/LOCKED state remains intentional.
This hotfix corrects false error rendering and operator-facing semantics only.

## Git identity

| Item | SHA |
|---|---|
| Starting Backend `main` | `3fa1eb47fccba986dc8b2843062bd25932af36d7` |
| Final tested and deployed hotfix code HEAD | `ab4159a52df2752ec3a8ca5bbc693a28b3701600` |
| Feature branch remote code HEAD before report-only commit | `ab4159a52df2752ec3a8ca5bbc693a28b3701600` |
| `main` remote code HEAD before report-only commit | `ab4159a52df2752ec3a8ca5bbc693a28b3701600` |

Branch: `fix/validation-console-ui-state-semantics-v1`.

The feature branch was pushed and verified against its tracking ref and live
`ls-remote`, then merged to `main` with `git merge --ff-only`. `main` was pushed,
fetched, and verified the same way. No squash, rebase, force-push, or history
rewrite was used.

This report is added after deployment as a documentation-only commit. Git
cannot embed the hash of the commit that contains this file. The exact final
feature and `main` report commit is therefore established by the post-push
tracking-ref and `ls-remote` verification, while `ab4159a...` remains the exact
tested and deployed code HEAD.

## Root cause and correction

The error panel was correctly emitted with the HTML `hidden` attribute, and the
successful JavaScript state path set `hidden=true`. The component CSS also set
`.error-panel { display: flex; }` but did not carry a local `[hidden]` override.
That made the placeholder capable of rendering as a false red error banner.

The page also presented the intentionally offline production console state as
if it were blocking the live Capture path. The API values themselves were
correct and were not changed.

The hotfix:

- adds the local rule `.error-panel[hidden] { display: none !important; }`;
- keeps true request failures visible with the original HTTP or parse error;
- clears stale successful state and disables mutation controls on request
  failure;
- explains that the page is an offline validation and audit surface;
- links to the task Dashboard and `/healthz` for production runtime truth;
- keeps `PENDING` as `PENDING`, styled amber and explained as an unpublished
  offline technical snapshot;
- renders a dedicated production `OFFLINE` explanation;
- disables the emergency-lock button when the page is not a fixture or is
  already locked;
- keeps all production Terra, Luna, Sol handoff, preflight, and formal-write
  actions unavailable.

No change was made to `dashboard/server.py`, `dashboard/validation_console.py`,
the production authorization contract, Capture routing, canary identity,
producer high-watermarks, Shared MCP, subject repositories, or formal writers.

## Modified files

- `dashboard/README.md`
- `dashboard/validation_static/index.html`
- `dashboard/validation_static/app.js`
- `dashboard/validation_static/styles.css`
- `dashboard/tests/test_validation_console.py`
- `dashboard/tests/test_frontend_api_binding.py`
- `handoff/VALIDATION_CONSOLE_UI_HOTFIX_REPORT.md` (report-only follow-up)

## Test evidence

| Scope | Result |
|---|---|
| Validation Console targeted | 11/11 PASS |
| Frontend API binding targeted | 18/18 PASS |
| Dashboard full discovery | 126/126 PASS in 7.833 seconds |
| Backend Core full discovery | 1192/1192 PASS in 810.084 seconds |
| Build Core gate | 143/143 PASS, skip count 0 |
| Build Dashboard gate | 126/126 PASS, skip count 0 |
| `git diff --check` | PASS |
| skip / xfail / expected-failure marker scan | 0 findings |

The first unbound Core invocation exposed one environment defect, not a product
regression: the portable MCP test interpreter was not set, producing 10
failures and 3 setup errors from the same missing binding. The documented
`STUDY_READ_MCP_TEST_PYTHON` binding was applied without changing product code;
the full 1192-test rerun passed.

No browser, Node, or external network dependency was added to the test suite.

## Browser and failure-path acceptance

A controlled local production-mode server rendered the source assets before
deployment. A separate controlled server returned HTTP 503 for the state API.
The real browser observed:

- error banner visible with `读取失败：HTTP 503`;
- stale release and success state replaced by `状态不可用`;
- all mutation controls disabled;
- successful recovery plus hard reload hid the banner again.

After activation, a hard reload of
`http://127.0.0.1:8767/validation-console/` observed:

- no false error banner;
- deployed release `da9b8831...`;
- `OFFLINE`, `LOCKED`, `ABSENT`, Capture-gate participation `否`, and formal
  write `0`;
- amber `PENDING` state;
- explicit offline-snapshot explanation and production truth links;
- all production mutation controls disabled.

## Immutable Build and strict verify

| Item | Value |
|---|---|
| Backend release ID | `da9b8831df0d78567bbd3edf2767ccef4f00c3e90a35a4942a016659df9a0efa` |
| Release manifest SHA-256 | `674657939785793ac2949d1b806e7d5bb28df576f5ddced2ab5e698481799447` |
| Generated config SHA-256 | `37d3860fc65c24e38fb60ab178d5846d5cc6ecef30d2bd32fe546880c61fb2a6` |
| Release profile | `concurrent_v2` |
| Execution mode | `live_authorized` |
| Strict immutable verify | `verified` |
| Shared MCP release | `4fbb3ee6abb6795f7711d970944542865f1ac6ce283ef378db7ee295ad526a37` |
| Shared MCP manifest SHA-256 | `433d9a9f514715288d2d4af22fed1bf0e1aef280a5bf9059a1818b3349e98e2e` |

The release copies of the three Validation Console static files, README, and
two modified test files were hashed against the merged source and matched
exactly.

## Formal-surface guard

| Stage | Manifest SHA-256 | Differences | Formal writes |
|---|---|---:|---:|
| Before implementation | `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352` | 0 | 0 |
| Pre-Build verification | `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352` | 0 | 0 |
| Build gate | `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352` | 0 | 0 |
| Post-activation Commissioning | `cab25cf58a88ae31e5a8b1f2ae0d456350ea4a655a96e4ed11b5eaa70e78c352` | 0 | 0 |

Frozen input hashes:

- formal config: `f385409cd2b0b8ff512db549cae34f1ea3d3f9bd715f3e83d9a31f489ef08c40`;
- formal baseline file: `4f6dbd9f827bca01c2d2ac0536f2096e23edf36392917493a8d855e6e8bc901c`.

## Preview and activation

| Item | Value |
|---|---|
| Canary manifest SHA-256 | `a1cfaa02d0e88d92dd63707d210588da38f28310e7f5bd5f22ddc81e39ff4751` |
| Preview status | `production_canary_planned` |
| Preview surface SHA-256 | `d4ba3566277e11508a9409e0c1794853b2d5d14edc0bfa7d25cf435f4f229677` |
| Preview non-mutation verified | true |
| Drain receipt SHA-256 | `50ed47a61a36bfac5e24f09df62889a99b3c2b513cc4786ef1f845117e28b2ca` |
| Activation ID | `755c0a9d62629f7346de99f928aae90ee1601ceb1416324af112a31c436faae6` |
| Prepare receipt SHA-256 | `59ca23642a8536238ba6248f18c17c6885fcd09e603c90ec062bea48adbd06b7` |
| Postcommit receipt SHA-256 | `e1ec712b403777a1ed717361daac5e06ec0ebaf35a32934ab2c9a17630da4a61` |
| Canary activation receipt SHA-256 | `ab39c3b37c7775b5fe53334e709f20f997d6b2b87b12a3f8cd3c0500d8f24793` |
| Post-activation verification SHA-256 | `b4ad6c5fb7331349e822eff4212e9ea8d0be4aa428e8065c88283fb4ccfb8f77` |
| External-profile apply proof SHA-256 | `cccb29797e2f8c3195355107908e0242921e04a5ed83fe5858c62a2252fb2c0f` |

The mutation-free preview was bracketed by independent snapshots. `current`,
all four LaunchAgent bytes and PIDs, the external Codex/MCP profile surfaces,
durable Capture and AnalysisPackage counts, active/claimed lease count,
continuation count, formal manifest, and the TCP 8767 owner matched exactly
before and after preview.

Activation used the exact expected current
`ab48738b3592ede2764cd87f9b6cfe4ebf862674d4b3cce6a5513f793d4fbf1c`
and the fresh drain receipt. No plist was edited manually and no single source
process was restarted as a substitute for deployment.

## Post-deployment Commissioning

`/healthz` returned HTTP 200 with:

- `status=ok`;
- `execution_mode=live_authorized`;
- `production_canary_runtime_ready=true`;
- `required_dispatchers=3` and `ready_dispatchers=3`;
- Math, CS408, and English `ready=true` and `release_match=true`;
- all three current stages `idle`;
- all three next actions `await_first_post_activation_capture`;
- all three queue depths and active task counts 0;
- all three canary formal-write counts 0;
- exactly three Dispatcher services and one Dashboard service running from
  `da9b8831...`;
- Dashboard owning TCP `127.0.0.1:8767`.

Independent runtime checks found:

- active or claimed lease count: 0;
- continuation file count: 0;
- `safe_paused` count: 0;
- Provider/model child count under the three Dispatchers: 0;
- global SubjectSol writer: absent and idle;
- durable Capture v1 count: 0;
- AnalysisPackage v1 count: 0.

The `/healthz` production counters remained:

- real Terra calls: 0;
- real Luna calls: 0;
- real Provider model requests: 0;
- production MCP tasks: 0;
- live Capture created: 0;
- live Capture consumed: 0;
- formal writes: 0.

## Validation Console production state

`/api/v1/validation-console/state` returned HTTP 200 with:

- `mode=production`;
- `current_release=da9b8831df0d78567bbd3edf2767ccef4f00c3e90a35a4942a016659df9a0efa`;
- `execution_mode=OFFLINE`;
- `live_gate=LOCKED`;
- `authorization=ABSENT`;
- `production_accepted=false`;
- `formal_write_count=0`;
- `emergency_locked=true`;
- all Skill, MCP preflight, and engineering values still `pending`;
- Terra, Luna, and Sol handoff action flags false.

These values remain intentional and were not treated as a production failure.

## Rollback readiness

A canonical rollback preview targeted the immediate previous Backend release
`ab48738b3592ede2764cd87f9b6cfe4ebf862674d4b3cce6a5513f793d4fbf1c`
with exact expected current `da9b8831...`.

- status: `planned`;
- operation: `rollback`;
- `launchagent_changed=false`;
- fresh drain receipt required for any apply;
- formal write count: 0;
- rollback apply executed: no.

The preview left `current` on `da9b8831...`, all services ready, all production
call counters at 0, active/claimed leases at 0, and durable Capture and
AnalysisPackage counts at 0.

## Final repository checks

Before the report-only commit, all five canonical worktrees were clean and
each local `main`, tracking ref, and live remote `main` matched. Only the
Backend repository was modified in this task. The feature branch and Backend
`main` both pointed to the tested/deployed code HEAD `ab4159a...`.

After this report-only commit, the feature branch and `main` are again checked
for a clean worktree plus local/tracking/live-remote equality. The exact report
commit is supplied by that post-push verification rather than self-embedded in
this file.

`READY_FOR_FIRST_REAL_CAPTURE_AFTER_VALIDATION_CONSOLE_UI_HOTFIX`
