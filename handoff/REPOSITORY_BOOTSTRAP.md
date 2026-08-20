# Repository Bootstrap

Generated: 2026-08-20, Asia/Shanghai

Scope: Study Intake V2 Repository Bootstrap only. No business implementation, test run, build, immutable verification, deployment, release tag, or implementation branch was created.

## Bootstrap Status

- SUCCESS
- repositories_created: 5
- repositories_reused: 0
- github_owner: `xialovezhu-wq`

## Repository Mapping

| component | source_workspace | canonical_local_workspace | github_repository | visibility | default_branch | baseline_commit_sha | tree_sha | imported_file_count | pushed | remote_verified |
|---|---|---|---|---|---|---|---|---:|---|---|
| backend | `/Users/xiazhibin/Documents/Codex/2026-08-10/lunamax-mcp-lunamax-lunamax-provider-codex/work/three-subject-successor` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/study-intake-v2-backend` | `xialovezhu-wq/study-intake-v2-backend` | PRIVATE | `main` | `80d8a4c0ad9b60ac8dbd0c37a5e9fd90bddf727f` | `b607a56c3d8025182e2f2b07b0e7c8cbd6c2c2a2` | 630 | true | true |
| shared MCP | `/Users/xiazhibin/Documents/Codex/local-study-read-mcp` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/local-study-read-mcp` | `xialovezhu-wq/local-study-read-mcp` | PRIVATE | `main` | `7e2071543adb3e4d6d016725f3e03448e329a34a` | `2cf28c84eface6021c63aac9c30f865300366ee1` | 45 | true | true |
| math | `/Users/xiazhibin/Documents/kaoyan-math` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-math` | `xialovezhu-wq/kaoyan-math` | PRIVATE | `main` | `7c0205c74a2e28f23f52b631614f17a48b1bb7ad` | `4caf31cbec3f49339e411b99c7f5ac8684c720e5` | 11 | true | true |
| CS408 | `/Users/xiazhibin/Documents/kaoyan-408` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-408` | `xialovezhu-wq/kaoyan-408` | PRIVATE | `main` | `b23f44a86adc89d04c0199e8adeba761c4e09a3c` | `aae0f15ddc64101b42532dd6d8abeff3f3035ff6` | 24 | true | true |
| English | `/Users/xiazhibin/Documents/kaoyan-english` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-english` | `xialovezhu-wq/kaoyan-english` | PRIVATE | `main` | `996fa5dd8454cf539b7ce8c1abd37da466db54ad` | `e81db99aea6b3e3c55518a86547c82145b67bc4c` | 19 | true | true |

For every mapping, the baseline commit was compared with both `git ls-remote` and the GitHub ref API. GitHub visibility was re-read as `private`, default branch was re-read as `main`, and each new canonical worktree had exactly one remote named `origin` pointing to its matching GitHub repository.

## Import Policy

- clean current tree baseline
- old history not pushed
- Gitee remotes preserved
- planning snapshot remains non-canonical
- old source workspaces were read-only import sources
- backend and shared MCP used bounded root allowlists
- math, CS408, and English used the exact sanitized source allowlists from the planning snapshot, with additional removal of prohibited local bindings and private runtime artifacts
- included source files are byte-identical to their source counterparts except for the five `.gitignore` files, which received only additional key and log-output safety exclusions

## Exclusions

No secret value, credential content, user dialogue, Capture payload, attachment content, or model output is reproduced in this report.

Common exclusions:

- all old `.git` directories, refs, objects, reflogs, and remotes
- `.env` files and local secret configuration
- keys, certificates, tokens, cookies, and credential material
- SQLite, database, WAL, and SHM files
- logs, launch or service logs, runtime state, locks, queues, receipts, and temporary state
- cache, virtual environments, generated output, build output, package output, and temporary directories
- uploads, user attachments, large source documents, PDFs, images, archives, and local absolute-path configuration artifacts

Component-specific exclusions:

- backend: local runtime configuration and release metadata, local workspace manifest and example binding configuration, private MCP override, local component bindings and launcher, dashboard runtime projection, compressed MCP transport data, model stage output, model transcript fixtures, generated validation/output material, and Python cache artifacts
- shared MCP: old Git history, virtual environment, work directory, cache and egg metadata, local canary-path configuration, and the local absolute-path configuration module
- math: everything outside the frozen source allowlist, including the real quick-intake ledger, attachment source tree, formal cards, generated relationship output, personal Wiki content, lecture documents, local producer-binding descriptor, cache, and temporary migration material
- CS408: everything outside the frozen source allowlist, including the full runtime/state tree, signing-key categories, SQLite hot-state databases, events, queues, receipts, question cards, personal Wiki material, books, attachments, local producer-binding descriptor, cache, and generated output
- English: everything outside the frozen source allowlist, including intake events, candidates, receipts, deferred intake, transcripts, source images, articles, personal banks, Wiki material, isolated databases, local producer-binding descriptor, cache, and generated output

## Security Scan

The scan was run against the exact staged baseline trees before the initial commits.

| repository | filename scan | credential pattern scan | database scan | log scan | large-file scan over 10 MiB | symlink/external-path scan | local absolute-path configuration | result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `study-intake-v2-backend` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| `local-study-read-mcp` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| `kaoyan-math` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| `kaoyan-408` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| `kaoyan-english` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |

Checks included sensitive filenames, high-confidence GitHub/OpenAI/Anthropic/AWS/Google/Slack/private-key/JWT/credential-URL patterns, database filenames and SQLite magic, log suffixes, file sizes, symlink Git modes, resolved external targets, local absolute-path configuration formats, `git diff --cached`, and `git status --short`.

The staged diff review found only added files: 630 backend, 45 shared MCP, 11 math, 24 CS408, and 19 English. No staged binary file or deletion was present. The shared MCP source retained three pre-existing blank-line-at-EOF findings; they were not rewritten because this bootstrap was not authorized to change MCP source.

## Worktree Status

Final `git status --short` after the handoff commit and push:

| canonical worktree | status |
|---|---|
| `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/study-intake-v2-backend` | empty |
| `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/local-study-read-mcp` | empty |
| `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-math` | empty |
| `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-408` | empty |
| `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-english` | empty |

## Known Limitations

- No test suite was run. The baseline commits do not establish test correctness.
- No Build, immutable verify, deployment, release tag, or real Capture validation was run.
- These are sanitized source baselines, not mirrors of personal learning data or old Git history.
- Portable replacements for the excluded local binding/configuration artifacts were not authored in this bootstrap. In particular, the shared MCP local configuration module and the three subject producer-binding descriptors are absent by design.
- Backend tests that depended on excluded raw model transport or transcript fixtures may require new synthetic fixtures before they can run.
- Non-configuration source, test, and documentation files still contain some non-secret local path literals. Portability was not verified, and no business logic was changed to rewrite them.
- The shared MCP staged diff had three pre-existing blank-line-at-EOF findings. They were retained byte-for-byte.
- The old source worktrees were dirty before import. Their prior branches, commits, uncommitted changes, and Gitee bindings were neither pushed nor normalized.
- No runtime or release state was queried to claim readiness. `REAL_CAPTURE_READY` is not claimed.

## Next Session Binding

| component | exact repository | exact baseline SHA | exact canonical local workspace |
|---|---|---|---|
| backend | `xialovezhu-wq/study-intake-v2-backend` | `80d8a4c0ad9b60ac8dbd0c37a5e9fd90bddf727f` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/study-intake-v2-backend` |
| shared MCP | `xialovezhu-wq/local-study-read-mcp` | `7e2071543adb3e4d6d016725f3e03448e329a34a` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/local-study-read-mcp` |
| math | `xialovezhu-wq/kaoyan-math` | `7c0205c74a2e28f23f52b631614f17a48b1bb7ad` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-math` |
| CS408 | `xialovezhu-wq/kaoyan-408` | `b23f44a86adc89d04c0199e8adeba761c4e09a3c` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-408` |
| English | `xialovezhu-wq/kaoyan-english` | `996fa5dd8454cf539b7ce8c1abd37da466db54ad` | `/Users/xiazhibin/Documents/Codex/github-canonical/study-intake-v2/kaoyan-english` |

Suggested later implementation branch, only after a new planning session explicitly authorizes implementation:

`replan/terra-max-capture-contract`

## Prohibited Assumptions

- 未运行测试不得视为通过
- 未 Build 不得视为可发布
- 未部署不得视为上线
- planning snapshot 不得视为 canonical source
- baseline push 不得视为 `REAL_CAPTURE_READY`
- sanitized source baseline 不得视为完整个人学习数据备份
