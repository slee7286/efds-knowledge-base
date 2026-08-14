"""Validate whether benchmark targets can be evaluated by semantic retrieval."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from efds.config import get_settings
from efds.db.session import session_scope
from efds.retrieval.embeddings import embedding_input_hash, is_embedding_eligible


def _load(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, {}
    return payload["queries"], {key: value for key, value in payload.items() if key != "queries"}


def _ids(row: dict[str, Any]) -> list[str]:
    values = list(row.get("expected_retrieval_unit_ids", row.get("primary_expected_ids", [])))
    values += list(row.get("acceptable_expected_ids", []))
    return list(dict.fromkeys(str(value) for value in values))


def validate(path: Path) -> dict[str, Any]:
    rows, metadata = _load(path)
    settings = get_settings()
    identifiers = [value for row in rows for value in _ids(row)]
    with session_scope() as session:
        records = session.execute(text("""
          SELECT u.id::text AS id, u.source_type, u.title, u.content, u.source_area,
                 u.topic, u.channel, u.source_parent_id, u.relative_path, u.metadata,
                 u.is_current, u.is_stale, u.is_deleted, u.review_status,
                 e.input_hash
          FROM retrieval_units u
          LEFT JOIN retrieval_embeddings e
            ON e.retrieval_unit_id = u.id
           AND e.provider = :provider
           AND e.model = :model
           AND e.model_version = :model_version
          WHERE u.id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": identifiers, "provider": settings.embedding_provider,
                 "model": settings.embedding_model, "model_version": settings.embedding_model}).mappings()
        by_id = {str(row["id"]): row for row in records}

    checked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in rows:
        statuses = []
        for identifier in _ids(item):
            record = by_id.get(identifier)
            if record is None:
                status = "SOURCE_NOT_AVAILABLE"
            elif not str(record["content"] or "").strip():
                status = "SOURCE_EMPTY"
            elif not record["is_current"] or record["is_stale"] or record["is_deleted"]:
                status = "SOURCE_NOT_AVAILABLE"
            else:
                eligible = is_embedding_eligible(SimpleNamespace(**dict(record)), settings)
                current_hash = embedding_input_hash(SimpleNamespace(**dict(record))) if eligible else None
                if record["input_hash"] is not None and record["input_hash"] == current_hash:
                    status = "EVALUABLE"
                else:
                    status = "SOURCE_NOT_EMBEDDED"
                    if not eligible:
                        status = "SOURCE_NOT_EMBEDDED_BY_POLICY"
            statuses.append({"id": identifier, "status": status, "title": record["title"] if record else None})
        evaluable = any(status["status"] == "EVALUABLE" for status in statuses)
        reason = "EVALUABLE" if evaluable else (statuses[0]["status"] if statuses else "SOURCE_NOT_AVAILABLE")
        counts[reason] = counts.get(reason, 0) + 1
        checked.append({"query": item["query"], "evaluable": evaluable, "status": reason, "targets": statuses})
    return {
        "dataset": str(path),
        "benchmark_metadata": metadata,
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "query_count": len(rows),
        "evaluable_query_count": sum(value for key, value in counts.items() if key == "EVALUABLE"),
        "not_evaluable_query_count": sum(value for key, value in counts.items() if key != "EVALUABLE"),
        "counts": counts,
        "queries": checked,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check benchmark semantic-evaluation coverage")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        # Validate UUID syntax before sending an array to PostgreSQL.
        rows, _ = _load(args.dataset)
        for row in rows:
            for identifier in _ids(row):
                uuid.UUID(identifier)
        report = validate(args.dataset)
    except Exception as error:
        print(f"Benchmark coverage validation failed: {error}")
        return 1
    rendered = json.dumps(report, indent=2)
    if args.json:
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
