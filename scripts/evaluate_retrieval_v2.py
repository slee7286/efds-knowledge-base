"""Evaluate bounded retrieval strategies on the frozen V2 benchmark.

This evaluator computes lexical and semantic candidates once per query, then
compares a small, explicit set of fusion strategies.  Development runs may
compare strategies; a holdout run requires one frozen strategy name.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from efds.db.session import session_scope
from efds.config import get_settings
from efds.retrieval.embeddings import embedding_input_hash, get_embedding_provider, is_embedding_eligible
from efds.retrieval.service import (
    _fuse, _search_lexical, _search_semantic,
)
from efds.retrieval.types import RetrievalFilters, RetrievalResult


def _base_evaluator():
    path = Path(__file__).with_name("evaluate_retrieval.py")
    spec = importlib.util.spec_from_file_location("evaluate_retrieval_base", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load the base evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = _base_evaluator()
IDENTIFIER_WORDS = re.compile(r"\b[A-Z][A-Z0-9_/-]{1,}\b")
URL_OR_FILENAME = re.compile(r"(?:https?://|\b[\w.-]+\.(?:docx|xlsx|pdf|csv|md|html)\b)", re.I)


def _load(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return BASE._load_dataset(path)


def _identifier_query(query: str) -> bool:
    words = [word for word in IDENTIFIER_WORDS.findall(query) if word not in {"I", "OK"}]
    return bool(words or URL_OR_FILENAME.search(query))


def _exact_rescue(query: str, lexical: list[RetrievalResult], semantic: list[RetrievalResult]) -> list[RetrievalResult]:
    """Prepend lexical results with a high-confidence identifier/title signal."""

    if not _identifier_query(query):
        return list(semantic)
    query_lower = query.casefold()
    identifier_words = [word.casefold() for word in IDENTIFIER_WORDS.findall(query) if word not in {"I", "OK"}]
    rescue: list[RetrievalResult] = []
    for result in lexical:
        title = (result.title or "").casefold()
        if title == query_lower or query_lower in title or any(word in title for word in identifier_words):
            rescue.append(result)
    seen = {result.retrieval_unit_id for result in rescue}
    return rescue + [result for result in semantic if result.retrieval_unit_id not in seen]


def _parent_key(result: RetrievalResult) -> tuple[str, str]:
    if result.source_type == "document":
        return result.source_type, result.source_record_id
    if result.source_parent_id:
        return result.source_type, result.source_parent_id
    if result.source_url:
        return result.source_type, result.source_url
    return result.source_type, result.source_record_id


def _cap_per_parent(results: list[RetrievalResult], maximum: int = 2) -> list[RetrievalResult]:
    counts: dict[tuple[str, str], int] = {}
    output: list[RetrievalResult] = []
    for result in results:
        key = _parent_key(result)
        if counts.get(key, 0) >= maximum:
            continue
        counts[key] = counts.get(key, 0) + 1
        output.append(result)
    return output


def _ranked(strategy: str, lexical: list[RetrievalResult], semantic: list[RetrievalResult]) -> list[RetrievalResult]:
    if strategy == "weighted_fts":
        return lexical
    if strategy == "semantic":
        return semantic
    if strategy == "rrf_equal":
        return _fuse(lexical, semantic, 100, semantic_weight=1.0)
    if strategy == "rrf_semantic_2":
        return _fuse(lexical, semantic, 100, semantic_weight=2.0)
    if strategy == "semantic_primary_exact":
        return _exact_rescue("", lexical, semantic)
    if strategy == "semantic_primary_exact_dedup":
        return _cap_per_parent(_exact_rescue("", lexical, semantic), maximum=2)
    raise ValueError(f"Unknown strategy: {strategy}")


def _ranked_for_query(strategy: str, query: str, lexical: list[RetrievalResult], semantic: list[RetrievalResult]) -> list[RetrievalResult]:
    if strategy in {"semantic_primary_exact", "semantic_primary_exact_dedup"}:
        results = _exact_rescue(query, lexical, semantic)
        return _cap_per_parent(results, maximum=2) if strategy.endswith("dedup") else results
    return _ranked(strategy, lexical, semantic)


def _metrics(rows: list[dict[str, Any]], ranked: dict[str, list[str]]) -> dict[str, float]:
    values = {f"hit_at_{k}": [] for k in (1, 3, 5, 8, 10)}
    values.update({"mrr": [], "recall_at_5": [], "recall_at_10": []})
    for row in rows:
        accepted = BASE._accepted_ids(row)
        relevant = BASE._relevant_ids(row)
        result_ids = list(dict.fromkeys(ranked[row["query"]]))
        positions = [index + 1 for index, value in enumerate(result_ids) if value in accepted]
        first = positions[0] if positions else None
        for k in (1, 3, 5, 8, 10):
            values[f"hit_at_{k}"].append(float(first is not None and first <= k))
        values["mrr"].append(1.0 / first if first else 0.0)
        if row.get("acceptable_expected_ids"):
            values["recall_at_5"].append(float(bool(set(result_ids[:5]) & accepted)))
            values["recall_at_10"].append(float(bool(set(result_ids[:10]) & accepted)))
        else:
            values["recall_at_5"].append(len(set(result_ids[:5]) & relevant) / len(relevant) if relevant else 0.0)
            values["recall_at_10"].append(len(set(result_ids[:10]) & relevant) / len(relevant) if relevant else 0.0)
    return {key: round(statistics.mean(value), 4) if value else 0.0 for key, value in values.items()}


def _breakdown(rows: list[dict[str, Any]], ranked: dict[str, list[str]], field: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(field) or "unknown"), []).append(row)
    return {name: _metrics(group, ranked) for name, group in sorted(groups.items())}


def _failure_details(session, rows, ranked, strategy: str) -> list[dict[str, Any]]:
    failures = []
    for row in rows:
        top_ids = list(dict.fromkeys(ranked[row["query"]][:5]))
        if BASE._accepted_ids(row) & set(top_ids):
            continue
        target_ids = list(BASE._accepted_ids(row))
        target_details = BASE._details(session, target_ids)
        returned_details = BASE._details(session, top_ids)
        failures.append({
            "query": row["query"], "category": row.get("category"),
            "source_family": row.get("source_family"), "strategy": strategy,
            "expected": sorted(BASE._relevant_ids(row)),
            "acceptable": sorted(BASE._accepted_ids(row) - BASE._relevant_ids(row)),
            "expected_details": [target_details[value] for value in sorted(BASE._accepted_ids(row)) if value in target_details],
            "top_5": [{"rank": index + 1, **returned_details[value]} for index, value in enumerate(top_ids) if value in returned_details],
        })
    return failures


def _coverage(session, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Classify target coverage before semantic metrics are calculated."""

    settings = get_settings()
    identifiers = list(dict.fromkeys(identifier for row in rows for identifier in BASE._accepted_ids(row)))
    if not identifiers:
        return {}
    records = session.execute(text("""
      SELECT u.id::text AS id, u.source_type, u.title, u.content, u.source_area,
             u.topic, u.channel, u.source_parent_id, u.relative_path, u.metadata,
             u.is_current, u.is_stale, u.is_deleted, u.review_status,
             e.input_hash
      FROM retrieval_units u
      LEFT JOIN retrieval_embeddings e
        ON e.retrieval_unit_id = u.id
       AND e.provider = :provider AND e.model = :model AND e.model_version = :model_version
      WHERE u.id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": identifiers, "provider": settings.embedding_provider,
           "model": settings.embedding_model, "model_version": settings.embedding_model}).mappings()
    by_id = {str(record["id"]): record for record in records}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        statuses = []
        for identifier in BASE._accepted_ids(row):
            record = by_id.get(identifier)
            status = _target_status(record, settings)
            statuses.append({"id": identifier, "status": status})
        evaluable = any(item["status"] == "EVALUABLE" for item in statuses)
        result[row["query"]] = {"evaluable": evaluable, "status": "EVALUABLE" if evaluable else (statuses[0]["status"] if statuses else "SOURCE_NOT_AVAILABLE"), "targets": statuses}
    return result


def _target_status(record: Any, settings: Any) -> str:
    if record is None:
        return "SOURCE_NOT_AVAILABLE"
    if not str(record["content"] or "").strip():
        return "SOURCE_EMPTY"
    if not record["is_current"] or record["is_stale"] or record["is_deleted"]:
        return "SOURCE_NOT_AVAILABLE"
    unit = SimpleNamespace(**dict(record))
    if not is_embedding_eligible(unit, settings):
        return "SOURCE_NOT_EMBEDDED_BY_POLICY"
    return "EVALUABLE" if record["input_hash"] == embedding_input_hash(unit) else "SOURCE_NOT_EMBEDDED"


def _run(session, rows, strategies: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, list[str]]], dict[str, dict[str, Any]]]:
    provider = get_embedding_provider()
    filters = RetrievalFilters()
    coverage = _coverage(session, rows)
    metric_rows = [row for row in rows if coverage.get(row["query"], {}).get("evaluable", True)]
    candidate_data: dict[str, dict[str, Any]] = {}
    ranked_by_strategy: dict[str, dict[str, list[str]]] = {strategy: {} for strategy in strategies}
    for row in rows:
        query = row["query"]
        start = time.perf_counter()
        weighted = __import__("efds.retrieval.service", fromlist=["_search_lexical"])._search_lexical(
            session, query, scope="admin", filters=filters, limit=100, offset=0,
        )
        lexical_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        vector = provider.embed_query(query)
        embedding_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        semantic = __import__("efds.retrieval.service", fromlist=["_search_semantic"])._search_semantic(
            session, vector, provider=provider.spec.provider, model=provider.spec.model,
            model_version=provider.spec.model_version, scope="admin", filters=filters, limit=100,
        )
        semantic_db_ms = (time.perf_counter() - start) * 1000
        candidate_data[query] = {
            "lexical": weighted, "semantic": semantic,
            "lexical_ms": lexical_ms, "embedding_ms": embedding_ms, "semantic_db_ms": semantic_db_ms,
        }
        for strategy in strategies:
            results = weighted if strategy == "weighted_fts" else _ranked_for_query(strategy, query, weighted, semantic)
            ranked_by_strategy[strategy][query] = [result.retrieval_unit_id for result in results]

    methods: dict[str, Any] = {}
    for strategy in strategies:
        ranked = ranked_by_strategy[strategy]
        timing = [candidate_data[row["query"]] for row in rows]
        methods[strategy] = {
            "metrics": _metrics(metric_rows, ranked),
            "by_source_family": _breakdown(metric_rows, ranked, "source_family"),
            "by_category": _breakdown(metric_rows, ranked, "category"),
            "latency_ms": {
                "lexical_median": round(statistics.median(item["lexical_ms"] for item in timing), 3) if timing else 0.0,
                "embedding_median": round(statistics.median(item["embedding_ms"] for item in timing), 3) if timing else 0.0,
                "semantic_db_median": round(statistics.median(item["semantic_db_ms"] for item in timing), 3) if timing else 0.0,
                "end_to_end_median": round(statistics.median(item["lexical_ms"] + item["embedding_ms"] + item["semantic_db_ms"] for item in timing), 3) if timing else 0.0,
                "embedding_p95": round(sorted(item["embedding_ms"] for item in timing)[max(0, int(len(timing) * .95) - 1)], 3) if timing else 0.0,
                "semantic_db_p95": round(sorted(item["semantic_db_ms"] for item in timing)[max(0, int(len(timing) * .95) - 1)], 3) if timing else 0.0,
                "end_to_end_p95": round(sorted(item["lexical_ms"] + item["embedding_ms"] + item["semantic_db_ms"] for item in timing)[max(0, int(len(timing) * .95) - 1)], 3) if timing else 0.0,
            },
        }
    return methods, ranked_by_strategy, coverage


def _select_best(methods: dict[str, Any]) -> str:
    return max(methods, key=lambda name: (
        methods[name]["metrics"]["recall_at_10"],
        methods[name]["metrics"]["hit_at_5"],
        methods[name]["metrics"]["mrr"],
        -methods[name]["latency_ms"]["end_to_end_median"],
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--phase", choices=("dev", "holdout"), required=True)
    parser.add_argument("--strategy", help="Required for holdout; the frozen strategy name")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    rows, metadata = _load(args.dataset)
    errors = BASE._validate_rows(rows)
    if errors:
        raise SystemExit("Invalid benchmark: " + "; ".join(errors[:8]))
    strategies = ["weighted_fts", "semantic", "rrf_equal", "rrf_semantic_2", "semantic_primary_exact", "semantic_primary_exact_dedup"]
    if args.phase == "holdout":
        if args.strategy not in strategies:
            raise SystemExit(f"--strategy must be one of: {', '.join(strategies)}")
        strategies = ["weighted_fts", "semantic", args.strategy]
    with session_scope() as session:
        methods, ranked, coverage = _run(session, rows, strategies)
        evaluable_rows = [row for row in rows if coverage.get(row["query"], {}).get("evaluable", True)]
        failures = {name: _failure_details(session, evaluable_rows, ranked[name], name) for name in strategies}
    report = {
        "benchmark_metadata": metadata,
        "phase": args.phase,
        "query_count": len(rows),
        "coverage": coverage,
        "evaluable_query_count": sum(1 for item in coverage.values() if item.get("evaluable")),
        "not_evaluable_query_count": sum(1 for item in coverage.values() if not item.get("evaluable")),
        "strategies": strategies,
        "best_strategy": _select_best(methods) if args.phase == "dev" else args.strategy,
        "methods": methods,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"phase": args.phase, "query_count": len(rows), "best_strategy": report["best_strategy"], "metrics": {name: value["metrics"] for name, value in methods.items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
