# ICU Freshdesk → EFDS PostgreSQL

## Boundary

The ICU crawler remains the owner of Freshdesk access, discovery, parsing, attachment handling, crawl state, and the durable local `data/` archive. This repository owns the PostgreSQL schema, SQLAlchemy sessions, ingestion-run accounting, and the generic EFDS knowledge records.

The integration is a one-way adapter in this repository:

```text
ICU Freshdesk → icu-crawler → icu-crawler/data/ → sync_icu.py → Supabase PostgreSQL
```

The adapter reads the crawler's current `articles/**/metadata.json` records and their referenced `article.html` and `article.md` files. It does not import or reimplement the Freshdesk crawler.

## Identity and versions

An ICU article is identified by its Freshdesk external article ID, scoped to `source_type = "icu_freshdesk"`; the canonical URL is retained and used as a fallback for older rows. `content_hash` identifies the current content version and is never used as the article identity.

`knowledge_articles` contains one current row per ICU article. A changed article updates that row in place. `knowledge_article_changes` records created, updated, missing, and restored events with previous/new hashes, source timestamps, changed fields, and a deterministic description. Full article snapshots are intentionally out of scope for V1.

## Documents table

ICU articles are not copied into `documents`. That table is for generic file/document ingestion with content-hash identity; using it for mutable Freshdesk articles would duplicate text and make updates ambiguous. ICU HTML, Markdown, provenance, and attachment references remain on the structured `knowledge_articles` record and its metadata.

## Sync lifecycle

`python scripts/sync_icu.py --data-dir <path>` creates one `ingestion_runs` row, enumerates the current local article corpus, and processes each article in an independent database transaction. A malformed article increments `records_failed` and does not stop the batch. A full successful scan can mark a previously seen article as `missing` without deleting it; a later reappearance is recorded as `restored`.

`--dry-run` reads and validates the local corpus and reports intended changes without mutating PostgreSQL or the filesystem. `--max-articles` limits processing and therefore does not perform missing-source reconciliation.

Repo A currently overwrites canonical files when content changes, but its directory layout is derived from title/category/folder and its writes are not atomic. The sync adapter therefore treats the crawler metadata paths as authoritative and does not rename or delete the existing corpus. Stable-ID filesystem paths and atomic crawler writes remain a follow-up in Repo A if title changes need to preserve the same physical directory.

## Structured ICU knowledge layer

The current corpus is 78 articles dominated by Events & Trips, Committee
Management, Summer Admin, Finance/Funding, Room Bookings, Sponsorships,
Compliance, Minibuses, Services, SUMS, and Training. See
[CORPUS_ASSESSMENT.md](CORPUS_ASSESSMENT.md) for the source-driven assessment.

`knowledge_articles` remains the authoritative current source row. The
extraction layer adds conservative relevance, topic, and role mappings and
typed derived records in `knowledge_requirements`, `knowledge_timing_rules`,
`knowledge_processes`/`knowledge_process_steps`, `knowledge_resources`, and
`knowledge_contacts`. `knowledge_topics`, `knowledge_roles`, and reusable
association tables support role dashboards and SQL filtering.

Every derived record carries source article ID, source URL, source content hash,
source update timestamp, evidence text/span, extraction timestamp, method,
confidence, review metadata, fingerprint, and `is_stale`. This avoids copying
the complete corpus while ensuring that no operational fact is source-less.

The deterministic layer handles metadata, headings, numbered steps, explicit
obligation language, dates, relative periods, URLs, emails, and resource
classification. An optional `SemanticExtractor` protocol is available for a
future provider; the default is inert and no credentials are required.

When an article hash changes, old derived rows are retained and marked stale;
their review state is preserved. Extraction creates new version-keyed
candidates, and a reviewer can approve the new version and supersede the old
one. No source article or previously reviewed derived record is deleted.

