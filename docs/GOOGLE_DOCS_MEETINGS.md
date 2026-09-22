# Google Docs meeting logs

Meetily ingestion is retired. Its former entry point exits without writing;
previous meeting records, artifacts and migration history remain intact.

Run the normal collector:

```sh
.venv/bin/python scripts/sync_slack.py --all-enabled
```

It archives only explicitly enabled channels, then imports Google Docs linked
from a successfully synced channel named `meetings`. A failed meeting-channel
sync skips document fetching. The `--dry-run` option writes nothing and does
not invoke the document importer.

To retry document extraction without re-fetching Slack:

```sh
.venv/bin/python scripts/sync_google_docs_meetings.py --dry-run
.venv/bin/python scripts/sync_google_docs_meetings.py
```

Use `--channel CHANNEL_ID` to select the meeting channel explicitly. The channel
must already be discovered and enabled. Read access to a shared Google Docs
plain-text export is required; login pages, forbidden responses and empty
exports are errors, never ingested as content. No Google sharing permissions
are changed and no Slack or Google write requests are made.

Document IDs are deduplicated across messages, replies, rich blocks and file
metadata. Canonical identity is `(google_docs_meetings, Google document ID)`.
The importer stores original text in `meeting_artifacts` as `notes`, with
content hashes, immutable versions, source-change events, and Slack message
references. A reversion reuses the original artifact rather than violating
version uniqueness. Failed exports retain the last good version and produce
an ingestion-run error. Missing Slack links do not delete historical meetings.

Document first-line titles are retained (generic “Quick notes” headings skipped).
Posting timestamps are not presented as meeting dates. Authorship, approval,
and AI generation are not inferred. Existing admin-only RLS protects the data;
no migrations or permission changes are required.

Refresh lexical search after ingestion:

```sh
.venv/bin/python scripts/rebuild_retrieval_index.py --source slack_message
.venv/bin/python scripts/rebuild_retrieval_index.py --source meeting_notes
```

Embeddings and external model calls are not part of this collection step.
Google Docs export formats: https://developers.google.com/workspace/drive/api/guides/ref-export-formats
