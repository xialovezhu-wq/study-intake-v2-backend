# Exact-Date Nightly Curation Contract

## Canonical paths

- Repository: `${KAOYAN_ENGLISH_ROOT}`
- CLI: `${KAOYAN_ENGLISH_ROOT}/scripts/english_learning_pipeline.py`
- State directory: `${KAOYAN_ENGLISH_ROOT}/intake`
- Events: `intake/events/YYYY-MM-DD/`
- Capture receipts: `intake/receipts/capture/YYYY-MM-DD/`
- Luna candidates: `intake/candidates/YYYY-MM-DD/`
- Nightly manifests, action documents and transactions: `intake/nightly/YYYY-MM-DD/`
- Deterministic writer receipts: `intake/receipts/nightly/YYYY-MM-DD/`

Use only candidate files returned or frozen by the CLI. A directory convention does not override manifest membership or hashes.

## Freeze

Run:

```text
python3 ${KAOYAN_ENGLISH_ROOT}/scripts/english_learning_pipeline.py freeze-nightly --state-dir ${KAOYAN_ENGLISH_ROOT}/intake --date <YYYY-MM-DD>
```

Parse the stdout freeze receipt. Require `schema_version=english_freeze_receipt_v1`, exact `status`, `batch_id`, canonical `manifest` path, `candidate_count`, `capture_event_count`, exact `uncovered_capture_event_ids` and `formal_write_count=0`. Validate the persisted manifest against `english_nightly_manifest_v1` before using it.

If the caller has already validated explicit Luna candidate paths, pass each with a repeated `--candidate <path>`. Do not mix dates. The manifest must freeze candidate paths and hashes, effective capture IDs, source hashes, formal source prehashes and the current 13-column CSV, 7-column mastered and 15-field SP schema checks.

The returned `batch_id` binds this invocation's one-shot authorization. The single bounded preprocessing retry below may replace the initial incomplete batch with one immediate refreeze in the same invocation; only the final refreeze `batch_id` is eligible for apply. After that retry path is exhausted, any later refreeze creates a new batch and requires a new exact user invocation; never reuse authorization across batches.

## Single preprocessing retry

Before treating an empty or partial candidate set as final, compare the requested date's effective capture IDs with the capture IDs covered by validated Luna candidates or the freeze receipt/manifest coverage report.

- If there are no effective capture IDs for the date, return `NOOP` without calling a model worker.
- If captures exist and some are not covered, invoke the deployed worker exactly once with the stable absolute command below. Do not use the legacy root `bin` script as a fallback.

```text
python3 ${STUDY_INTAKE_RUNTIME_ROOT}/current/bin/preprocess_worker.py --config ${STUDY_INTAKE_RUNTIME_ROOT}/current/config.json run-once --subject english --date <YYYY-MM-DD>
```

- Parse the bounded stdout scan object and its nested `receipt`. Require `schema_version=study-intake-english-run-once-receipt-v1`, `subject=english`, the exact `study_date`, `status` in `processed`, `no_eligible_batch`, `failed` or `dry_run`, and `formal_write_count=0`. Preserve `receipt_id`, counts, `processed` and `adapter_error`; do not reinterpret this stdout-only receipt as a formal writer receipt.
- A CLI envelope with `schema_version=study-intake-preprocess-cli-error-v1`, `status=error` or exit code 2 is a failed bounded attempt. Do not retry with another binary.
- If the `current` binary or config is absent, do not search for an older worker and do not pretend a retry occurred. Keep the initial manifest and mark its uncovered IDs `needs_preprocess` directly.
- When the bounded run-once command was actually invoked, wait only for its result, then call `freeze-nightly` once more for the same date.
- Do not start or restart the daemon, poll indefinitely, or run a second preprocessing attempt.
- If capture coverage is still incomplete, record the exact missing capture IDs as `needs_preprocess` in the final manifest/report. Continue only with independently validated covered candidates.

This one bounded preprocessing retry and immediate refreeze are part of the same exact-date invocation. Any later refreeze caused by CAS drift, changed evidence or another run requires a fresh exact user command and a new batch authorization.

## Candidate gate

Before Sol judgment, require:

- candidate `study_date` equals the requested date;
- every candidate hash matches the manifest;
- every `candidate_documents[].runtime_identity_status` matches its candidate, and `runtime_identity_summary` lists the exact candidate IDs under `confirmed`, `requested_unverified`, `mismatch` and `unavailable`; the frozen `mismatch` and `unavailable` lists must be empty;
- capture IDs and source hashes are present and allowed;
- `producer.role=luna_candidate_consumer`, candidate validation is `PASS`, and `runtime_identity.status` is either `confirmed` or `requested_unverified`;
- for `confirmed`, requested and observed identity are equal and independent provenance uses `runtime_attestation` or `process_metadata` with a nonempty reference; for `requested_unverified`, retain requested, observed and provenance exactly, allow Sol deep review, and never report it as confirmed;
- `mismatch` and `unavailable` runtime identities are ineligible;
- Luna reports `formal_writeback=none` and no formal write count;
- source sentences are real evidence, not generated examples;
- candidate validation succeeds through the repository CLI.

Candidates outside the frozen manifest, from another date or changed after freeze are ineligible.

## Typed action file

Write Sol's non-formal JSON action document inside the returned nightly batch directory. Validate it against the repository's typed-action schema. This file is a proposal consumed by the writer; it is not a formal data mutation.

## Dry-run

Run the deterministic writer without formal mutation:

```text
python3 ${KAOYAN_ENGLISH_ROOT}/scripts/english_learning_pipeline.py apply-nightly --state-dir ${KAOYAN_ENGLISH_ROOT}/intake --manifest <manifest.json> --actions <typed-actions.json> --dry-run
```

Continue only when the raw writer status is `DRY_RUN_VALID`, `DRY_RUN_PARTIAL` or `DRY_RUN_NO_ACTION` and manifest hashes, action schema, target schemas, duplicate checks and proposed posthashes validate. Stop on `CAS_CONFLICT` or `FAILED`.

## Apply

The exact trigger is the authorization for this frozen batch. Apply safe actions without asking again:

```text
python3 ${KAOYAN_ENGLISH_ROOT}/scripts/english_learning_pipeline.py apply-nightly --state-dir ${KAOYAN_ENGLISH_ROOT}/intake --manifest <manifest.json> --actions <typed-actions.json> --apply --authorization <batch_id>
```

The writer resolves formal paths itself, acquires its lock, enforces manifest prehash compare-and-swap, uses atomic replacement, appends the hash-chain journal and returns a receipt. Sol must not bypass any of these steps.

## Receipt gate

Require `schema_version=english_apply_receipt_v1` and the persisted receipt under `intake/receipts/nightly/YYYY-MM-DD/`. It must bind `batch_id`, `action_set_id`, `manifest_sha256` and `actions_sha256`. For apply, preserve the raw status `APPLIED`, `PARTIAL`, `NO_ACTION`, `CAS_CONFLICT` or `FAILED`; recovery has its separate `RECOVERED_*` path. Check `formal_files_changed`, `formal_write_count`, per-action results, formal prehashes/posthashes and validation. An identical replay may be a no-op; a different payload under the same authorization is a conflict.

The worker's `study-intake-english-run-once-receipt-v1` is stdout-only and has no `receipt_path`. It proves only the bounded preprocessing attempt. Never present it as the deterministic formal-entry receipt.

Never call preprocessing or apply from a timer or retained preference. No exact trigger means no freeze, no run-once and no apply.