The layer supports future SQL admin views and role-specific dashboards. The
normalized fields and evidence are also ready for PostgreSQL full-text or
semantic search later; pgvector is intentionally not part of this migration.

## Application authorization boundary

```text
Microsoft / approved OTP
          ↓
Supabase auth.users.id
          ↓
profiles.auth_user_id
          ↓
member_type + officer_id + access_role + active
          ↓
Supabase RLS + Next.js server authorization
```

Migration `0004_auth_profiles_and_rls` adds `profiles` and
`auth_access_exceptions`. `auth_user_id` is a unique UUID without a managed
foreign key because the `auth` schema is owned by Supabase Auth. Email values
are lowercase and unique. Application roles remain text values to match the
existing schema style; check constraints enforce the supported role and member
type values.

### Access matrix

| Dataset | Public | Member | Committee | Admin | Backend |
| --- | --- | --- | --- | --- | --- |
| `profiles` | — | own row | own row | read/manage | read/write |
| `auth_access_exceptions` | boolean RPC only | — | — | manage | read/write |
| officers | — | — | read | read | read/write |
| ICU source/derived knowledge | — | — | read | read | read/write |
| knowledge changes/extraction/ingestion | — | — | — | read | read/write |
| documents/meetings/decisions/actions | — | — | read | read | read/write |
| Slack source tables | — | — | — | read | read/write |

No internal table receives an unauthenticated `SELECT` policy. The public
website must use explicit publication views or server APIs when public content
is eventually connected.

## Knowledge Operations V1 review boundary

The authoritative lifecycle is:

```text
source → sync → extract → proposed → review → approved → explicitly publish
                         ↑                         ↓
                    source change → stale     invalidate on source change
```

ICU `knowledge_articles` owns the external source. Every typed derived record
retains its source article, URL, content hash, source update time, evidence,
extraction method/time, and confidence. `knowledge_review_events` is an
append-only audit stream for approve, edit-and-approve, reject, defer,
supersede, publish and unpublish actions. Reviewer and publisher identities
are EFDS `profiles`, not client-provided names.

Derived review status is one field (`proposed`, `approved`, `rejected`,
`needs_review`, or `superseded`); `is_stale` is independent and means the
source version no longer matches. A stale approved row is retained until a
reviewer explicitly keeps, replaces, rejects, or supersedes it. Visibility is
also independent: `internal` is the default, followed by explicit
`committee`, `member`, or `public` publication. Publication requires approved
and current knowledge and never changes the authoritative source row.

The website applies server-side role checks before every read/mutation and
Supabase RLS repeats the boundary: administrators can review and publish;
committee users can inspect internal knowledge; member users can select only
approved, current `member`/`public` rows; anonymous public reads use the
restricted public resources view. The source article Markdown is never exposed
through the public publication path.
## Transactional review mutations

The canonical website write path is:

```text
Next.js Server Action → Supabase RPC → PostgreSQL transaction
                         ├─ auth.uid() → active admin profile
                         ├─ row lock + review_version concurrency check
                         ├─ allowlisted interpretation/publication update
                         └─ exactly one knowledge_review_events row
                       → commit (or rollback everything)
```

The RPC uses a safe `search_path` and does not trust profile IDs, roles,
statuses, or publisher IDs from the browser. An old admin tab receives a
`concurrency_conflict` error and cannot overwrite a newer decision. Edit
patches cannot address source identity, hashes, evidence, extraction metadata,
or raw ICU content. Process step removal supersedes the derived step; adding a
step copies parent provenance and records `reviewer_added` metadata.

## Slack Institutional Memory V1

Slack is a separate source boundary from ICU-derived operational knowledge:

```text
Slack workspace → read-only Web API → Python synchronizer → PostgreSQL source archive
                                                        → admin-only website browsing
```

The backend owns `SLACK_BOT_TOKEN`, API access, normalization, rate-limit
handling, checkpoints, and persistence. The website never calls Slack and has
no Slack credential. Public and member routes do not expose the archive; the
admin route reads it through normal authenticated Supabase queries and the
existing RLS boundary.

