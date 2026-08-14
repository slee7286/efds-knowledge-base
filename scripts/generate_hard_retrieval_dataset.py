"""Create paraphrased retrieval labels against the current indexed corpus.

The expected IDs are selected from real current retrieval units; the query text
is intentionally written in different language from the matching source term.
This is a reviewable seed set, not an automatic claim that the labels are gold.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from efds.db.session import session_scope


CASES = [
    ("What do we need to do before accepting money from a company?", "sponsorship"),
    ("Can we invite someone from outside Imperial to speak at our event?", "speaker"),
    ("How do I get money back for something I paid for personally?", "reimburse"),
    ("How can the society apply for funding?", "funding"),
    ("What is the process for reserving Union rooms?", "room booking"),
    ("When can alcohol be served at a student event?", "alcohol"),
    ("What safety paperwork is needed for an event?", "risk assessment"),
    ("How do we hire a minibus?", "minibus"),
    ("Where do officers manage society submissions?", "SUMS"),
    ("How is the committee constitution changed?", "constitution"),
    ("How do elections work?", "election"),
    ("What should a treasurer consider before spending?", "spending"),
    ("How do we register an external coach?", "coach"),
    ("What welfare issues must event organisers plan for?", "welfare"),
    ("How should we make activities accessible?", "accessibility"),
    ("What is needed to run a stall?", "stall"),
    ("How should we plan a boat party?", "boat party"),
    ("What are the rules for sponsorship agreements?", "sponsorship agreement"),
    ("How do we request access to society spaces?", "space access"),
    ("What is the Union grant timeline?", "grant"),
    ("How do we submit a funding application?", "funding application"),
    ("What support exists for summer handover?", "summer"),
    ("What happens when society accounts are frozen?", "frozen accounts"),
    ("What do new committee members need to know?", "committee member resources"),
    ("How should we organise a social?", "running a social"),
    ("What does the drinks token guidance say?", "drinks tokens"),
    ("How is event calendar information submitted?", "what's on calendar"),
    ("What is required for compliance and regulation?", "compliance"),
    ("Where can I find coach insurance guidance?", "coach insurance"),
    ("How can students request adjustments at events?", "accessibility"),
    ("What is the process for equipment hire?", "equipment hire"),
    ("How do I use the CSP email account?", "CSP email"),
    ("What should a society do after an incident?", "incident"),
    ("What does sustainable spending mean for a committee?", "sustainable"),
    ("How can a society run a fundraising activity?", "fundraising"),
]

MIN_MEANINGFUL_CONTENT_CHARS = 80


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, nargs="?", default=Path("evaluation/retrieval_queries_hard.json"))
    args = parser.parse_args()
    dataset = []
    with session_scope() as session:
        for query, term in CASES:
            rows = session.execute(text("""
            SELECT id, source_type FROM retrieval_units
            WHERE is_current AND NOT is_deleted
                AND length(trim(content)) >= :minimum_content_chars
                AND NOT (
                    source_type = 'slack_message'
                    AND (
                        content ILIKE '%has joined the channel%'
                        OR content ILIKE '%has left the channel%'
                        OR content ILIKE '%was added to the channel%'
                    )
                )
                AND (title ILIKE :pattern OR content ILIKE :pattern)
              ORDER BY
                CASE WHEN lower(title) = lower(:term) THEN 0 ELSE 1 END,
                CASE WHEN lower(title) LIKE lower(:exact) THEN 0 ELSE 1 END,
                length(content) DESC,
                id
              LIMIT 1
            """), {
                "pattern": f"%{term}%",
                "exact": f"%{term}%",
                "term": term,
                "minimum_content_chars": MIN_MEANINGFUL_CONTENT_CHARS,
            }).mappings().all()
            if not rows:
                continue
            row = rows[0]
            dataset.append({"query": query, "expected_retrieval_unit_ids": [str(row["id"])], "source_family": str(row["source_type"]), "category": "hard_paraphrase", "label_term": term})
    if len(dataset) < 30:
        raise SystemExit(f"Only found {len(dataset)} actual-corpus labels; review terms or corpus before using this dataset")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    print(f"Generated {len(dataset)} hard actual-corpus labels at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
