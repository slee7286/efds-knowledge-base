"""Evaluate unified retrieval against reviewed labels from the actual corpus.

Dataset rows are intentionally ID-based so labels cannot refer to invented
source names. Generate a starting set from the current index, then review the
queries and add additional vocabulary where appropriate.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from efds.db.session import session_scope
from efds.retrieval.service import search_retrieval
from efds.retrieval.types import RetrievalFilters


def _accepted_ids(row: dict[str, Any]) -> set[str]:
    """Return IDs that satisfy a hit/MRR assertion for a benchmark row."""

    ids = set(row.get("expected_retrieval_unit_ids", []))
    ids.update(row.get("primary_expected_ids", []))
    ids.update(row.get("acceptable_expected_ids", []))
    return {str(value) for value in ids}


def _relevant_ids(row: dict[str, Any]) -> set[str]:
    """Return directly relevant IDs used as the Recall@5 denominator.

    ``acceptable_expected_ids`` are alternatives, not a requirement to return
    every duplicate representation. If no primary list is supplied, the
    historical expected list remains the directly relevant set.
    """

    ids = row.get("primary_expected_ids") or row.get("expected_retrieval_unit_ids", [])
    return {str(value) for value in ids}


def _metrics(rows: list[dict[str, Any]], ranked: dict[str, list[str]]) -> dict[str, float]:
    values = {key: [] for key in ("hit_at_1", "hit_at_3", "hit_at_5", "mrr", "recall_at_5")}
    for row in rows:
        accepted = _accepted_ids(row)
        relevant = _relevant_ids(row)
        result_ids = list(dict.fromkeys(ranked[row["query"]]))
        positions = [index + 1 for index, value in enumerate(result_ids) if value in accepted]
        values["hit_at_1"].append(float(bool(positions and positions[0] <= 1)))
        values["hit_at_3"].append(float(bool(positions and positions[0] <= 3)))
        values["hit_at_5"].append(float(bool(positions and positions[0] <= 5)))
        values["mrr"].append(1.0 / positions[0] if positions else 0.0)
        if row.get("acceptable_expected_ids"):
            # Alternatives represent interchangeable valid evidence, not a
            # requirement to retrieve every duplicate representation.
            recall = float(bool(set(result_ids[:5]) & accepted))
        else:
            recall = len(set(result_ids[:5]) & relevant) / len(relevant) if relevant else 0.0
        values["recall_at_5"].append(recall)
    return {key: round(statistics.mean(value), 4) if value else 0.0 for key, value in values.items()}


def _breakdown(rows: list[dict[str, Any]], ranked: dict[str, list[str]], field: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(field) or "unknown"), []).append(row)
    return {name: _metrics(group, ranked) for name, group in sorted(groups.items())}


def _load_dataset(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict) and isinstance(payload.get("queries"), list):
        return payload["queries"], {key: value for key, value in payload.items() if key != "queries"}
    raise ValueError("benchmark must be a JSON list or an object containing a queries list")


def _validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Return structural benchmark errors before any retrieval is executed."""

    errors: list[str] = []
    seen_queries: set[str] = set()
    for index, row in enumerate(rows, start=1):
        query = str(row.get("query", "")).strip()
        if not query:
            errors.append(f"row {index}: query is empty")
        elif query.casefold() in seen_queries:
            errors.append(f"row {index}: duplicate query {query!r}")
        else:
            seen_queries.add(query.casefold())
        expected = row.get("expected_retrieval_unit_ids", row.get("primary_expected_ids", []))
        acceptable = row.get("acceptable_expected_ids", [])
        if not isinstance(expected, list) or not expected:
            errors.append(f"row {index}: at least one expected retrieval-unit UUID is required")
        if not isinstance(acceptable, list):
            errors.append(f"row {index}: acceptable_expected_ids must be a list")
        identifiers = list(expected) if isinstance(expected, list) else []
        identifiers += list(acceptable) if isinstance(acceptable, list) else []
        if len({str(value) for value in identifiers}) != len(identifiers):
            errors.append(f"row {index}: duplicate expected/acceptable retrieval-unit ID")
        for value in identifiers:
            try:
                uuid.UUID(str(value))
            except (ValueError, AttributeError, TypeError):
                errors.append(f"row {index}: invalid retrieval-unit UUID {value!r}")
    return errors


