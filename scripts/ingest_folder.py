"""CLI for recursive document ingestion."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from efds.db.session import session_scope
from efds.ingestion.documents import ingest_folder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recursively ingest EFDS documents into PostgreSQL.")
    parser.add_argument("folder", type=Path, help="Folder containing documents to ingest")
    parser.add_argument("--academic-year", help="Academic year to associate with ingested documents")
    parser.add_argument("--source-type", default="filesystem", help="Provenance label for this source")
    parser.add_argument("--dry-run", action="store_true", help="Hash and inspect files without creating documents")
    parser.add_argument("--verbose", action="store_true", help="Enable informational ingestion logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if not args.folder.exists():
        print(f"Folder does not exist: {args.folder}", file=sys.stderr)
        return 2
    if not args.folder.is_dir():
        print(f"Expected a folder, got: {args.folder}", file=sys.stderr)
        return 2
    try:
        with session_scope() as session:
            summary = ingest_folder(
                session,
                args.folder,
                academic_year=args.academic_year,
                source_type=args.source_type,
                dry_run=args.dry_run,
            )
    except Exception as error:
        print(f"Ingestion failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Run {summary.run_id}: {summary.status}; "
        f"seen={summary.records_seen}, created={summary.records_created}, "
        f"skipped={summary.records_skipped}, failed={summary.records_failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

