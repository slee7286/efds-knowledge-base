"""Create auditable pre-term/full holdout views from the frozen V2 holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from efds.db.session import session_scope
from efds.retrieval.embeddings import DEFAULT_DOCUMENT_AREAS


def _ids(row: dict[str, Any]) -> list[str]:
    values = list(row.get("expected_retrieval_unit_ids", row.get("primary_expected_ids", [])))
    values += list(row.get("acceptable_expected_ids", []))
    return list(dict.fromkeys(str(value) for value in values))


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"queries": payload}


def _preterm_rows(rows: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_types = {"icu_article", "knowledge_requirement", "knowledge_timing_rule", "knowledge_process", "knowledge_process_step", "knowledge_resource", "knowledge_contact", "document"}
    selected = []
    for row in rows:
        primary = row.get("primary_expected_ids") or row.get("expected_retrieval_unit_ids", [])
        primary_records = [records.get(str(identifier)) for identifier in primary]
        if primary_records and all(record and record["source_type"] in allowed_types for record in primary_records):
            if all(record["source_type"] != "document" or (record["source_area"] or "").casefold() in DEFAULT_DOCUMENT_AREAS for record in primary_records):
                selected.append(row)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("evaluation/retrieval_queries_v2_holdout.json"))
    parser.add_argument("--preterm", type=Path, default=Path("evaluation/retrieval_v2_preterm_holdout.json"))
    parser.add_argument("--full", type=Path, default=Path("evaluation/retrieval_v2_full_holdout.json"))
    args = parser.parse_args(argv)
    payload = _load(args.input)
    rows = payload["queries"]
    identifiers = [identifier for row in rows for identifier in _ids(row)]
    with session_scope() as session:
        result = session.execute(text("SELECT id::text AS id, source_type, source_area FROM retrieval_units WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": identifiers}).mappings()
        records = {str(row["id"]): dict(row) for row in result}
    base = {key: value for key, value in payload.items() if key != "queries"}
    full = dict(base, benchmark_version="retrieval_v2_full_holdout_v1", scope="full_holdout", queries=rows)
    preterm_rows = _preterm_rows(rows, records)
    preterm = dict(base, benchmark_version="retrieval_v2_preterm_holdout_v1", scope="preterm_knowledge", document_areas=sorted(DEFAULT_DOCUMENT_AREAS), queries=preterm_rows)
    for path, value in ((args.preterm, preterm), (args.full, full)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"full": len(rows), "preterm": len(preterm_rows), "preterm_document_areas": sorted(DEFAULT_DOCUMENT_AREAS)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
