"""Report embedding eligibility and coverage without calling an embedding provider."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from efds.config import get_settings
from efds.db.session import session_scope
from efds.retrieval.embeddings import embedding_input_hash, is_embedding_eligible


def _unit(row: Any) -> SimpleNamespace:
    return SimpleNamespace(**dict(row))


def collect_coverage(source: str | None = None, area: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    with session_scope() as session:
        query = """
          SELECT u.id, u.source_type, u.title, u.content, u.source_area, u.topic,
                 u.channel, u.source_parent_id, u.relative_path, u.metadata,
                 u.is_current, u.is_stale, u.is_deleted, u.review_status,
                 e.input_hash
          FROM retrieval_units u
          LEFT JOIN retrieval_embeddings e
            ON e.retrieval_unit_id = u.id
           AND e.provider = :provider
           AND e.model = :model
           AND e.model_version = :model_version
          WHERE u.is_current AND NOT u.is_stale AND NOT u.is_deleted
        """
        params: dict[str, object] = {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "model_version": settings.embedding_model,
        }
        if source:
            query += " AND u.source_type = :source"
            params["source"] = source
        if area:
            query += " AND lower(u.source_area) = :area"
            params["area"] = area.casefold()
        rows = session.execute(text(query), params).mappings().all()

    totals = defaultdict(int)
    by_area: dict[str, dict[str, int]] = {}
    by_source_type: dict[str, dict[str, int]] = {}
    for row in rows:
        unit = _unit(row)
        eligible = is_embedding_eligible(unit, settings)
        existing_hash = row["input_hash"]
        current_hash = embedding_input_hash(unit) if eligible else None
        if not eligible:
            status = "ineligible"
        elif existing_hash is None:
            status = "missing"
        elif existing_hash != current_hash:
            status = "outdated"
        else:
            status = "embedded"
        key = row["source_area"] or "(none)"
        bucket = by_area.setdefault(key, {"eligible": 0, "embedded": 0, "missing": 0, "outdated": 0, "ineligible": 0})
        source_bucket = by_source_type.setdefault(row["source_type"], {"eligible": 0, "embedded": 0, "missing": 0, "outdated": 0, "ineligible": 0})
        bucket[status] += 1
        source_bucket[status] += 1
        if eligible:
            bucket["eligible"] += 1
            source_bucket["eligible"] += 1
            totals["eligible"] += 1
        totals[status] += 1
        totals["units"] += 1

    return {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "source_filter": source,
        "area_filter": area,
        "totals": dict(totals),
        "by_source_type": dict(sorted(by_source_type.items())),
        "by_source_area": dict(sorted(by_area.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report current embedding eligibility and coverage")
    parser.add_argument("--source")
    parser.add_argument("--area")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = collect_coverage(args.source, args.area)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['provider']} / {report['model']}")
        print(json.dumps(report["totals"], indent=2))
        for source_type, values in report["by_source_type"].items():
            print(f"{source_type}: {values}")
        for area, values in report["by_source_area"].items():
            print(f"{area}: {values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
