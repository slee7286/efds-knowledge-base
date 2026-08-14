# EFDS Retrieval Evaluation V2 Report

## Executive result

The current retrieval substrate is **not ready to certify EFDS Agent V1**. Semantic retrieval is the strongest tested strategy, but the frozen holdout achieved only `Recall@10 = 0.5263`. All seven holdout OneDrive queries failed because the current privacy policy has zero document embeddings. Slack, Meetily, and operational truth have no substantive live records to evaluate.

This is a genuine NO-GO for agent certification, not a reason to change labels or add another embedding model.

## Live corpus audit

| Retrieval source type | Current units | Embedded (`text-embedding-3-small`) | Benchmark status |
|---|---:|---:|---|
| `icu_article` | 543 | 543 | included |
| `knowledge_requirement` | 374 | 374 | included |
| `knowledge_resource` | 382 | 382 | included |
| `knowledge_process_step` | 101 | 101 | included where substantive |
| `knowledge_timing_rule` | 75 | 75 | included where substantive |
| `knowledge_contact` | 37 | 37 | included where substantive |
| `knowledge_process` | 23 | 23 | included |
| `document` | 1,137 | 0 | 20 governance/committee candidates included; semantic coverage unavailable |
| `slack_message` | 35 | 0 | excluded: 28 joins and 7 channel events/renames |
| `meeting_*` | 0 | 0 | no Meetily meetings |
| `operational_*` | 0 | 0 | no operational records |

The 1,137 document units include a large `00_inbox` mirror of ICU material. The benchmark uses only substantive `01_governance` and `03_committee` OneDrive records to avoid pretending that an ICU mirror is independent OneDrive evidence.

## Benchmark

Generated files:

- `evaluation/retrieval_queries_v2_candidates.json` — reviewable candidates with target previews and provenance.
- `evaluation/retrieval_queries_v2_reviewed.json` — frozen 59-query evaluator input.
- `evaluation/retrieval_queries_v2_dev.json` — 40-query development split.
- `evaluation/retrieval_queries_v2_holdout.json` — 19-query holdout split.
- `evaluation/RETRIEVAL_V2_REVIEW.md` — availability and review rules.

The split seed is `efds-retrieval-v2-split-2026-08-14`. Source-family and repeated-category coverage is enforced where at least two examples exist. Holdout labels were not changed after evaluation.

Primary source distribution:

| Source family | Queries |
|---|---:|
| ICU articles | 30 |
| Structured knowledge | 9 |
| OneDrive governance/committee documents | 20 |

There are 46 multi-target rows (78.0%). Most alternatives are duplicate article representations or chunks of the same logical document. Seven cross-source questions have primary ICU/structured evidence plus a related live governance/committee document. No Slack, Meetily, or operational questions were fabricated.

## Development comparison

| Strategy | Hit@5 | Hit@10 | MRR | Recall@10 |
|---|---:|---:|---:|---:|
| Weighted FTS | 0.0250 | 0.0250 | 0.0250 | 0.0250 |
| Semantic | 0.6000 | 0.6250 | 0.4196 | 0.6250 |
| Equal RRF | 0.3500 | 0.5500 | 0.2672 | 0.5500 |
| RRF, semantic weight 2 | 0.4250 | 0.5750 | 0.3582 | 0.5750 |
| Semantic-primary + exact rescue | 0.6000 | 0.6250 | 0.4196 | 0.6250 |
| Semantic-primary + parent cap 2 | 0.5250 | 0.5500 | 0.4058 | 0.5500 |

The selected configuration is semantic-primary with deterministic lexical rescue for exact acronyms, URLs, filenames, and exact-title signals. Generic RRF was not selected. Parent capping reduced recall on this corpus and was not selected.

## Holdout results

The holdout was run once after the development comparison with the selected semantic-primary/exact-rescue configuration.

| Method | Hit@1 | Hit@3 | Hit@5 | Hit@8 | Hit@10 | MRR | Recall@5 | Recall@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Weighted FTS | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Semantic | 0.3158 | 0.3684 | 0.4737 | 0.4737 | 0.5263 | 0.3761 | 0.4737 | 0.5263 |
| Semantic-primary + exact rescue | 0.3158 | 0.3684 | 0.4737 | 0.4737 | 0.5263 | 0.3761 | 0.4737 | 0.5263 |

### Holdout by source family

For the selected configuration:

- `document`: 0.0000 Hit@5 / 0.0000 Recall@10 — embeddings are intentionally absent under the current privacy policy.
- `icu_article`: 0.6250 Hit@5 / 0.7500 Recall@10.
- `knowledge_process`: 1.0000 Hit@5 / 1.0000 Recall@10.
- `knowledge_requirement`: 1.0000 Hit@5 / 1.0000 Recall@10.

### Holdout by category

- events: Hit@5 0.7143, Recall@10 0.7143
- finance: Hit@5 0.5000, Recall@10 0.5000
- governance: Hit@5 0.0000, Recall@10 0.0000
- identifier: Hit@5 0.0000, Recall@10 0.0000
- policy: Hit@5 0.0000, Recall@10 1.0000
- process: Hit@5 1.0000, Recall@10 1.0000
- sponsorship: Hit@5 0.0000, Recall@10 0.0000
- academics: Hit@5 0.0000, Recall@10 0.0000

Small category counts make these per-category values directional rather than stable estimates.

## Latency

The V2 evaluator separately measures the main components.

| Phase | Development median | Holdout median |
|---|---:|---:|
| Weighted lexical DB search | 25.010 ms | 35.895 ms |
| Query embedding | 162.575 ms | 207.640 ms |
| Semantic PostgreSQL search | 77.476 ms | 135.871 ms |
| End-to-end candidate retrieval | 305.239 ms | 413.278 ms |

Holdout p95 was 2,543.165 ms end-to-end, 314.429 ms for query embedding, and 850.855 ms for semantic database search. No query-result cache was added; an authorization-safe query-embedding cache remains optional.

## Failure taxonomy

The main failure classes are:

- **Missing embedding / privacy policy:** all document failures.
- **Representation mismatch:** ICU article versus requirement/resource/process representations.
- **Identifier/entity failure:** SUMS and similar exact system identifiers.
- **Ambiguous operational language:** broad governance, sponsorship, and planning questions.
- **Authority/context failure:** related controls or process steps outranking the selected article.
- **Source absence:** Slack, Meetily, and operational truth are not populated.

No additional embedding model, reranker, or canonical-ingestion change is justified by this run.

## Final retrieval contract

The production `hybrid` path now uses:

1. permission-scoped lexical retrieval;
2. permission-scoped semantic retrieval;
3. semantic results as the primary order;
4. lexical rescue only for high-confidence exact identifiers/title/URL/filename signals;
5. lexical fallback if the embedding provider is unavailable;
6. current-state and existing RLS restrictions unchanged.

`make_context_package()` exposes deterministic metadata: result count, source-family count, top score, rank gap, exact lexical match, and `retrieval_quality` (`low`, `limited`, or `normal`). Empty results are not replaced with irrelevant context.

## Recommendation

**NO-GO for EFDS Agent V1 certification.** The retrieval substrate is technically usable for a constrained ICU/structured-knowledge prototype, but system-wide agent readiness is not demonstrated. Before reconsidering GO:

1. decide and document whether OneDrive content may be embedded externally; if yes, opt it into embedding and re-evaluate;
2. ingest real operational truth and Meetily records;
3. synchronize substantive Slack content or explicitly exclude Slack from the first agent scope;
4. add reviewed source-balanced holdout queries;
5. improve exact identifier handling and representation diversification without tuning against holdout.
