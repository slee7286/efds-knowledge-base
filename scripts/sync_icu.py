"""Synchronise the ICU crawler's current local corpus into PostgreSQL."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from efds.config import get_settings
from efds.db.session import session_scope
from efds.integrations.icu import sync_icu


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "icu-crawler" / "data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronise the ICU crawler's current data directory into EFDS PostgreSQL."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help="icu-crawler data directory (default: sibling ../icu-crawler/data)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without database or filesystem writes")
    parser.add_argument("--max-articles", type=int, help="Process at most N articles; disables missing-source reconciliation")
    parser.add_argument("--force", action="store_true", help="Re-evaluate every current article (the sync already scans all files)")
    parser.add_argument("--verbose", action="store_true", help="Enable informational sync logs")
    return parser


def _redacted_error(error: Exception) -> str:
    message = str(error)
    try:
        database_url = get_settings().database_url
    except RuntimeError:
        database_url = ""
    return message.replace(database_url, "<redacted>") if database_url else "Database operation failed"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    if args.max_articles is not None and args.max_articles < 0:
        print("--max-articles must be non-negative", file=sys.stderr)
        return 2
    if not args.data_dir.exists():
        print(f"ICU data directory does not exist: {args.data_dir}", file=sys.stderr)
        return 2
    if not args.data_dir.is_dir():
        print(f"Expected a directory: {args.data_dir}", file=sys.stderr)
        return 2

    try:
        with session_scope() as session:
            summary = sync_icu(
                session,
                args.data_dir,
                dry_run=args.dry_run,
                max_articles=args.max_articles,
                force=args.force,
            )
    except Exception as error:
        print(f"ICU sync failed: {_redacted_error(error)}", file=sys.stderr)
        return 1

    print(
        f"Run {summary.run_id or '(dry run)'}: {summary.status}; "
        f"seen={summary.records_seen}, created={summary.records_created}, "
        f"updated={summary.records_updated}, skipped={summary.records_skipped}, "
        f"failed={summary.records_failed}"
    )
    return 1 if summary.records_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
