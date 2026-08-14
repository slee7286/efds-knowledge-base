#!/usr/bin/env python
"""Export the KB-owned benchmark retrieval order for agent parity checks.

This is read-only.  It calls the same ``search_retrieval(..., mode=hybrid)``
path used by the benchmark evaluator; it does not call the agent or alter the
database.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from efds.db.session import session_scope
from efds.retrieval.service import search_retrieval
from efds.retrieval.types import RetrievalFilters


def _load(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return document["queries"]


def _row(result: Any, rank: int) -> dict[str, Any]:
    return {
        "rank": rank, "retrieval_unit_id": result.retrieval_unit_id,
        "source_type": result.source_type, "source_record_id": result.source_record_id,
        "source_version_id": result.source_version_id, "title": result.title,
        "score": result.score, "authority": result.authority, "visibility": result.visibility,
        "is_current": result.is_current, "is_stale": result.is_stale,
        "source_area": result.source_area, "provenance": {
            "source_url": result.source_url, "permalink": result.permalink,
            "relative_path": result.relative_path, "content_hash": result.content_hash,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export KB canonical retrieval for agent parity")
    parser.add_argument("dataset", type=Path, default=Path("evaluation/retrieval_v2_preterm_holdout.json"), nargs="?")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=("public", "member", "committee", "admin"), default="admin")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        raise SystemExit("--limit must be between 1 and 20")
    rows = _load(args.dataset)
    exported = []
    with session_scope() as session:
        for row in rows:
            results = search_retrieval(session, row["query"], scope=args.scope, filters=RetrievalFilters(), limit=args.limit, mode="hybrid")
            exported.append({"query": row["query"], "scope": args.scope, "limit": args.limit,
                             "strategy": "semantic_primary_exact via search_retrieval(mode=hybrid)",
                             "results": [_row(result, index) for index, result in enumerate(results, 1)]})
    output = {"contract": "kb_canonical_retrieval_export_v1", "query_count": len(exported), "cases": exported}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "query_count": len(exported)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
