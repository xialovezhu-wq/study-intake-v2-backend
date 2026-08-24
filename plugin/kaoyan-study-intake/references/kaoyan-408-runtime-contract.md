# 408 skill runtime contract

Read this contract only when a request crosses current-question, capture, Luna, or
formal-curation boundaries. A healthy learner turn follows the short hot-path card in
its owning skill and does not reopen this file.

## Routing

| Outcome | Owner | Boundary |
|---|---|---|
| Prepared A-D answer and next surface | `kaoyan-408-daily-study-loop` or question worker | One `answer-current-and-next` call |
| Eligible failure capture and recovery | `kaoyan-408-wrong-intake` | Answer-safe fact capture; zero formal writes |
| Background semantic analysis | Luna worker | Sealed current evidence and pinned bounded candidates only |
| Explicit-date batch curation | `kaoyan-408-daily-intake-curation` and Sol | Frozen failure captures; one formal item at a time |
| Named formal maintenance | `kaoyan-408-wrong-intake` cold route | Narrow explicit scope |
| Named pre-2026-07-12 transaction recovery | `kaoyan-408-intake-coordinator` | Legacy receipt/WAL scope only |

## One external learner-turn entry

For every first-answer or same-question correction reply, call exactly once:

```text
python3 scripts/managed_408_current_turn.py --repo <repo> --private-root <root> answer-current-and-next --display-receipt-locator <current-question-turn://sha256/...> --choice <A|B|C|D> --confidence <high|medium|low> --prompt-level <none|L1|L2|L3|L4|L5> --trace-json '<JSON list>'
```

The external result schema is `managed-408-answer-current-and-next-v1`. Keep the same
display receipt while correcting one item. `--trace-json` contains only this turn's
events; each event
objects have exactly `role`, `kind`, and `text`. Roles are `learner` and `assistant`;
kinds are `utterance`, `first_action`, `reasoning`, `hint`, `correction`,
`restatement`, and `answer`. An exact canonical learner answer event containing only
`A|B|C|D` must match `--choice`; a conflict fails closed. Free-form answer speech is
preserved and receives a separate canonical choice event when needed.

The command internally validates the display receipt, creates operation/request/time
identities, opens exactly one prepared evaluator, freezes feedback, commits the
current answer/session evidence, commits the applicable observation or capture, and
publishes a successor only when its receipts allow it. The model must not separately
call `date`, `prepare-current-turn`, `current-turn`, `continue-current`, `next-item`,
the retired direct router, or a capture writer, and must not copy intermediate JSON
by hand.

## Exact branch contract

- High-confidence, unprompted independent correct creates one private
  `current-question-study-observation-v1` with
  `processing_status=awaiting_background_analysis`, creates no failure capture, and
  may publish the prepared successor.
- First `fragile_correct` creates no observation, but creates one
  `current-question-evidence-bundle-v3` plus one answer-safe capture at
  `awaiting_daily_curation`; its background handoff becomes `ready` with
  `completion_kind=first_turn_complete` only when evidence is complete. An image
  role or integrity gap remains `evidence_pending` with zero model calls.
- First `wrong|partial|uncertain` creates the same bundle/capture pair under
  `current_question_failure_standing_policy_v1`, returns
  `status=feedback_ready_continue_current`, and sets
  `next_item_published=false`. Its handoff is `teaching_pending` and its turn receipt
  has `advance_allowed=false`; the next correction uses the same display receipt and
  unified command.
- A later still-wrong reply uses the same display receipt and command, accumulates the
  private trace, and creates no second first result or capture. A later correct reply
  publishes `teaching-resolution-attestation-v1`, a capture-bound
  `current-question-trace-supplement-v1`, and session resolution
  `relearn_required` with `mastery_effect=none`, `retention_effect=none`, and
  `independent_repair=false`; it then promotes the handoff to `ready` with
  `completion_kind=teaching_resolved`, and only then may publish the successor.
- Unstable evidence or a receipt/private-layer failure returns an exact recovery
  state. Recovery is type-preserving: independent correct restores only its
  observation and never creates a capture; capture-eligible results restore only
  bundle/capture. Successful recovery closes the original answer operation and may
  return its gated successor. The caller displays answer-bearing feedback and a next
  surface only when the returned fields authorize them.

The external result always reports `luna_call_count=0` and
`formal_write_count=0`. Its `next_item_published` field and `next_item` object are the
only successor truth; do not infer navigation from correctness or capture status.

## Bounded private interaction trace

The normalized object is `current-question-interaction-trace-v2`:

```text
schema, events[{ordinal,role,kind,text}], event_count, truncated
```

It keeps at most 24 included events, 2048 UTF-8 bytes per event, and 32 KiB canonical
total. Per-call input beyond a cap is rejected. It always records original, included,
and omitted counts, exact omitted ordinal ranges, the truncation reason, and the
full-trace SHA-256. Never call an omitted trace complete. Events contain only observed
current-question speech, never reconstructed history or inferred learner speech.

The first-turn trace is private inside `current-question-evidence-bundle-v3`. An
`image_question` bundle contains one to eight ordered original-byte images and needs
at least one `question_image` and one `solution_image`; a ninth image, corrupt file,
role gap, size/hash drift, or declared/detected format mismatch fails closed before
any model call. A `dialogue_only` bundle contains no image rows. A
resolved failure's bounded cumulative trace is stored in a private capture-bound
supplement. Public captures contain only answer-safe facts and evidence refs. The
formal repository and public ledgers never receive the complete question, answer,
options, full response, full explanation, grader, images, handwriting, or private
filesystem path.

