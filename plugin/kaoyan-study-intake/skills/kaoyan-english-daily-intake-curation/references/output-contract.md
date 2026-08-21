# Daily Curation Output Contract

Use plain-text labels in chat and omit empty sections.

```text
本次结果

日期：YYYY-MM-DD
状态：COMPLETE / PARTIAL / NEEDS_PREPROCESS / NOOP / FAILED
batch_id：
action_set_id：
writer_receipt_id：
writer_receipt_path：
writer_status：

处理统计

冻结 Luna candidates：
runtime confirmed：
runtime requested_unverified：
安全动作：
已应用：
幂等 no-op：
失败：
needs_user：
needs_preprocess：

预处理覆盖

当日 effective captures：
首次冻结已覆盖：
worker run-once：未调用 / processed / no_eligible_batch / failed / dry_run / cli_error / runtime_missing
worker receipt_id：
二次冻结已覆盖：
仍缺 capture IDs：

正式变更

master_bank：新增 / 更新 / 未变更
mastered_items：新增 / 未变更
sentence_patterns：新增 / 归并 / 未变更
formal_files_changed：

验证

manifest hash：通过 / 失败
typed action schema：通过 / 失败
formal prehash CAS：通过 / 失败
post-write schema：通过 / 失败
receipt journal：通过 / 失败

待你判断

candidate_id：最小歧义与会改变动作的一个问题
```

Rules:

- `COMPLETE`: every eligible item reached applied, idempotent no-op or evidence-backed skip with no unresolved ambiguity.
- `PARTIAL`: safe covered actions were preserved and at least one item is `needs_user`, `needs_preprocess` or safely failed independently.
- `NEEDS_PREPROCESS`: effective captures exist but one bounded worker run and one refreeze still did not produce any eligible covered work for the remaining batch. If covered safe work was applied, report `PARTIAL` and retain the exact `needs_preprocess` IDs instead. Never collapse either case into `NOOP`.
- `NOOP`: the requested date has no effective captures, or every covered action was already current and there are no uncovered capture IDs; no filler write.
- `FAILED`: freeze, manifest, action schema, CAS, writer or receipt validation failed before a trustworthy result.
- Do not ask for confirmation after a safe dry-run. The exact trigger already authorized the frozen batch.
- When `needs_user` exists, ask one compact question covering only the unresolved decision; do not restate completed items or request approval for them.
- Keep the stdout-only worker receipt separate from `english_apply_receipt_v1`; only the latter is the formal writer receipt and has a canonical nightly receipt path.
- Preserve `requested_unverified` as an auditable runtime state in the final report; never display it as confirmed merely because Sol completed deep review.
