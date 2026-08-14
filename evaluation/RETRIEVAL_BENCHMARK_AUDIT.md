# EFDS Retrieval Benchmark Audit

## Versions

- `retrieval_queries.json`: original title-derived benchmark; preserved for historical comparison.
- `retrieval_queries_hard.json`: original paraphrased benchmark; preserved for audit.
- `retrieval_queries_hard_v2.json`: improved substantive-content seed; preserved unchanged.
- `retrieval_queries_hard_reviewed.json`: reviewed benchmark used for current evaluation.

The reviewed benchmark was created on 2026-08-14 from the 34-entry `hard_v2` seed. It contains 32 queries after removing two entries whose query/target pairing was not sufficiently reliable. The original seed files remain unchanged for audit/history.

## Audit method

Every target was checked against the live `retrieval_units` row for:

- source type and provenance;
- title and content length;
- whether the content directly answered the query;
- duplicate or alternative representations;
- placeholder, metadata-only, and Slack system-message content.

The label audit did not change canonical source data. Retrieval ranking was only adjusted later through the bounded semantic RRF-weight experiment documented below.

## Summary

| Outcome | Count |
|---|---:|
| `hard_v2` queries audited | 34 |
| Primary labels retained | 25 |
| Primary labels relabelled | 7 |
| Converted to multi-target acceptance | 19 |
| Removed | 2 |
| Final reviewed queries | 32 |

Converted-to-multi-target counts are reviewed rows with an `acceptable_expected_ids` list. These lists represent interchangeable evidence representations and are not a claim that every representation must be returned. The primary-label and multi-target counts are intentionally not mutually exclusive.

## Original label problems

Examples found in the historical hard dataset included:

- `2833fc32...`: `minibus test credit`, not a minibus-hire procedure.
- `ae82664c...`: duplicated `risk assessment` metadata.
- `b5b89170...`: duplicated `the room booking` metadata.
- `f9fa0a2a...`: Slack system message saying a user joined a channel.
- `2ded376a...`: `Sponsorships step by step -` with almost no substantive content.

The `hard_v2` generator correctly eliminated most of those cases, but its longest-content heuristic still produced collisions. For example, `committee member resources` matched a long `ROOM BOOKING COMPLAINTS` article because the phrase appeared in article metadata.

## Entry-by-entry decisions

| # | Query | `hard_v2` target | Decision | Reviewed action |
|---:|---|---|---|---|
| 1 | accepting money from a company | `SPONSORSHIP STEP BY STEP` | valid single | retained |
| 2 | invite someone from outside Imperial | `SPEAKER EVENTS PROCESS` | valid single | retained |
| 3 | money back for a personal purchase | event reimbursement requirement | valid multi, but refunds article is stronger | primary changed; requirement accepted as alternative |
| 4 | society apply for funding | `INTRODUCTION TO FUNDING` | valid multi | funding-support process made primary |
| 5 | reserving Union rooms | `ROOM BOOKING COMPLAINTS` | invalid label | changed to room-booking request requirement and accepted exact booking articles |
| 6 | alcohol at a student event | `ALCOHOL GUIDELINES` | valid single | retained |
| 7 | event safety paperwork | `CORE ACTIVITY RISK ASSESSMENTS` | valid single | retained |
| 8 | hire a minibus | `MINIBUS HIRE PROCESS` | valid single | retained |
| 9 | society submissions | `SUMS - DASHBOARD/SUBMISSIONS/APPLICATIONS` | valid single | retained |
| 10 | change committee constitution | `CLUB & SOCIETY CONSTITUTIONS` | valid single | retained |
| 11 | elections | `AUTUMN ONLINE ELECTIONS` | valid single | retained |
| 12 | treasurer spending | `SUSTAINABLE AND EFFECTIVE SPENDING` | valid single | retained |
| 13 | register an external coach | coach FAQ | valid single | retained |
| 14 | welfare issues at events | welfare concerns | valid single | retained |
| 15 | make activities accessible | accessibility article | valid multi due duplicate article units | primary retained; duplicate article units accepted |
| 16 | run a stall | stall article | valid single | retained |
| 17 | plan a boat party | boat-party article | valid single | retained |
| 18 | sponsorship-agreement rules | alcohol-sponsorship requirement | invalid semantic target | sponsorship-agreement requirement made primary; sponsorship article accepted |
| 19 | access society spaces | space-access article | valid single | retained |
| 20 | Union grant timeline | minibus requirement | invalid label | Trips Fund made primary; funding records accepted as alternatives |
| 21 | submit a funding application | funding-support article | valid multi | process made primary; article accepted |
| 22 | summer handover support | support-over-summer article | valid single | retained |
| 23 | frozen society accounts | minibus requirement | invalid label | frozen-account requirement made primary; article accepted |
| 24 | new committee members | room-booking article | ambiguous/mismatched | removed |
| 25 | organise a social | running-a-social article | valid single | retained |
| 26 | drinks-token guidance | drinks-token article | valid single | retained |
| 27 | submit event-calendar information | What's On calendar article | valid single | retained |
| 28 | compliance and regulation | PCI-DSS URL-only resource | ambiguous/weak target | removed |
| 29 | request event adjustments | accessibility article | valid multi | duplicate article units accepted |
| 30 | equipment hire | equipment-hire article | valid single | retained |
| 31 | CSP email account | CSP requirement | valid multi | email-account article accepted |
| 32 | after an incident | incident-reporting article | valid single | retained |
| 33 | sustainable spending | sustainable-spending article | valid single | retained |
| 34 | fundraising activity | substantive fundraising requirement | valid single after relabel | requirement retained as the primary target |

## Reviewed benchmark distribution

Primary source families:

| Source family | Queries |
|---|---:|
| ICU articles | 25 |
| Structured knowledge | 7 |