## Hot-path isolation

The learner turn reads no personalization, MEMORY, history, formal nodes, knowledge
indexes, relations, old questions, linked practice, another prepared surface, Luna,
worker/consumer/report state, port 8767, end-of-day state, reference books, or source
code. It runs no `--help`, status, audit, reconcile, generation, schema discovery,
whole-ledger scan, or repository crawl.

An idempotent replay of the same answer operation returns the sealed original result
with zero new learner-evidence writes. Receipt and capture recovery reuse the original
identities and append only the missing type-preserving layer. They close the original
operation on success and never duplicate a first result, observation, bundle,
capture, teaching resolution, trace supplement, or successor. Internal navigation
also binds the prior receipt to the current session navigation frontier; a stale receipt cannot
skip a later answered-but-unresolved item.

## Deterministic knowledge and history snapshot

Background preprocessing, not the learner turn and not Luna free search, must build a
deterministic, bounded, answer-safe candidate snapshot pinned to the exact evidence
manifest and source/index cutoffs. It may include candidate subject/module/chapter and
knowledge IDs, prior formal wrong questions and outcomes, chapter-history aggregates,
and existing relation candidates with stable source references and hashes.

Snapshot membership proves only that a candidate existed under the bound cutoffs. It
does not prove a classification, recurring error, identity, or relation. If the
runtime has not supplied a verified snapshot schema, locator, hash, and binding,
report the background input as missing or limited; never invent those fields and
never repair them by scanning the repository in the learner turn.

## Failure background-handoff gate

The private immutable object is `current-question-background-handoff-v1`; its atomic
pointer is `current-question-background-handoff-binding-v1`, and its locator is
`current-question-background-handoff://sha256/<object_sha256>`. Both bind the exact
capture ID; the object also binds context/item, evidence-manifest hash, capture and
resolution receipts, completion kind, and applicable trace-supplement hashes.

The pending object is published before capture commit. A `fragile_correct` capture is
promoted to `ready/first_turn_complete`; `wrong|partial|uncertain` stays
`teaching_pending` until the resolved supplement and final receipt exist, then becomes
`ready/teaching_resolved`. Missing, unsafe, drifted, unknown, or pending state is
defer/fail closed. `recover-current-capture` follows the same transition. Regression
or a different ready identity fails closed.

## Luna proposal boundary

The learner-facing chat never starts, wakes, waits for, polls, retries, or inspects
Luna, its worker, consumer, dashboard, report, or port 8767. An observation at
`awaiting_background_analysis` and a capture at `awaiting_daily_curation` prove
durable input state only; neither proves that Luna ran.

Only a fully verified bundle-v3, trace-v2 handoff `ready` object is eligible for
semantic processing through the consumer in the current immutable preprocessor
release. Every analysis, critical review, and recovery request is fixed to
`gpt-5.6-luna` with `reasoning_effort=max`; no fallback model or lower effort is
allowed.
Ready proves completed input, not a Luna call or completed report. A resolved failure
must bind its `resolved_trace` object and binding hashes; Luna must not infer an
unfinished correction exchange.

The actual Luna artifact remains `study-intake-luna-analysis-v2`. Luna may read only
the exact sealed current private bundle, bounded interaction trace, the applicable
correct observation, and the verified pinned candidate snapshot. It must not crawl
the knowledge base or formal history. It may propose:

- subject, module, chapter, and knowledge classification;
- directly supported learning breaks and unresolved gaps;
- matches to the bounded prior-error or chapter-history candidates;
- candidate formal-node or relation links;
- counterevidence, confidence, and exact Sol verification actions.

Every Luna result is advisory. It cannot alter learner facts, capture status, formal
identity, taxonomy, relations, mastery, scheduling, batch authorization, or formal
repository state. Missing, thin, stale, failed, or absent Luna output never changes
the hot-path terminal and never delays the next learner response.

## Explicit-date Sol curation

Only an explicit user-specified `Asia/Shanghai` study date authorizes
`kaoyan-408-daily-intake-curation` to freeze that date's failure captures. Correct-only
observations are excluded from the formal wrong-question batch.

For each frozen capture, Sol reopens the raw capture, exact private bundle/first-turn
trace, the resolved trace supplement when required, the verified deterministic
snapshot when present, and at most one consumable Luna v2 report. Sol independently
verifies direct source/hash/date/identity bindings and
records one `adopt`, `modify`, or `reject` decision for every Luna classification,
break, historical match, or relation proposal. A snapshot candidate or Luna reference
alone is never sufficient evidence.

An immutable user/source/date/answer/provenance conflict, unresolved identity, or
material unresolved field becomes `needs_user` with apply count zero. Otherwise one
answer-safe Sol-approved package reaches deterministic preflight and at most one
batch-size-1 formal apply. The next formal item cannot start before the current item
reaches a receipt-bound terminal. A successful apply with failed closeout resumes
only from its original receipt and is never replayed.

managed_408_runtime_v10=entry:managed-408-answer-current-and-next-v1-only;correct:current-question-study-observation-v1;failure:current-question-evidence-bundle-v3-plus-capture;correction:same-display-until-resolved;recovery:type-preserving-operation-close;navigation:frontier-bound;handoff:evidence-pending-or-ready-fail-closed;trace:choice-bound-current-question-interaction-trace-v2-plus-resolved-supplement;snapshot:deterministic-bounded-pinned-input;luna:two-pass-ready-gpt-5.6-luna-max-proposal-only;formal:explicit-date-sol-verification
