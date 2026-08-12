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
