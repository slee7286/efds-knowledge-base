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