The reviewed set does not contain trustworthy Slack, OneDrive, operational-truth, or Meetily examples. The current corpus either lacks substantive examples for these query intents or the candidate labels pointed to metadata/system records. Those source families should receive separate reviewed queries rather than being forced into this set.

Primary query categories:

| Category | Queries |
|---|---:|
| events | 8 |
| finance | 7 |
| process | 7 |
| governance | 3 |
| policy | 3 |
| identifier | 2 |
| sponsorship | 2 |

## Leakage review

The reviewed queries are paraphrases and do not contain the expected UUIDs. The generator is used only to find candidate records; it does not pass expected labels into retrieval. The old title-derived generator remains available for historical comparison but is not used as evidence of semantic quality.

## Interpretation

Metrics from the reviewed set should be treated as the first trustworthy V1 comparison, but still as a small evaluation. The set is ICU-heavy and contains duplicate article representations, so future versions should add manually reviewed Slack, OneDrive, operational-truth, and Meetily queries when substantive records are available.

## Evaluation results

The following results were produced with:

```text
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py evaluation\retrieval_queries_hard_reviewed.json --repetitions 1
```

The benchmark has 32 queries. `Hit@K`, MRR, and `Recall@5` treat a reviewed acceptable alternative as a successful hit; direct multi-relevance labels, if added in a later benchmark version, will contribute fractional recall.

| Method | Hit@1 | Hit@3 | Hit@5 | MRR | Recall@5 | Median ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| ILIKE | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 69.374 | 81.373 |
| Basic FTS | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 17.623 | 21.421 |
| Weighted FTS | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 18.263 | 22.907 |
| Semantic | 0.5000 | 0.7188 | 0.8750 | 0.6411 | 0.8750 | 1050.621 | 1142.166 |
| Hybrid, semantic RRF weight 2.0 | 0.3750 | 0.5625 | 0.6250 | 0.4973 | 0.6250 | 1017.706 | 1092.386 |

The semantic and hybrid timings include query embedding. The current evaluator does not yet split provider latency from database vector-search latency. The lexical zero scores are expected for this deliberately vocabulary-mismatched set, not evidence that lexical retrieval is unusable for exact identifiers or title-derived queries.

### Source-family breakdown

| Source family | Method | Hit@1 | Hit@3 | Hit@5 | MRR | Recall@5 |
|---|---|---:|---:|---:|---:|---:|
| ICU article | Semantic | 0.56 | 0.72 | 0.88 | 0.6707 | 0.88 |
| ICU article | Hybrid | 0.36 | 0.56 | 0.60 | 0.4791 | 0.60 |
| Knowledge process | Semantic | 0.50 | 1.00 | 1.00 | 0.7500 | 1.00 |
| Knowledge process | Hybrid | 1.00 | 1.00 | 1.00 | 1.0000 | 1.00 |
| Knowledge requirement | Semantic | 0.20 | 0.60 | 0.80 | 0.4491 | 0.80 |
| Knowledge requirement | Hybrid | 0.20 | 0.40 | 0.60 | 0.3868 | 0.60 |

There are no trustworthy reviewed document, Slack, operational-truth, or Meetily rows in this benchmark; their absence is reported rather than hidden behind an aggregate score.

### Category breakdown

| Category | Semantic Hit@5 | Semantic MRR | Hybrid Hit@5 | Hybrid MRR |
|---|---:|---:|---:|---:|
| events | 0.8750 | 0.6869 | 0.6250 | 0.6613 |
| finance | 0.8571 | 0.5898 | 0.7143 | 0.3831 |
| governance | 1.0000 | 0.8333 | 0.6667 | 0.7037 |
| identifier | 0.5000 | 0.2727 | 0.5000 | 0.5172 |
| policy | 0.6667 | 0.4370 | 0.3333 | 0.4145 |
| process | 1.0000 | 0.7619 | 0.7143 | 0.3440 |
| sponsorship | 1.0000 | 0.6000 | 0.5000 | 0.5714 |

## Failure analysis

The latest run had four semantic Top-5 misses and twelve hybrid Top-5 misses. Seven of the twelve hybrid misses returned a directly relevant alternative representation that was not included in the accepted list (alcohol guidance, elections, boat-party planning, sponsorship, event-calendar submission, equipment hire, and fundraising). One of the four semantic misses overlaps this group. Two further cases are broad/ambiguous (grant timeline and welfare), leaving three clearer semantic/entity/ranking cases: SUMS, accessibility wording, and incident reporting.

The remaining clear failure patterns were:

- `SUMS` and `CSP` exact identifiers: semantic retrieval can lose to nearby governance/contact material; lexical exact-match handling must be preserved and separately tested.
- `Union grant timeline`: the query is broad and the corpus has several funding concepts; the Trips Fund target is defensible because it contains explicit deadlines, but it is not the only reasonable answer.
- accessibility and welfare: broad event-policy wording retrieves related event controls rather than one canonical article.
- incident reporting: related welfare/process records outrank the selected incident article.

The reviewed benchmark therefore distinguishes three cases: a bad original label, a valid alternative representation, and a genuine semantic/ranking miss. No production retrieval architecture change was made to force the selected UUIDs to the top.

## Hybrid decision

Semantic retrieval materially improves the reviewed paraphrase benchmark over all lexical baselines. Equal-weight RRF was weaker than semantic-only, so a bounded `semantic_weight=2.0` variant was tested after label review. It improved hybrid Hit@5 from 0.50 to 0.625, but still did not exceed semantic-only Hit@5 of 0.875. This is useful evidence, not a claim that hybrid is solved. A deterministic development/holdout split and additional exact-identifier rows are required before making the weight-2 setting the final default for agent-facing retrieval.
