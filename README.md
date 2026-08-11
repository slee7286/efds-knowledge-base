# EFDS Platform — V1 ingestion foundation

This repository is the first EFDS (Economics, Finance & Data Science Society) institutional-memory layer. It stores structured records and extracted document text in PostgreSQL while OneDrive/SharePoint remains the canonical store for binary files.

V1 deliberately covers only the database and filesystem document foundation. It does not include Slack ingestion, Freshdesk crawling, embeddings, a website, or Fireflies integration.

## Architecture

- Python 3.12+ scripts and a small `src/efds` package
- SQLAlchemy 2.x with psycopg 3 for direct PostgreSQL access
- A standard `postgresql://` `DATABASE_URL` is automatically routed through the psycopg 3 SQLAlchemy driver
- Alembic for reproducible schema migrations
- Extracted text and provenance metadata in PostgreSQL; no binary uploads
- SHA-256 content hashes provide idempotent document ingestion
- `ingestion_runs` records progress, counters, and per-file errors
- ICU Freshdesk articles are synchronised from the existing `icu-crawler/data/` archive into `knowledge_articles`

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
- `meetings` and `meeting_attendees`
- `decisions` and `action_items`
- `slack_channels` and `slack_messages` (schema only)
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
- There is no full-text index, vector search, crawler, Slack API client, transcript integration, or web/admin application yet.

## Planned extensions

The next planned integrations are Slack Events API ingestion, Fireflies/meeting ingestion, a website/admin surface, PostgreSQL full-text search, and `pgvector` later if retrieval needs justify it. See [ARCHITECTURE.md](ARCHITECTURE.md) for the ICU boundary and lifecycle details.
