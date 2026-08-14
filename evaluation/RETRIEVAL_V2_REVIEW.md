# Retrieval Evaluation V2 Review

This report records the live-corpus availability and candidate-generation review boundary.

## Availability

- ICU articles and structured ICU knowledge: substantive and benchmarkable.
- OneDrive: substantive governance and committee material exists; only those areas are included to avoid treating the ICU mirror in `00_inbox` as independent OneDrive evidence.
- Slack: 35 current units, all channel join/rename system noise; excluded.
- Meetily: zero meetings/artifacts; excluded.
- Operational truth: zero records; excluded.

Document candidates skipped because no live substantive match was found: 0.

## Candidate distribution

- Total candidate/reviewed rows: 59
- Source families: {'document': 20, 'icu_article': 30, 'knowledge_process': 3, 'knowledge_requirement': 6}
- Categories: {'academics': 1, 'competitions': 1, 'events': 14, 'finance': 14, 'governance': 9, 'identifier': 2, 'meetings': 1, 'policy': 4, 'process': 7, 'sponsorship': 5, 'welfare': 1}
- Multi-target rows: 46

Every candidate includes target details in `retrieval_queries_v2_candidates.json`. The clean reviewed file omits previews and reasons so it remains an evaluator input rather than a corpus dump.

## Review rules

1. A target must be current, non-deleted, non-stale, and have meaningful content.
2. Hash-like document titles, channel system events, and metadata-only records are not benchmark targets.
3. Chunks from the same logical document are acceptable alternatives when the query is document-level.
4. A supporting record is retained for provenance but does not count as a full hit unless placed in `acceptable_expected_ids`.
5. Empty source families are reported, never represented by fabricated queries.
