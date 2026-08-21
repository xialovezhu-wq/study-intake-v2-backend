# Sol Typed Actions Contract

Sol is the semantic curator, not the writer. Produce one machine-valid JSON document bound to the frozen batch.

## Envelope

The document must carry:

- schema version
- `batch_id` and frozen manifest hash
- deterministic `action_set_id`
- requested study date
- `actions`: safe deterministic operations only
- `unresolved`: non-writing dispositions with `status=needs_user`
- evidence refs and source hashes for every decision
- `formal_write_count=0` and `formal_writeback=none`

Canonicalize and hash the JSON according to the repository schema. Do not invent fields when the installed schema differs; validate and fail closed.

## Allowed formal targets

Only these writer-recognized action types are eligible:

- `master_bank_insert`
- `master_bank_update`
- `mastered_insert`
- `sentence_pattern_append`
- `sentence_pattern_merge`
- `skip_duplicate` for an evidence-backed deterministic no-write disposition

Each action names a logical target and typed fields. It must not contain an absolute path, arbitrary patch, shell command, Python code or instruction to overwrite a file.

## Decision rules

- Insert into master bank only with a real source article and source sentence, allowed type and writing-value enums, and no mastered or normalized-item conflict.
- Update an existing bank item rather than inserting a duplicate. Preserve old evidence and change only schema-authorized fields.
- Emit `mastered_insert` only for explicit `independent_correct_use` evidence. Guided, explained, self-reported or generated-example evidence becomes `needs_user` or skip.
- Emit `sentence_pattern_merge` for a reusable teaching structure that matches an existing SP and limit additions to the installed schema. Emit `sentence_pattern_append` only when merge-first comparison proves the structure is new; use the fixed 15-field card shape.
- Keep A/B/C export tier separate from formal action choice. An A item can still be skipped or unresolved.

## Partial batches

Put semantic ambiguity, missing context, uncertain duplicate/sense mapping, source-hash conflict and unsafe mastery claims in `unresolved`. Give each one a stable candidate ID, reason code, evidence refs and the smallest user decision needed.

Do not withhold independent safe actions merely because another item needs the user. The writer receives only the safe `actions`; unresolved items remain non-writing. A batch with applied safe actions and one or more unresolved items reports `PARTIAL`.
