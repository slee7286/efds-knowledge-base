# EFDS operational truth

The operational layer is the reviewed interpretation between source evidence and future agent features.

```text
Slack / Google Docs meeting notes / OneDrive / ICU evidence
        -> proposed operational record
        -> admin review
        -> approved operational truth

Approved committee action ticket -> committee assignment/status/detail updates
```

## Canonical model

`operational_records` supports `decision`, `action_item`, `commitment`, `open_question`, and `status_update`. Review state (`proposed`, `approved`, `rejected`, `needs_review`, `superseded`) is separate from execution state (`open`, `in_progress`, `blocked`, `completed`, `resolved`, and related states).

Legacy `decisions` and `action_items` rows are backfilled into this model by migration 0012; the legacy tables remain intact for compatibility.

## Evidence and provenance

`operational_record_evidence` points to a permission-scoped `retrieval_units` row and copies its source identifiers as a citation snapshot. The source record is never rewritten by operational editing. Multiple evidence rows may support or contradict one record. Meeting notes and Slack discussions are evidence, not automatic decisions.

## Review and audit

Admin review mutations use `mutate_operational_record`, a PostgreSQL transaction-backed RPC. It resolves the actor from `auth.uid()` and an active admin profile, locks the record, checks `review_version`, mutates the record, and writes exactly one `operational_review_events` row before returning. A stale browser receives a concurrency conflict and cannot overwrite a newer decision.

Committee ticket mutations use `mutate_committee_ticket`. It permits active committee/admin profiles to create approved committee-visible action tickets, update their details and status, and replace the set of active officer assignees. It uses the same version check and audit log. These ticket operations do not grant committee members permission to approve other candidate operational records.

Records start proposed. Approval, rejection, defer, edit-and-approve, supersession, execution updates, evidence changes, and publication are audited. Rejected, completed, and superseded records are retained.

## Visibility

Raw operational records are admin-readable by default. Approved records may be explicitly published as `committee`, `member`, or `public`; RLS prevents non-admin users from reading proposed or unpublished records. Ticket assignments are visible to committee/admin only, and the committee ticket surface reads approved current action records with committee visibility.

## Retrieval

The rebuild index creates `operational_decision`, `operational_action`, `operational_commitment`, `operational_question`, and `operational_status` units. Approved operational truth receives a stronger explainable authority adjustment than candidate records, while source evidence remains separately traceable.

The current website can request cited ticket suggestions from `efds-agent`; these remain unpersisted text until a human creates a ticket. No automatic AI approval is included. A future extractor should submit candidates with evidence retrieval-unit IDs, confidence, and extraction method; it must not write approved truth directly. Recent Outlook mailbox evidence is not part of the current suggestion source set.
