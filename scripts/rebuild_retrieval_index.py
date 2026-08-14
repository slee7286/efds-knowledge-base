"""Rebuild the derived unified retrieval index from canonical source tables."""

from __future__ import annotations

import argparse
import sys

from efds.db.session import session_scope
from efds.retrieval.indexer import RETRIEVAL_SOURCE_TYPES, rebuild_retrieval_index


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild the EFDS PostgreSQL retrieval index.")
    parser.add_argument("--dry-run", action="store_true", help="Build and count units without writing rows")
    parser.add_argument("--full", action="store_true", help="Rebuild all supported source families")
    parser.add_argument("--source", choices=RETRIEVAL_SOURCE_TYPES, help="Rebuild one source family")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.source and not args.full:
        source = args.source
    else:
        source = None
    try:
        with session_scope() as session:
            stats = rebuild_retrieval_index(session, source=source, dry_run=args.dry_run)
    except Exception as error:
        print(f"Retrieval rebuild failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Retrieval rebuild {'(dry run) ' if args.dry_run else ''}complete: "
        f"discovered={stats['discovered']}, created={stats['created']}, "
        f"updated={stats['updated']}, unchanged={stats['unchanged']}, retired={stats['retired']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
