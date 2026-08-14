"""Create a deterministic development/holdout split for a reviewed benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SEED = "efds-retrieval-v2-split-2026-08-14"


def _key(row: dict[str, Any]) -> str:
    return f"{SEED}|{row['query']}|{row.get('source_family','')}|{row.get('category','')}"


def split_rows(rows: list[dict[str, Any]], holdout_fraction: float = 0.30) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda row: hashlib.sha256(_key(row).encode()).hexdigest())
    holdout_size = max(1, round(len(ordered) * holdout_fraction))
    holdout = ordered[:holdout_size]
    dev = ordered[holdout_size:]

    # Ensure every source family and category with at least two examples is
    # represented in holdout without allowing singleton categories to dominate.
    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dev:
        source_groups[str(row.get("source_family", "unknown"))].append(row)
        category_groups[str(row.get("category", "unknown"))].append(row)

    required_groups = [("source_family", source_groups, str(row.get("source_family", "unknown"))) for row in rows]
    required_groups += [("category", category_groups, str(row.get("category", "unknown"))) for row in rows]
    seen_requirements: set[tuple[str, str]] = set()
    for kind, groups, value in required_groups:
        if (kind, value) in seen_requirements:
            continue
        seen_requirements.add((kind, value))
        all_group_rows = [row for row in rows if str(row.get(kind, "unknown")) == value]
        if len(all_group_rows) < 2 or any(str(row.get(kind, "unknown")) == value for row in holdout):
            continue
        candidates = groups.get(value, [])
        if not candidates:
            continue
        moved = min(candidates, key=lambda row: hashlib.sha256(_key(row).encode()).hexdigest())
        dev.remove(moved)
        holdout.append(moved)
        for bucket in (source_groups, category_groups):
            for values in bucket.values():
                if moved in values:
                    values.remove(moved)
    return sorted(dev, key=lambda row: row["query"]), sorted(holdout, key=lambda row: row["query"])


def _write(path: Path, *, version: str, rows: list[dict[str, Any]], split: str, peer_count: int) -> None:
    payload = {
        "benchmark_version": version,
        "split": split,
        "split_seed": SEED,
        "holdout_fraction": 0.30,
        "peer_count": peer_count,
        "queries": rows,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, default=Path("evaluation/retrieval_queries_v2_reviewed.json"), nargs="?")
    parser.add_argument("--dev", type=Path, default=Path("evaluation/retrieval_queries_v2_dev.json"))
    parser.add_argument("--holdout", type=Path, default=Path("evaluation/retrieval_queries_v2_holdout.json"))
    args = parser.parse_args(argv)
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    rows = payload["queries"] if isinstance(payload, dict) else payload
    dev, holdout = split_rows(rows)
    args.dev.parent.mkdir(parents=True, exist_ok=True)
    _write(args.dev, version="retrieval_v2_dev_v1", rows=dev, split="development", peer_count=len(holdout))
    _write(args.holdout, version="retrieval_v2_holdout_v1", rows=holdout, split="holdout", peer_count=len(dev))
    print(json.dumps({"dev": len(dev), "holdout": len(holdout), "seed": SEED}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