`slack_workspaces` and the workspace-scoped channel/user/message tables use
Slack's stable identifiers. A message is uniquely identified by
`workspace_id + channel_id + slack_ts`; `content_hash` identifies the current
source version, not the message itself. Replies remain individual rows linked
by `thread_ts` and `parent_message_id`. Reactions, links, and file metadata are
normalized child records. File binaries are intentionally not downloaded.

Channel discovery is metadata-only and creates disabled sync settings for new
channels. A sync reads only explicitly enabled public/private committee
channels. Full backfill uses Slack cursor pagination; incremental sync starts
from the saved newest timestamp minus a seven-day reconciliation window and
re-fetches thread roots with replies. Explicit `message_deleted` payloads are
represented as tombstones and `slack_message_changes` events; absence from a
page is never treated as deletion. Each channel is isolated in a nested
transaction so a malformed or inaccessible channel does not commit partial
rows for that channel, while `ingestion_runs` records the run outcome.

## OneDrive Filesystem Institutional Memory V1

The existing `documents` table originally used a globally unique content hash,
which made a changed file look like a new logical document and collapsed exact
copies. Migration `0008_filesystem_institutional_memory` preserves the legacy
table for existing meeting references while adding filesystem source identity
fields and `document_versions`:

```text
source_root + normalized_relative_path → documents logical source row
                                         ↓
                                  document_versions
                                  (content_hash = version identity)
                                         ↓
                              document_source_changes
```

The synchronizer stores relative paths as the primary filesystem identity and
never stores an absolute local path in the website model. Same-content copies
remain separate source rows and are exposed through exact SHA-256 duplicate
groups. Same-path edits retain the source row and add a version. Complete full
reconciliations mark absent rows missing; reappearance creates restored history.
One-to-one same-hash disappearance/reappearance can be recorded as a
rename/move, while ambiguous matches remain separate missing and created rows.

The repository-root `filesystem_ingestion.toml` explicitly excludes the three
software repositories in `12_Technology`, source-control/build/cache folders,
temporary Office files, databases, environment files, credentials and private
keys. Unsupported files are retained as metadata with `unsupported` status;
parser and hydration failures are recorded without aborting the batch.

`python scripts/sync_filesystem.py --watch` provides low-dependency local
polling for near-real-time updates. It is not authoritative: an hourly or
daily `--full` reconciliation is required because a laptop can be offline and
filesystem events can be missed. The model leaves room for future Graph
`drive_id`/`item_id` metadata without implementing Microsoft Graph now.

## Unified retrieval V1

Canonical ICU, structured knowledge, OneDrive, and Slack tables remain
independent. `retrieval_units` is a deterministic derived index for
PostgreSQL-native search. The rebuild maps current source truth into stable
UUID5 units, chunks long text, stores provenance, and retires superseded units
without deleting history. A GIN-indexed `tsvector`, title/body weights,
exact-match boosts, modest authority/recency adjustments, and `ts_headline`
provide explainable V1 retrieval.

Website search calls the `search_retrieval_units` SECURITY INVOKER RPC. RLS on
the denormalized index is deliberately restrictive: admins can read admin-only
Slack/OneDrive sources; member/public scopes can only read explicitly published,
approved, current, non-stale structured knowledge. No application-side
post-filtering is used as the access boundary. Rebuild with
`python scripts/rebuild_retrieval_index.py --full` after source synchronization,
review, or publication changes.

## Meetily Meeting Ingestion V1

Meetily is a local source producer, not a second EFDS transcription service:

```text
Meetily SQLite / supported export
             ↓ read-only adapter
        meetings (stable logical identity)
             ↓
 meeting_artifacts (immutable transcript/summary/note versions)
             ↓
 meeting_transcript_segments (timestamps and supplied speakers)
             ↓
 retrieval_units (meeting_transcript / meeting_summary / meeting_notes)
```

