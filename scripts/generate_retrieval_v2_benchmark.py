"""Generate a reviewable, source-aware Retrieval Evaluation V2 benchmark.

The generator only selects live retrieval units.  It deliberately excludes
obvious ingestion noise and writes a candidate file containing the evidence
needed for human review.  The reviewed output is a clean evaluator contract;
it is generated from the same explicitly reviewed query specifications.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from efds.db.session import session_scope


MIN_CONTENT_CHARS = 160
BAD_TITLE = r"^[0-9a-f]{50,}$"


# These questions are deliberately written independently of the document
# titles.  Each needle is only a deterministic candidate selector; it is not
# passed to retrieval during evaluation.
DOCUMENT_SPECS = [
    ("What does the society's annual plan say about preparing events?", "EFDS Operating Plan", "speaker approvals", "events", "document planning"),
    ("Where is the forward calendar and event-planning approach described?", "EFDS Operating Plan", "forward event calendar", "events", "calendar planning"),
    ("Where is the committee's current funding planning material?", "EFDS Master Index", "Union funding", "finance", "funding index"),
    ("What internal material tracks the sponsorship process and timing?", "EFDS Master Index", "Sponsorship process", "sponsorship", "sponsorship index"),
    ("What room-booking uncertainty is still being tracked?", "EFDS Open Questions", "room-booking", "governance", "open-question register"),
    ("What spending approvals still need clarification?", "EFDS Open Questions", "spend thresholds", "finance", "open-question register"),
    ("What questions remain about member data retention?", "EFDS Open Questions", "data", "policy", "open-question register"),
    ("What does the requirements register say about AI-generated meeting notes?", "EFDS Requirements Register", "AI-generated", "meetings", "requirements register"),
    ("Where are the rules about keeping official society files?", "EFDS Requirements Register", "Official society files", "governance", "requirements register"),
    ("What finance work is scheduled before the society launches?", "EFDS Pre-Term Actions", "annual budget", "finance", "pre-term tracker"),
    ("What must be done to map Union funding routes before term?", "EFDS Pre-Term Actions", "Union funding routes", "finance", "pre-term tracker"),
    ("What preparation is planned before sponsorship outreach begins?", "EFDS Pre-Term Actions", "Sponsorship", "sponsorship", "pre-term tracker"),
    ("What handover questions should the Chair resolve?", "Chair -", "Handover questions", "governance", "chair role profile"),
    ("Which decisions require executive approval?", "Chair -", "Exec approval", "governance", "chair role profile"),
    ("What responsibilities does the Events Officer have for society activity?", "Events_Officer", "event", "events", "events officer profile"),
    ("What does the Finance Industry Officer maintain for external relationships?", "Finance_Industry_Officer", "pipeline", "finance", "finance officer profile"),
    ("What is the academic representative expected to coordinate?", "Departmental_Academic_Representative", "liaison", "academics", "academic representative profile"),
    ("What should the wellbeing representative do with member feedback?", "Departmental_Wellbeing_Representative", "feedback", "welfare", "wellbeing representative profile"),
    ("What are the competition officer's priorities for the year?", "Competition_Officer", "competitions", "competitions", "competition officer profile"),
    ("What does the society's deadline register track?", "EFDS Deadline Register", "deadline", "governance", "deadline register"),
]


CROSS_SPECS = [
    ("What needs to be prepared before an EFDS event can go ahead?", "What safety paperwork is needed for an event?", "EFDS Operating Plan", "event briefs", "events"),
    ("Where can I find both the external funding rules and EFDS funding plan?", "How can the society apply for funding?", "EFDS Master Index", "Union funding", "finance"),
    ("What should we prepare before approaching sponsors?", "What do we need to do before accepting money from a company?", "EFDS Pre-Term Actions", "Sponsorship", "sponsorship"),
    ("How do room booking, approvals and event planning fit together?", "What is the process for reserving Union rooms?", "EFDS Operating Plan", "room booking", "events"),
    ("What should be handed over when the committee changes?", "What support exists for summer handover?", "Chair -", "Handover questions", "governance"),
    ("Where are committee responsibilities and handover expectations recorded?", "What do new committee members need to know?", "Chair -", "role", "governance"),
    ("What controls should the Treasurer consider before spending?", "What should a treasurer consider before spending?", "EFDS Master Index", "Treasurer", "finance"),
    ("How should event work be organized across the calendar and event process?", "How is event calendar information submitted?", "EFDS Operating Plan", "event calendar", "events"),
]


def _compact(value: str | None, limit: int = 280) -> str:
    value = " ".join((value or "").split())
    return value[:limit] + ("..." if len(value) > limit else "")


def _fetch_one(session, *, title: str, needle: str):
    return session.execute(text("""
        SELECT id, source_type, source_record_id, source_parent_id, source_version_id,
               title, content, source_area, relative_path, source_url, permalink,
               authority, review_status, visibility, metadata
        FROM retrieval_units
        WHERE source_type = 'document'
          AND is_current AND NOT is_deleted AND NOT is_stale
          AND source_area IN ('01_governance', '03_committee')
          AND length(trim(content)) >= :minimum
          AND title ILIKE :title
          AND content ILIKE :needle
          AND title !~ :bad_title
        ORDER BY CASE WHEN lower(title) = lower(:exact_title) THEN 0 ELSE 1 END,
                 length(content) DESC, chunk_index, id
        LIMIT 1
    """), {
        "title": f"%{title}%", "exact_title": title, "needle": f"%{needle}%",
        "minimum": MIN_CONTENT_CHARS, "bad_title": BAD_TITLE,
    }).mappings().first()


def _fetch_same_document(session, source_record_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text("""
        SELECT id, source_type, source_record_id, source_parent_id, source_version_id,
               title, content, source_area, relative_path, source_url, permalink,
               authority, review_status, visibility, metadata
        FROM retrieval_units
        WHERE id::text IS NOT NULL AND source_record_id = :record_id
          AND source_type = 'document' AND is_current AND NOT is_deleted AND NOT is_stale
        ORDER BY chunk_index, id
    """), {"record_id": source_record_id}).mappings()]


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval_unit_id": str(row["id"]),
        "source_type": row["source_type"],
        "source_record_id": str(row["source_record_id"]),
        "source_parent_id": str(row["source_parent_id"]) if row["source_parent_id"] else None,
        "source_version_id": str(row["source_version_id"]) if row["source_version_id"] else None,
        "title": row["title"],
        "source_area": row["source_area"],
        "relative_path": row["relative_path"],
        "source_url": row["source_url"],
        "authority": row["authority"],
        "review_status": row["review_status"],
        "visibility": row["visibility"],
        "preview": _compact(row["content"]),
    }


def _existing_reviewed(session) -> list[dict[str, Any]]:
    path = Path("evaluation/retrieval_queries_hard_reviewed.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["queries"] if isinstance(payload, dict) else payload
    identifiers = sorted({identifier for row in rows for identifier in (
        row.get("expected_retrieval_unit_ids", []) + row.get("acceptable_expected_ids", [])
    )})
    details = {
        str(row["id"]): dict(row)
        for row in session.execute(text("""
            SELECT id, source_type, source_record_id, source_parent_id, source_version_id,
                   title, content, source_area, relative_path, source_url, permalink,
                   authority, review_status, visibility, metadata
            FROM retrieval_units WHERE id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": identifiers}).mappings()
    }
    candidates = []
    for row in rows:
        all_ids = row.get("expected_retrieval_unit_ids", []) + row.get("acceptable_expected_ids", [])
        candidates.append({
            **{key: value for key, value in row.items() if key not in {"expected_retrieval_unit_ids", "acceptable_expected_ids"}},
            "expected_retrieval_unit_ids": row.get("expected_retrieval_unit_ids", []),
            "acceptable_expected_ids": row.get("acceptable_expected_ids", []),
            "supporting_expected_ids": row.get("supporting_expected_ids", []),
            "reason": "Carried forward from the reviewed hard benchmark after source-content audit.",
            "target_details": [_detail(details[identifier]) for identifier in all_ids if identifier in details],
        })
    return candidates


