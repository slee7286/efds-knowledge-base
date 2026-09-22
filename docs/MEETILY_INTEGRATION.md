> Retired on 2026-09-22. The Meetily importer has been removed. Use
> [Google Docs meeting sync](GOOGLE_DOCS_MEETINGS.md). The material below
> documents historical data only; the old commands no longer ingest.

# Meetily integration

Meetily is a local-first recorder/transcriber/summarizer. EFDS reads its
current Windows SQLite database in read-only mode at
`%APPDATA%\com.meetily.ai\meeting_minutes.sqlite`. The adapter discovered the
current tables `meetings`, `transcripts`, `transcript_chunks`,
`summary_processes`, and `meeting_notes`. Meetily currently has no separate
start/end timestamp on the meeting row, so `created_at` is used as the
available meeting time and audio timestamps are retained on transcript rows.

## Source boundary

The synchronizer never writes to Meetily's database or files. A staged export
directory can also be supplied; each meeting directory may contain
`meeting.json`, `transcript.json|md|txt`, and `summary.json|md|txt`. This is a
conservative fallback rather than an assumption about undocumented future
Meetily layouts.

Stable meeting identity is `source_type=meetily` plus Meetily's `meetings.id`.
Transcript and summary SHA-256 hashes identify artifact versions, not logical
meetings. Older artifacts remain in `meeting_artifacts` and only the latest
version is current. Timestamped transcript segments are stored separately;
speaker labels are copied only when Meetily supplies them.

Meetily summaries are stored as `artifact_type=summary`, `generated_by=meetily`,
and `review_status=source_generated`. They are labelled in the website as
“AI-generated Meetily summary — not committee-approved minutes”. No decisions,
actions, speaker identity inference, or LLM extraction is performed here.

## Sync

```powershell
$env:MEETILY_DB_PATH="$env:APPDATA\com.meetily.ai\meeting_minutes.sqlite"
python scripts/sync_meetily.py --check
python scripts/sync_meetily.py --list
python scripts/sync_meetily.py --dry-run
python scripts/sync_meetily.py --meeting MEETILY_ID
python scripts/sync_meetily.py --full
```

`--full` reconciles missing meetings; a partial/single-meeting import does
not. Repeated imports skip identical artifact hashes. Changed transcripts or
summaries create a new current version and a source-change event. A missing
meeting is retained and becomes `restored` if it reappears. Each non-dry run
uses the existing `ingestion_runs` table with `source_type=meetily`.

## Filesystem overlap

Meetily artifacts and files exported into OneDrive remain separate source
instances. Exact hash overlap can be identified later, but the meeting layer
keeps meeting identity, timestamps, and artifact type while the filesystem
layer keeps its relative path and document identity. No source is collapsed or
deleted.

## Retrieval and privacy

Current transcript artifacts become deterministic `meeting_transcript` units,
grouped by timestamped segments and bounded to approximately 2,400 characters.
Summaries and notes become `meeting_summary` and `meeting_notes` units. Their
metadata includes meeting/artifact IDs and transcript timestamp ranges.
Their authority is below approved operational knowledge: a transcript is
primary evidence of captured discussion, while a summary is a Meetily-derived
interpretation and neither is an automatically approved decision.

Meeting source tables and retrieval units are admin-only in V1. The database
RLS policies and retrieval function exclude meeting sources from member,
committee, and public retrieval. The website never calls Meetily directly;
`/admin/meetings`, `/admin/meetings/all`, and `/admin/meetings/[id]` browse
authenticated admin reads.

## Migration and verification

Review the generated SQL and apply through the backend owner only:

```powershell
alembic upgrade head
python scripts/rebuild_retrieval_index.py --source meeting_transcript --full
```

The first live import should use one test meeting, verify its artifacts and
segments in `meetings`, `meeting_artifacts`, and
`meeting_transcript_segments`, then run the full reconciliation. A future
reviewed decisions/actions layer should be separate from these source
records.
