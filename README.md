# EFDS Knowledge Platform

**An end-to-end data, AI and software platform built for Imperial College London's Economics, Finance & Data Science Society.**

I designed and built the system across the full application stack — from **data ingestion and PostgreSQL architecture to secure retrieval, AI integration, authentication, APIs and the user-facing web application**.

### What this project demonstrates

* **End-to-end system design:** multiple data sources → ingestion pipelines → PostgreSQL/Supabase → permission-aware retrieval → AI service → web application.
* **Backend & data engineering:** relational schema design, migrations, versioned ingestion, provenance, audit trails, structured extraction and hybrid search.
* **AI systems:** embedding-based retrieval, grounded model calls, citation validation, context controls and streamed responses.
* **Security & access control:** Supabase Auth, PostgreSQL Row-Level Security, role-based authorization and separation of browser, server, database and model credentials.
* **Full-stack integration:** Python services, PostgreSQL, FastAPI and a Next.js/TypeScript frontend integrated into one working system.

This is not an isolated chatbot or database project: it demonstrates my ability to **design, build and integrate a multi-service software system from data ingestion through backend infrastructure and AI services to the final application.**

## Repositories

| Repository                                                                 | Role                                                                                                                                                              |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[efds-knowledge-base](https://github.com/slee7286/efds-knowledge-base)** | Core PostgreSQL/Supabase data platform, ingestion, schema migrations, provenance, permissions, retrieval and embeddings                                           |
| **[efds-agent](https://github.com/slee7286/efds-agent)**                   | FastAPI AI service for authenticated retrieval, grounded model synthesis, citation validation and streaming                                                       |
| **[efds-site](https://github.com/slee7286/efds-site)**                     | Next.js/TypeScript web application, authentication, authorization, admin tooling and agent integration                                                            |
| **[efds-recruiting](https://github.com/slee7286/efds-recruiting)**         | Separate local-first recruiting intelligence system demonstrating application workflows, APIs, automation, browser integration and private/public data boundaries |

## System architecture

```text
ICU / OneDrive / Slack / Meetings
              │
              ▼
      Ingestion + Versioning
              │
              ▼
      PostgreSQL / Supabase
   RLS · Audit · Search · pgvector
              │
              ▼
   Permission-aware Retrieval
              │
              ▼
       FastAPI AI Service
              │
              ▼
 Grounded Model Responses + Citations
              │
         SSE / API Layer
              │
              ▼
      Next.js / TypeScript
              │
              ▼
        End-user Application
```

## Technical stack

**Data & backend:** Python · PostgreSQL · Supabase · SQLAlchemy · Alembic · pgvector
**AI:** FastAPI · OpenAI Responses API · embeddings · hybrid retrieval · citation validation
**Frontend:** Next.js · TypeScript · Supabase Auth · server-side authorization · SSE streaming
**Sources:** ICU Freshdesk · OneDrive · Slack · meeting transcripts · structured operational records

<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>
<br>

# Current Repo: EFDS Platform — V1 ingestion foundation

This repository is the first EFDS (Economics, Finance & Data Science Society) institutional-memory layer. It stores structured records and extracted document text in PostgreSQL while OneDrive/SharePoint remains the canonical store for binary files.

The current platform includes the database/filesystem foundation, ICU ingestion and structured knowledge review, plus a read-only Slack institutional-memory archive. Embeddings, a website, and Fireflies integration are outside this backend's ingestion scope.

## Architecture

- Python 3.12+ scripts and a small `src/efds` package
- SQLAlchemy 2.x with psycopg 3 for direct PostgreSQL access
- A standard `postgresql://` `DATABASE_URL` is automatically routed through the psycopg 3 SQLAlchemy driver
- Alembic for reproducible schema migrations
- Extracted text and provenance metadata in PostgreSQL; no binary uploads
- SHA-256 content hashes provide idempotent document ingestion
- `ingestion_runs` records progress, counters, and per-file errors
- ICU Freshdesk articles are synchronised from the existing `icu-crawler/data/` archive into `knowledge_articles`
- Slack is synchronised by `scripts/sync_slack.py` into private, allowlisted canonical source tables; the Slack token stays in the backend environment
- The local EFDS OneDrive tree is synchronised by `scripts/sync_filesystem.py` into stable source rows and immutable document versions
- Google Docs linked in the enabled Slack `meetings` channel are imported automatically after Slack sync into versioned, admin-only meeting notes. See [Google Docs meeting sync](docs/GOOGLE_DOCS_MEETINGS.md). Meetily sync is retired; existing history is retained.

The direct Supabase PostgreSQL connection is suitable for local development, Alembic, and ingestion scripts. A transaction/session pooler may be preferable for a future serverless deployment.

If the direct `db.<project-ref>.supabase.co` hostname cannot be reached from your Windows network, check IPv6 support. Supabase direct database endpoints are IPv6 unless the project has IPv4 enabled. For an IPv4-only network, copy the Supavisor **session mode** connection string from the Supabase Connect dialog instead; it uses the shared pooler on port `5432` and works with Alembic and this SQLAlchemy backend. Do not guess the pooler region or username.

## Windows PowerShell setup

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env` and set `DATABASE_URL` to the direct PostgreSQL connection string. Do not commit `.env` or put credentials in source files.

Apply the schema and check the connection:

```powershell
alembic upgrade head
python scripts/test_db.py
```

The migration enables `pgcrypto` for PostgreSQL UUID generation. Do not paste schema SQL into Supabase manually; Alembic is the source of truth.

Seed the 2026-27 officers (safe to run repeatedly):

```powershell
python scripts/seed_officers.py
```

Ingest a folder recursively:

```powershell
python scripts/ingest_folder.py "C:\path\to\EFDS Society" --academic-year 2026-27
```

Optional flags are `--source-type onedrive`, `--dry-run`, and `--verbose`. Supported files are `.txt`, `.md`, `.docx`, `.pdf`, `.pptx`, `.xlsx`, `.eml`, and `.html`. Duplicate bytes are skipped by `content_hash`; a failed individual extraction is logged and does not stop the rest of the run.

## ICU Freshdesk sync

The crawler remains the owner of Freshdesk crawling, parsing, attachments, and its durable local `data/` directory. This repository only reads that current archive:

```text
ICU Freshdesk → icu-crawler → data/ → scripts/sync_icu.py → Supabase PostgreSQL
```

Apply the schema migration manually before the first sync:

```powershell
alembic upgrade head
```

Bootstrap PostgreSQL from the already-populated crawler corpus without network access:

```powershell
python scripts/sync_icu.py --data-dir "C:\Users\slee7\OneDrive - Imperial College London\Imperial EFDS Society 26-27\12_Technology\icu-crawler\data"
```

For future runs, crawl Repo A and then sync its current output:

```powershell
python -m efds_ingest crawl --data-dir "C:\Users\slee7\OneDrive - Imperial College London\Imperial EFDS Society 26-27\12_Technology\icu-crawler\data"
python scripts/sync_icu.py --data-dir "C:\Users\slee7\OneDrive - Imperial College London\Imperial EFDS Society 26-27\12_Technology\icu-crawler\data"
```

Useful sync options are `--dry-run`, `--max-articles N`, `--force`, and `--verbose`. Dry runs read and validate the local archive but perform no database or filesystem writes. A full sync creates one `ingestion_runs` row and processes each article independently, so one malformed article does not stop the batch.

`knowledge_articles` keeps one current row per ICU Freshdesk article, keyed by source-scoped Freshdesk ID; the canonical URL is retained as a fallback. Content changes update that row in place. `knowledge_article_changes` records created, updated, missing, and restored events with hashes, timestamps, changed fields, and deterministic descriptions. ICU articles are not duplicated into the generic `documents` table.

The crawler marks disappeared URLs stale and does not delete local files. After a successful complete sync, previously seen ICU rows are marked missing in metadata rather than deleted; a later reappearance is recorded as restored. Limited runs do not reconcile missing articles.

## ICU structured extraction

The current corpus assessment and schema rationale are in
[CORPUS_ASSESSMENT.md](CORPUS_ASSESSMENT.md) and
[ARCHITECTURE.md](ARCHITECTURE.md). After syncing the current crawler archive,
run the structured extraction layer:

```powershell
python scripts/extract_icu_knowledge.py --dry-run
python scripts/extract_icu_knowledge.py --deterministic-only
python scripts/extract_icu_knowledge.py --article-id 101000590031 --force
python scripts/extract_icu_knowledge.py --relevance-min high
```

The first non-dry run processes the 78 current articles, seeds controlled topic
and role taxonomies, and creates proposed records for explicit requirements,
multiple timing-rule shapes, numbered processes, links/forms/systems, and
explicit email contacts. Repeating it without source changes skips articles by
`last_extracted_content_hash`. A changed source hash marks old derived rows
stale, retains their review state, and creates new proposed versioned rows. No
LLM is required; the semantic provider boundary is optional and currently
inert.

Run unit tests:

```powershell
pytest
```

The duplicate-document integration test is skipped by default. It requires an explicitly configured, migrated test database and `RUN_DB_TESTS=1`.

## Database tables

The initial migration creates:

- `officers`
- `documents`
- `document_versions` and `document_source_changes`
- `meetings` and `meeting_attendees`
- `decisions` and `action_items`
- `slack_workspaces`, `slack_channels`, `slack_channel_sync_settings`, `slack_users`, `slack_messages`, `slack_message_changes`, `slack_reactions`, `slack_message_links`, and `slack_files`
- `knowledge_articles` and `knowledge_article_changes`
- `ingestion_runs`
- `knowledge_topics`, `knowledge_roles`, and article/topic/role mappings
- `knowledge_extraction_runs`
- `knowledge_requirements` and requirement/role mappings
- `knowledge_timing_rules`
- `knowledge_processes`, `knowledge_process_steps`, and process mappings
- `knowledge_resources`, `knowledge_contacts`, and article relationships

All timestamps are timezone-aware. `updated_at` is refreshed by a SQLAlchemy update event. Officer identity is unique across name, role, and academic year. Departmental representatives are distinguished in officer metadata; no email addresses are fabricated.

## Security and V1 limitations

- `.env` is ignored by Git; no service-role key is required.
- Scripts never print the connection string or password.
- PostgreSQL stores extracted text, metadata, and source paths only—not raw binary files.
- Source paths are treated as provenance metadata; future public frontends must never receive unrestricted database credentials.
- PDF extraction has no OCR, email attachment contents are not extracted, and classification is rule-based.
- Slack stores message/file metadata and source history only; it does not download file binaries or ingest direct messages.
- Search is PostgreSQL-backed and there is no vector search, transcript integration, or LLM extraction.

## Application profiles, external access, and RLS

Migration `0004_auth_profiles_and_rls` adds the production application-access layer used by the EFDS website:

- `profiles` maps a Supabase `auth.users.id` UUID to normalized email, membership type, application access role, optional officer link, active state, last login and metadata.
- `auth_access_exceptions` is an admin-only allowlist for explicitly approved identities, including temporary alternate authentication for Imperial-domain users when required. Rows are normalized, unique, active/expiry checked and never directly readable by ordinary members.
- RLS helper functions resolve the active profile role through `auth.uid()`. The role hierarchy is `viewer < member < committee < admin`.
- `is_external_email_eligible(text)` is a narrow boolean-style RPC for the pre-OTP website check. It accepts only an explicitly active, unexpired exception and does not return exception rows.
- `current_efds_external_access_role()` returns only the current authenticated identity's effective exception role for post-OTP provisioning.

The migration does not modify `auth.users` or Supabase Auth internals. The backend's direct `DATABASE_URL` connection remains the trusted ingestion writer and is not replaced by browser Supabase clients.

Review the migration before applying it locally:

```powershell
alembic upgrade head
```

The first admin is granted only after a profile exists from a successful login:

```powershell
python scripts/grant_access.py person@imperial.ac.uk --role admin
```

This command is idempotent, normalizes the email, validates optional officer IDs and never receives database credentials as arguments. It does not run against the remote database automatically.

Approved external identities are managed through the backend CLI as well:

```powershell
python scripts/set_access_exception.py adviser@example.org --role viewer --reason "EFDS adviser" --expires-at 2027-09-01T00:00:00Z
```

The command normalizes email, supports both `ic.ac.uk` and `imperial.ac.uk` addresses as well as external addresses, supports inactive/expired rows, and is idempotent. An exception with `admin` is intentionally not self-provisioned by the website; provision the initial profile with a non-admin role, then promote it through `grant_access.py`.

## Knowledge Operations V1

The source-first review lifecycle is:

```text
ICU source → crawler archive → knowledge_articles → deterministic extraction
→ proposed derived knowledge → admin review → approved internal knowledge
→ explicit committee/member/public publication
```

`knowledge_articles` remains authoritative external guidance. Derived rows are
reviewable interpretations, not replacements for ICU source content. Existing
`review_status` values are extended by constraint to `proposed`, `approved`,
`rejected`, `needs_review`, and `superseded`; `is_stale` remains the separate
source-version signal. A changed article marks old derived rows stale and
creates new versioned candidates without deleting the old interpretation.

Migrations `0005_knowledge_review_publication` and
`0006_transactional_knowledge_review` add `knowledge_review_events`,
profile-based reviewer/publication references, and `visibility` values of
`internal`, `committee`, `member`, and `public`. Review events record the
knowledge type and record ID, action, previous/new status, reviewer, reason,
and JSON changes. Approved/current knowledge is never automatically published.

All website review/publication writes use the database-owned
`review_knowledge_transaction` RPC. It resolves `auth.uid()` to an active
admin profile, locks the row, compares `review_version`, applies an allowlisted
interpretation/publication patch, and inserts exactly one audit event in the
same PostgreSQL transaction. A conflict or failure rolls back the row and
event together. Source identity, URL, content hash, evidence, extraction
metadata and raw ICU content are never writable by the RPC. Reviewer-added
process steps retain parent provenance and are marked in metadata.

The website admin console is the human-operated surface for review, stale
reconciliation, source inspection, registers, role views and publication. The
member view reads only approved, current rows explicitly published as `member`
or `public`; the public resources view reads the restricted
`public_knowledge_resources` view. No Slack, meeting, vector, or full RAG
integration is part of this milestone.

Apply locally, after reviewing the generated SQL:

```powershell
alembic upgrade head
```

Do not run this command automatically against production.

## Slack Institutional Memory V1

The Slack sync boundary is:

```text
Slack Web API → scripts/sync_slack.py → ingestion_runs + canonical Slack tables
                                      → admin-only website archive
```

Configure only the backend environment variable `SLACK_BOT_TOKEN`. The sync
validates the token with Slack, discovers channels without ingesting content,
and archives only channels explicitly enabled in `slack_channel_sync_settings`.
New channels default to disabled. Threads remain separate message rows;
edits and explicit deletions are append-only change events plus a current
message projection. Links are extracted and files are metadata-only. After a successful sync of the
enabled `meetings` channel, its Google Docs links are fetched into private, versioned meeting notes. See [docs/SLACK_INTEGRATION.md](docs/SLACK_INTEGRATION.md).

## Planned extensions

The next integrations may include PostgreSQL text-search refinement, Slack
decision/action extraction, and richer committee workflows. Fireflies/meeting
ingestion, embeddings, and full RAG remain explicitly out of scope for this
milestone.

## OneDrive Filesystem Institutional Memory V1

The generic `documents` table is retained for existing meeting references and
legacy ingestion, but OneDrive rows use `source_type = onedrive_filesystem`.
Their stable identity is the normalized relative path under `EFDS_FILES_ROOT`;
their byte/text version identity is `document_id + content_hash`. This keeps
same-content copies at different paths separate while making exact duplicates
queryable. `document_versions` and `document_source_changes` preserve edits,
renames/moves, missing/restored state, extraction status and provenance.

The synchronizer excludes the software repositories under `12_Technology`,
development artifacts, temporary Office files, local databases, credentials and
private-key patterns. Raw filesystem content is admin-only through RLS. See
[docs/FILESYSTEM_SYNC.md](docs/FILESYSTEM_SYNC.md) for reconciliation, watch,
OneDrive placeholder and future Graph compatibility behavior.

## Unified retrieval

Apply the latest Alembic migration and rebuild the derived PostgreSQL retrieval
index with `python scripts/rebuild_retrieval_index.py --full`. Website member
and admin search use the permission-filtered retrieval RPC. See
[docs/RETRIEVAL.md](docs/RETRIEVAL.md).

## Decisions, Actions & Operational Truth V1

Migration `0012_operational_truth` adds the reviewed operational layer for
decisions, action items, commitments, open questions and status updates. New
records are proposed, evidence is attached through retrieval-unit IDs, and
admin review/publication mutations are atomic with their audit events. The
operations layer never edits Slack, Meetily, OneDrive or ICU source rows. See
[docs/OPERATIONAL_TRUTH.md](docs/OPERATIONAL_TRUTH.md).

## Semantic and hybrid retrieval V1

Migration `0013_semantic_retrieval` adds versioned pgvector embeddings behind
the backend-only `OPENAI_API_KEY` provider boundary. The default privacy policy
embeds ICU and ICU-derived structured knowledge, approved operational records,
and OneDrive `01_governance` records; other document areas, Slack and Meetily
require explicit configuration. Use
`python scripts/embed_retrieval_units.py --dry-run --missing-only` and
`python scripts/report_embedding_coverage.py` before a real batch. Lexical
retrieval remains the safe fallback. See
[docs/EMBEDDINGS.md](docs/EMBEDDINGS.md).