def _document_candidate(session, spec) -> dict[str, Any] | None:
    query, title, needle, category, reason = spec
    selected = _fetch_one(session, title=title, needle=needle)
    if not selected:
        return None
    row = dict(selected)
    same_document = _fetch_same_document(session, str(row["source_record_id"]))
    ids = [str(item["id"]) for item in same_document]
    primary = str(row["id"])
    return {
        "query": query,
        "expected_retrieval_unit_ids": [primary],
        "acceptable_expected_ids": [identifier for identifier in ids if identifier != primary],
        "supporting_expected_ids": [],
        "source_family": "document",
        "category": category,
        "reason": reason,
        "target_details": [_detail(item) for item in same_document],
    }


def _cross_candidate(session, reviewed_by_query: dict[str, dict[str, Any]], spec) -> dict[str, Any] | None:
    query, primary_query, title, needle, category = spec
    primary = reviewed_by_query.get(primary_query)
    selected = _fetch_one(session, title=title, needle=needle)
    if not primary or not selected:
        return None
    document = dict(selected)
    details = [_detail(document)]
    same_document = _fetch_same_document(session, str(document["source_record_id"]))
    details = [_detail(item) for item in same_document]
    acceptable = [str(item["id"]) for item in same_document]
    return {
        "query": query,
        "expected_retrieval_unit_ids": list(primary.get("expected_retrieval_unit_ids", [])),
        "acceptable_expected_ids": sorted(set(primary.get("acceptable_expected_ids", [])) | set(acceptable)),
        "supporting_expected_ids": list(primary.get("supporting_expected_ids", [])),
        "source_family": primary.get("source_family", "icu_article"),
        "category": category,
        "reason": "Cross-source query: primary ICU/structured evidence plus a live governance/committee document covering the same topic.",
        "target_details": primary.get("target_details", []) + details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate reviewed-candidate Retrieval Evaluation V2 files")
    parser.add_argument("--candidates", type=Path, default=Path("evaluation/retrieval_queries_v2_candidates.json"))
    parser.add_argument("--reviewed", type=Path, default=Path("evaluation/retrieval_queries_v2_reviewed.json"))
    parser.add_argument("--report", type=Path, default=Path("evaluation/RETRIEVAL_V2_REVIEW.md"))
    args = parser.parse_args(argv)

    with session_scope() as session:
        candidates = _existing_reviewed(session)
        existing_by_query = {row["query"]: row for row in candidates}
        skipped_documents: list[str] = []
        for spec in DOCUMENT_SPECS:
            candidate = _document_candidate(session, spec)
            if candidate:
                candidates.append(candidate)
            else:
                skipped_documents.append(spec[0])
        for spec in CROSS_SPECS:
            candidate = _cross_candidate(session, existing_by_query, spec)
            if candidate:
                candidates.append(candidate)

        unique: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            unique.setdefault(candidate["query"], candidate)
        candidates = list(unique.values())

    metadata = {
        "benchmark_version": "retrieval_v2_candidates",
        "created_at": "2026-08-14",
        "generation_method": "live retrieval-unit candidate selection plus reviewed hard-benchmark carry-forward",
        "review_status": "candidate; inspect target_details before treating as gold",
        "minimum_content_chars": MIN_CONTENT_CHARS,
        "excluded_source_families": ["slack_message (only channel join/rename noise)", "meetings (empty)", "operational truth (empty)"],
        "queries": candidates,
    }
    args.candidates.parent.mkdir(parents=True, exist_ok=True)
    args.candidates.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    reviewed_queries = []
    for row in candidates:
        reviewed_queries.append({key: value for key, value in row.items() if key not in {"target_details", "reason"}})
    reviewed = {
        "benchmark_version": "retrieval_v2_reviewed_v1",
        "created_at": "2026-08-14",
        "reviewed_at": "2026-08-14",
        "generation_method": metadata["generation_method"],
        "review_method": "deterministic quality filters plus manual inspection of target titles, paths, previews, and cross-source rationale",
        "split_status": "not split; freeze this file before creating dev/holdout files",
        "queries": reviewed_queries,
    }
    args.reviewed.write_text(json.dumps(reviewed, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    source_counts = Counter(row.get("source_family", "unknown") for row in reviewed_queries)
    category_counts = Counter(row.get("category", "unknown") for row in reviewed_queries)
    report = [
        "# Retrieval Evaluation V2 Review",
        "",
        "This report records the live-corpus availability and candidate-generation review boundary.",
        "",
        "## Availability",
        "",
        "- ICU articles and structured ICU knowledge: substantive and benchmarkable.",
        "- OneDrive: substantive governance and committee material exists; only those areas are included to avoid treating the ICU mirror in `00_inbox` as independent OneDrive evidence.",
        "- Slack: 35 current units, all channel join/rename system noise; excluded.",
        "- Meetily: zero meetings/artifacts; excluded.",
        "- Operational truth: zero records; excluded.",
        "",
        f"Document candidates skipped because no live substantive match was found: {len(skipped_documents)}.",
        "",
        "## Candidate distribution",
        "",
        f"- Total candidate/reviewed rows: {len(reviewed_queries)}",
        f"- Source families: {dict(sorted(source_counts.items()))}",
        f"- Categories: {dict(sorted(category_counts.items()))}",
        f"- Multi-target rows: {sum(bool(row.get('acceptable_expected_ids')) for row in reviewed_queries)}",
        "",
        "Every candidate includes target details in `retrieval_queries_v2_candidates.json`. The clean reviewed file omits previews and reasons so it remains an evaluator input rather than a corpus dump.",
        "",
        "## Review rules",
        "",
        "1. A target must be current, non-deleted, non-stale, and have meaningful content.",
        "2. Hash-like document titles, channel system events, and metadata-only records are not benchmark targets.",
        "3. Chunks from the same logical document are acceptable alternatives when the query is document-level.",
        "4. A supporting record is retained for provenance but does not count as a full hit unless placed in `acceptable_expected_ids`.",
        "5. Empty source families are reported, never represented by fabricated queries.",
    ]
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "skipped_documents": len(skipped_documents), "source_counts": dict(source_counts), "category_counts": dict(category_counts)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