The current Windows installation was inspected and stores its records in
`%APPDATA%\com.meetily.ai\meeting_minutes.sqlite`. The adapter reads the
`meetings`, `transcripts`, `transcript_chunks`, `summary_processes`, and
`meeting_notes` tables without modifying them. An export-directory fallback
supports explicitly staged `meeting.json`, transcript, and summary files.

Meetily's meeting ID is the logical identity. SHA-256 identifies an artifact
version only. Changed transcripts/summaries retain their old artifact and add
a current version; unchanged hashes are skipped. Timestamped segments are
stored structurally and unknown speakers remain null. The summary is marked
`generated_by=meetily` and `source_generated`; it is not authoritative minutes
and no decision/action extraction occurs in this milestone.

Meeting tables and meeting retrieval units are admin-only under the V1 RLS and
retrieval policies. A source file exported into OneDrive remains an independent
filesystem source instance even when its content hash matches a meeting
artifact. See [docs/MEETILY_INTEGRATION.md](docs/MEETILY_INTEGRATION.md) for
sync commands, limitations, and the first-import procedure.

## Decisions, Actions & Operational Truth V1

Operational records are a separate reviewed interpretation layer over ICU,
Slack, Meetily, document and retrieval evidence. Migration 0012 adds one
canonical `operational_records` model for decisions, action items,
commitments, open questions and status updates, plus many-to-many evidence and
review-event tables. Legacy decisions and action items are backfilled without
deleting their original rows.

Admin mutations use the `mutate_operational_record` Supabase RPC. The function
resolves the actor from `auth.uid()`, requires an active admin profile, locks
the row, checks `review_version`, updates the record and inserts one audit event
in the same transaction. Source rows remain immutable. Approved/current
records can be explicitly published; RLS keeps proposed and internal records
out of member/public retrieval.

## Semantic and hybrid retrieval V1

Migration 0013 adds versioned `retrieval_embeddings` backed by PostgreSQL
pgvector. The backend-only embedding worker applies an explicit source privacy
allowlist and content/model hash idempotency. ICU and ICU-derived structured
knowledge, approved operational records, and normalized OneDrive
`01_governance` records are eligible by default. Other OneDrive areas, Slack,
and Meetily sources require explicit configuration.

The Python retrieval service preserves lexical search and adds semantic and
RRF hybrid modes. Semantic SQL filters current state, visibility, source
filters, and history before returning vector candidates. Website search keeps
the existing Supabase lexical boundary until a backend HTTP retrieval service
is deployed; the provider key is never placed in Next.js.
## Retrieval Evaluation V2 and agent boundary

Retrieval remains a derived, permission-scoped layer over the canonical ICU,
OneDrive, Slack, Meetily, and operational models. The final evaluated ranking
uses semantic retrieval as the primary order and applies lexical rescue only
for high-confidence exact identifiers, URLs, filenames, and exact-title
signals. Generic lexical/semantic RRF is retained as an evaluation strategy,
not the selected default, because it displaced relevant semantic results in
the reviewed development and holdout tests.

The agent boundary is `ContextPackage`, not a model call. Packages contain
provenance-preserving `RetrievalResult` values and deterministic diagnostics.
Empty packages are marked low evidence and are not padded with unrelated
records. RLS and source-specific authorization remain enforced during both
lexical and semantic retrieval.

Retrieval Evaluation V2 uses a frozen reviewed benchmark plus deterministic
development/holdout files. The current corpus has no substantive Slack,
Meetily, or operational records, and 21 governance document units are
eligible but currently missing embeddings under the conservative external-
provider policy. Consequently the
current evidence remains a NO-GO for certifying a system-wide EFDS Agent V1
until approved document coverage is embedded; current coverage is reported by
`scripts/report_embedding_coverage.py` and policy is documented in
`docs/EMBEDDING_PRIVACY_POLICY.md`.
