# EFDS Embedding Privacy Policy

## Purpose

Embeddings are a derived retrieval index over `retrieval_units`. They are not
canonical EFDS records and do not replace PostgreSQL authorization or source
provenance.

The current provider boundary is the backend-only OpenAI embedding adapter.
No browser or website code receives the API key. This document records policy
and configuration; it does not make claims about provider retention or legal
compliance that have not been independently verified.

## Default policy

The default is deny-by-default for raw internal sources. ICU articles and
structured ICU knowledge remain enabled as before. Approved operational records
remain eligible. OneDrive documents are enabled only for normalized area
`01_governance` by default. Unknown, missing, or newly introduced areas are
denied.

Slack and Meetily are not enabled by default. Ingestion permission and external
embedding permission are separate decisions. A Slack channel must be listed by
stable channel ID before its messages can be embedded. Meeting transcript and
summary source types must be explicitly added to the source-type allowlist.

## Configuration

The backend reads:

* `EMBEDDING_SOURCE_TYPES`: explicit source-type allowlist. If empty, the
  conservative built-in ICU/structured/approved-operational set is used.
* `EMBEDDING_DOCUMENT_AREAS`: comma-separated normalized `source_area` values.
  If empty, only `01_governance` is enabled.
* `EMBEDDING_SLACK_CHANNEL_IDS`: stable Slack channel IDs. If empty, Slack is
  denied even if `slack_message` is added to the source-type allowlist.
* `EMBEDDING_COST_PER_MILLION_TOKENS_USD`: optional local estimate input; it is
  not a provider pricing claim.

Examples:

```powershell
$env:EMBEDDING_SOURCE_TYPES="icu_article,knowledge_requirement,knowledge_resource,document"
$env:EMBEDDING_DOCUMENT_AREAS="01_governance"
```

To stage another reviewed area, add it explicitly and run a dry-run first:

```powershell
$env:EMBEDDING_DOCUMENT_AREAS="01_governance,03_committee"
python scripts/embed_retrieval_units.py --source document --area 03_committee --dry-run --missing-only
```

## Data transmitted

The deterministic embedding input contains the retrieval-unit title,
controlled source type, source area/topic/channel when available, and content.
It does not contain database UUIDs, content hashes, absolute Windows paths, or
API keys. Content itself can still be sensitive, which is why eligibility is
checked before a provider request.

Obvious credential/key/database filenames and empty content are rejected as
defence in depth. Canonical ingestion is unaffected by this policy.

## Coverage and failure behavior

`report_embedding_coverage.py` reports eligible, embedded, missing, outdated,
and policy-ineligible units by area. `embed_retrieval_units.py --dry-run`
reports the same policy effect plus approximate tokens and an optional cost
estimate. Provider failure leaves canonical ingestion and lexical retrieval
available; it only leaves semantic vectors missing or outdated.

Benchmark validation marks targets as `SOURCE_NOT_EMBEDDED_BY_POLICY`,
`SOURCE_NOT_EMBEDDED`, `SOURCE_NOT_AVAILABLE`, or `SOURCE_EMPTY` instead of
silently calling them semantic-model failures.
