# EFDS semantic and hybrid retrieval

## Boundary

`retrieval_units` remain the canonical derived retrieval abstraction. Embeddings
are a replaceable index in `retrieval_embeddings`; they never replace source
records or provenance. Migration `0013_semantic_retrieval` enables PostgreSQL
pgvector and stores unbounded vectors so model dimensions can change without
changing canonical tables. V1 uses exact cosine-distance scans because the
corpus is small; an HNSW index can be evaluated later from measured corpus
size and latency.

## Provider

The provider interface is `EmbeddingProvider` with `embed_documents` and
`embed_query`. V1 implements OpenAI `text-embedding-3-small` through the
backend-only `OPENAI_API_KEY`. The SDK is not imported during ordinary backend
imports. Provider/model/version and vector dimension are stored per embedding.

Embedding input is deterministic and contains title, controlled source type,
area/topic/channel when available, and content. It excludes UUIDs, hashes,
absolute Windows paths, and secrets. `input_hash` prevents re-embedding when
the semantic input and model are unchanged.

## Privacy policy

The default policy embeds ICU articles and ICU-derived structured knowledge,
including proposed records because they are derived from the ICU source and
remain permission-filtered at retrieval time. Operational records must be
approved before embedding. 
OneDrive is now an explicit area-scoped policy rather than a globally
hard-coded exclusion. The conservative default allows only normalized area
`01_governance`; all other areas require an explicit
`EMBEDDING_DOCUMENT_AREAS` setting. Slack, Meetily transcripts/summaries, and
other raw internal sources remain excluded unless their source type and (for
Slack) stable channel IDs are explicitly enabled. This prevents sensitive
committee material leaving EFDS infrastructure accidentally. The policy also
excludes empty, stale, deleted and historical records. Unapproved operational
records remain excluded.

Example deliberate opt-in:

```powershell
$env:EMBEDDING_SOURCE_TYPES="icu_article,knowledge_requirement,knowledge_process,operational_decision,operational_action,slack_message,document,meeting_transcript,meeting_summary"
```

Review this policy before enabling raw internal sources. The backend key is
never sent to the website or browser. `EMBEDDING_DOCUMENT_AREAS` uses the
normalized `retrieval_units.source_area` value, not a Windows path. An
unknown area is denied.

The embedding input contains only the title, controlled source type,
area/topic/channel when available, and content. It excludes database IDs,
hashes, absolute paths, and provider secrets. A defence-in-depth filename
check rejects obvious credential/key/database records even if one reaches the
retrieval index.

## Commands

```powershell
alembic upgrade head
python scripts/rebuild_retrieval_index.py --full
python scripts/embed_retrieval_units.py --dry-run --missing-only
python scripts/embed_retrieval_units.py --source document --area 01_governance --dry-run --missing-only
python scripts/embed_retrieval_units.py --missing-only
python scripts/report_embedding_coverage.py
python scripts/validate_benchmark_embedding_coverage.py evaluation/retrieval_queries_v2_holdout.json
```

The command batches requests, retries only through the provider SDK boundary,
stores each successful batch independently, reports approximate tokens, and
refuses large unforced jobs. `--force` is required for broad re-embedding.
Provider failure does not affect canonical ingestion or lexical search.

## Retrieval

The Python retrieval service supports `lexical`, `semantic`, and `hybrid`.
Semantic SQL applies source filters, current/history rules, and access
predicates before returning candidates. Hybrid retrieves up to 100 candidates
from each engine and uses Reciprocal Rank Fusion with modest authority/current
adjustments. Exact identifiers continue to benefit from FTS.

The existing website directly calls Supabase and therefore does not receive
the backend embedding key. Its current route remains lexical until a deployed
backend retrieval HTTP boundary is configured; the backend service is ready
for that boundary and falls back to lexical when embeddings are unavailable.

## Cost and migration

The embedding command reports approximate token volume from actual corpus text,
per-area eligibility, and (when configured) an estimated cost. Set
`EMBEDDING_COST_PER_MILLION_TOKENS_USD` only after confirming current provider
pricing; the system does not invent a cost when it is unset. A future
model is added as another provider/model/version, evaluated, and switched by
configuration without changing `retrieval_units`.
