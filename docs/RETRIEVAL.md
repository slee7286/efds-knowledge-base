# Unified retrieval

Canonical ICU, structured knowledge, OneDrive, Slack, and Meetily tables remain
independent. `retrieval_units` is a derived citation-ready index over them.

Each unit has a deterministic stable key made from source type, canonical
record, source version, and chunk number. Its UUID is a UUID5 of that key, so
rebuilds do not randomly re-identify unchanged content. Content hashes identify
indexed versions, never logical source files. Long text uses deterministic
paragraph/word-bounded chunks with modest overlap; Slack messages remain
individual units with channel/thread/permalink metadata.

PostgreSQL `simple` full-text search stores a weighted `tsvector` and GIN index.
Title fields receive weight A, source context B, and body C. Ranking combines
`ts_rank_cd`, exact title/substring boosts, explainable authority adjustments,
current-state preference, and a small Slack recency adjustment. `ts_headline`
returns compact snippets.

The `search_retrieval_units` SECURITY INVOKER RPC and RLS constrain access during
retrieval. Admins can search current admin sources. Members can search only
approved, current, non-stale member/public structured knowledge. Raw Slack and
OneDrive remain admin-only. Public search is limited to explicitly public
approved records. History is opt-in and admin-only.

Meetily transcript units are groups of timestamped segments with meeting and
artifact-version metadata. Meetily summary units are marked AI-generated and
rank below approved operational knowledge. All meeting retrieval types are
admin-only in V1; they are excluded from member, committee, and public access
at the database boundary.

Rebuild after source synchronization, extraction, review, publication, Slack,
or Meetily sync:

```powershell
python scripts/rebuild_retrieval_index.py --dry-run
python scripts/rebuild_retrieval_index.py --full
python scripts/rebuild_retrieval_index.py --source document
python scripts/rebuild_retrieval_index.py --source meeting_transcript
```

Evaluation uses reviewed, ID-based labels from the actual corpus:

```powershell
python scripts/evaluate_retrieval.py --generate-dataset evaluation/retrieval_queries.json
python scripts/evaluate_retrieval.py evaluation/retrieval_queries.json
```

The report compares ILIKE, basic FTS, and weighted FTS using Hit@1/3/5, MRR,
Recall@5, median/p95 latency, and Top-5 failure cases. Review generated labels
before treating them as a benchmark. The original generated 50-row dataset is
lexically easy because many queries are copied from indexed titles (and some
filesystem titles are hashes). `evaluation/retrieval_queries_hard.json` contains
34 paraphrased labels selected from actual corpus matches; its zero baseline
scores are useful evidence that lexical PostgreSQL search does not solve
vocabulary mismatch. Embeddings attach to `retrieval_units.id` and
`content_hash` in the separate versioned `retrieval_embeddings` table. Their
privacy eligibility is documented in `docs/EMBEDDING_PRIVACY_POLICY.md`.

Operational truth adds five controlled source types: `operational_decision`,
`operational_action`, `operational_commitment`, `operational_question`, and
`operational_status`. The index is derived from current operational records;
approved records receive the strongest operational authority adjustment and
candidate records remain admin-visible only. Evidence is attached separately
through `operational_record_evidence`, whose retrieval-unit IDs preserve the
source citation without copying or mutating canonical Slack, Meetily,
filesystem, or ICU content.

## Retrieval Evaluation V2

The V2 benchmark is source-aware and split into frozen development and holdout
files:

```powershell
python scripts/generate_retrieval_v2_benchmark.py
python scripts/split_retrieval_benchmark.py
python scripts/evaluate_retrieval_v2.py evaluation/retrieval_queries_v2_dev.json --phase dev --output evaluation/retrieval_v2_dev_results.json
python scripts/evaluate_retrieval_v2.py evaluation/retrieval_queries_v2_holdout.json --phase holdout --strategy semantic_primary_exact --output evaluation/retrieval_v2_holdout_results.json
```

The live corpus currently has no substantive Slack, Meetily, or operational
records. OneDrive governance records are eligible by default; other areas are
explicitly disabled unless configured. The coverage validator reports policy-
disabled or missing targets rather than treating them as semantic-model
failures.

The selected ranking behavior is semantic-primary with deterministic lexical
rescue for high-confidence acronyms, URLs, filenames, and exact-title signals.
Generic RRF was tested but reduced development and holdout quality. Results
are grouped by source and category, and parent-capping was rejected because it
reduced recall on the current corpus.

## Agent contract validation

Migration `0014_agent_retrieval_contract` defines the read-only
`search_retrieval_units_v1` PostgREST contract consumed by `efds-agent`. The
current migration wrapper delegates to the lexical `search_retrieval_units`
function and returns the versioned provenance/authority columns. It does not
itself create a query embedding or invoke `search_retrieval_units_semantic`.
Consequently, it must not be described as benchmark-parity semantic-primary
retrieval until the knowledge-base owner exposes a canonical endpoint/function
that performs query embedding, semantic search, and the selected exact rescue
as one owned path. Agent code must not recreate that ranking path.

The agent's read-only health probe and parity harness are documented in
`efds-agent/docs/AGENT_DOGFOOD_REPORT.md`. Applying the migration remains an
explicit operator action; no agent startup or validation command applies it.

`make_context_package()` preserves citation-ready results and adds only
deterministic diagnostics. Empty results are marked `retrieval_quality=low`;
one-result packages are `limited`; the system never pads a low-evidence query
with unrelated context.