def _details(session: Any, identifiers: list[str]) -> dict[str, dict[str, Any]]:
    if not identifiers:
        return {}
    rows = session.execute(text("""
      SELECT id, source_type, title, left(regexp_replace(content, :newlines, :space, :flags), 220) AS preview
      FROM retrieval_units
      WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": identifiers, "newlines": r"[\r\n]+", "space": " ", "flags": "g"}).mappings()
    details: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = dict(row)
        value["id"] = str(value["id"])
        details[value["id"]] = value
    return details


def _like(session, query: str) -> list[str]:
    rows = session.execute(text("""
      SELECT id FROM retrieval_units
      WHERE is_current AND NOT is_deleted AND (title ILIKE :pattern OR content ILIKE :pattern)
      ORDER BY CASE WHEN lower(title) = lower(:query) THEN 0 ELSE 1 END, updated_at DESC, id
      LIMIT 50
    """), {"pattern": f"%{query}%", "query": query}).scalars()
    return [str(value) for value in rows]


def _basic_fts(session, query: str) -> list[str]:
    rows = session.execute(text("""
      SELECT id FROM retrieval_units
      WHERE is_current AND NOT is_deleted AND search_vector @@ websearch_to_tsquery('simple', :query)
      ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query), 32) DESC, id
      LIMIT 50
    """), {"query": query}).scalars()
    return [str(value) for value in rows]


def _weighted(session, query: str) -> list[str]:
    return [result.retrieval_unit_id for result in search_retrieval(session, query, scope="admin", limit=50, mode="lexical")]


def _semantic(session, query: str) -> list[str]:
    return [result.retrieval_unit_id for result in search_retrieval(session, query, scope="admin", limit=50, mode="semantic")]


def _hybrid(session, query: str) -> list[str]:
    return [result.retrieval_unit_id for result in search_retrieval(session, query, scope="admin", limit=50, mode="hybrid")]


def _generate(session, output: Path, minimum: int) -> None:
    rows = session.execute(text("""
      SELECT id, title, source_type FROM retrieval_units
      WHERE is_current AND NOT is_deleted AND length(trim(title)) >= 3
      ORDER BY source_type, title, id LIMIT 500
    """)).mappings()
    dataset: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        query = str(row["title"]).strip()
        if query.lower() in seen:
            continue
        seen.add(query.lower())
        dataset.append({"query": query, "expected_retrieval_unit_ids": [str(row["id"])], "source_family": row["source_type"], "category": "title"})
        if len(dataset) >= minimum:
            break
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    print(f"Generated {len(dataset)} actual-corpus evaluation labels at {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate EFDS PostgreSQL retrieval")
    parser.add_argument("dataset", type=Path, nargs="?", help="Reviewed JSON evaluation dataset")
    parser.add_argument("--generate-dataset", type=Path, help="Generate title-based labels from current indexed records")
    parser.add_argument("--minimum", type=int, default=50)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        with session_scope() as session:
            if args.generate_dataset:
                _generate(session, args.generate_dataset, args.minimum)
                if not args.dataset:
                    return 0
            if not args.dataset:
                parser.error("provide DATASET or --generate-dataset")
            rows, benchmark_metadata = _load_dataset(args.dataset)
            validation_errors = _validate_rows(rows)
            if validation_errors:
                raise ValueError("invalid benchmark: " + "; ".join(validation_errors[:8]))
            methods = {"ilike": _like, "fts": _basic_fts, "weighted_fts": _weighted, "semantic": _semantic, "hybrid": _hybrid}
            report: dict[str, Any] = {"query_count": len(rows), "benchmark_metadata": benchmark_metadata, "methods": {}}
            for name, method in methods.items():
                ranked: dict[str, list[str]] = {}
                durations: list[float] = []
                try:
                    for row in rows:
                        start = time.perf_counter()
                        result: list[str] = []
                        for _ in range(max(args.repetitions, 1)):
                            result = method(session, row["query"])
                        durations.append((time.perf_counter() - start) / max(args.repetitions, 1) * 1000)
                        ranked[row["query"]] = result
                except Exception as error:
                    report["methods"][name] = {"status": "unavailable", "reason": str(error)}
                    continue
                metrics = _metrics(rows, ranked)
                report["methods"][name] = {"metrics": metrics, "by_source_family": _breakdown(rows, ranked, "source_family"), "by_category": _breakdown(rows, ranked, "category"), "median_ms": round(statistics.median(durations), 3), "p95_ms": round(sorted(durations)[max(0, int(len(durations) * .95) - 1)], 3)}
                if name in {"weighted_fts", "semantic", "hybrid"}:
                    failures = []
                    for row in rows:
                        top_ids = list(dict.fromkeys(ranked[row["query"]][:5]))
                        if _accepted_ids(row) & set(top_ids):
                            continue
                        target_details = _details(session, list(_accepted_ids(row)))
                        returned_details = _details(session, top_ids)
                        failures.append({
                            "query": row["query"],
                            "category": row.get("category"),
                            "source_family": row.get("source_family"),
                            "expected": sorted(_relevant_ids(row)),
                            "acceptable": sorted(_accepted_ids(row) - _relevant_ids(row)),
                            "expected_details": [target_details[value] for value in sorted(_accepted_ids(row)) if value in target_details],
                            "top_5": [{"rank": index + 1, **returned_details[value]} for index, value in enumerate(top_ids) if value in returned_details],
                        })
                    report.setdefault("failures", {})[name] = failures
            print(json.dumps(report, indent=2))
    except Exception as error:
        print(f"Retrieval evaluation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
