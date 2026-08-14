# EFDS operational truth

The operational layer is the reviewed interpretation between source evidence and future agent features.

```text
Slack / Meetily / OneDrive / ICU evidence
        -> proposed operational record
        -> admin review
        -> approved operational truth
```

## Canonical model

`operational_records` supports `decision`, `action_item`, `commitment`, `open_question`, and `status_update`. Review state (`proposed`, `approved`, `rejected`, `needs_review`, `superseded`) is separate from execution state (`open`, `in_progress`, `blocked`, `completed`, `resolved`, and related states).

Legacy `decisions` and `action_items` rows are backfilled into this model by migration 0012; the legacy tables remain intact for compatibility.

## Evidence and provenance

`operational_record_evidence` points to a permission-scoped `retrieval_units` row and copies its source identifiers as a citation snapshot. The source record is never rewritten by operational editing. Multiple evidence rows may support or contradict one record. Meetily summaries and Slack discussions are evidence, not automatic decisions.

## Review and audit

All admin mutations use `mutate_operational_record`, a PostgreSQL transaction-backed RPC. It resolves the actor from `auth.uid()` and an active admin profile, locks the record, checks `review_version`, mutates the record, and writes exactly one `operational_review_events` row before returning. A stale browser receives a concurrency conflict and cannot overwrite a newer decision.

Records start proposed. Approval, rejection, defer, edit-and-approve, supersession, execution updates, evidence changes, and publication are audited. Rejected, completed, and superseded records are retained.

## Visibility

Raw operational records are admin-readable by default. Approved records may be explicitly published as `committee`, `member`, or `public`; RLS prevents non-admin users from reading proposed or unpublished records. Admin-only review mutations are deliberate for V1.

## Retrieval

The rebuild index creates `operational_decision`, `operational_action`, `operational_commitment`, `operational_question`, and `operational_status` units. Approved operational truth receives a stronger explainable authority adjustment than candidate records, while source evidence remains separately traceable.

No automatic extraction or AI approval is included in V1. A future extractor should submit candidates with evidence retrieval-unit IDs, confidence, and extraction method; it must not write approved truth directly.
