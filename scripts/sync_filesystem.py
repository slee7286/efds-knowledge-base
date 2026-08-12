"""Reconcile the local EFDS OneDrive institutional filesystem."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from efds.db.session import session_scope
from efds.ingestion.filesystem import FilesystemConfig, sync_filesystem, watch_filesystem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronise the EFDS OneDrive filesystem into versioned PostgreSQL source records.")
    parser.add_argument("--root", type=Path, help="OneDrive root; defaults to EFDS_FILES_ROOT")
    parser.add_argument("--config", type=Path, help="Filesystem exclusion TOML configuration")
    parser.add_argument("--dry-run", action="store_true", help="Discover and hash without database source/version writes")
    parser.add_argument("--full", action="store_true", help="Complete reconciliation; mark absent prior sources missing")
    parser.add_argument("--area", help="Restrict discovery to one top-level area, e.g. 01_Governance")
    parser.add_argument("--include", action="append", default=[], help="Relative path/glob to include; may be repeated")
    parser.add_argument("--exclude", action="append", default=[], help="Additional relative directory exclusion; may be repeated")
    parser.add_argument("--limit", type=int, help="Limit discovered files for a controlled first run")
    parser.add_argument("--watch", action="store_true", help="Poll for local changes and reconcile repeatedly")
    parser.add_argument("--interval", type=float, default=5.0, help="Watch polling interval in seconds (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Enable informational logs")
    return parser


def _root(value: Path | None) -> Path | None:
    if value is not None:
        return value.expanduser()
    configured = os.getenv("EFDS_FILES_ROOT", "").strip()
    return Path(configured).expanduser() if configured else None


def _print_summary(summary) -> None:
    print(
        f"Run {summary.run_id or '(dry run)'}: {summary.status}; "
        f"discovered={summary.files_discovered}, created={summary.files_created}, "
        f"updated={summary.files_updated}, unchanged={summary.files_unchanged}, "
        f"missing={summary.files_missing}, restored={summary.files_restored}, "
        f"renamed={summary.files_renamed}, moved={summary.files_moved}, "
        f"versions={summary.versions_created}, duplicates={summary.duplicates_detected}, "
        f"unsupported={summary.files_unsupported}, unavailable={summary.unavailable}, "
        f"excluded={summary.excluded}, failures={summary.extraction_failures + len(summary.errors)}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _root(args.root)
    if root is None:
        print("Set EFDS_FILES_ROOT or pass --root PATH", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit <= 0:
        print("--limit must be positive", file=sys.stderr)
        return 2
    if args.interval <= 0:
        print("--interval must be positive", file=sys.stderr)
        return 2
    if args.watch and args.dry_run:
        print("--watch cannot be combined with --dry-run", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    config = FilesystemConfig.load(args.config)
    options = {"config": config, "area": args.area, "includes": tuple(args.include), "excludes": tuple(args.exclude), "limit": args.limit, "dry_run": args.dry_run}
    try:
        if args.watch:
            watch_filesystem(root, **options, interval_seconds=args.interval)
            return 0
        with session_scope() as session:
            summary = sync_filesystem(session, root, **options, full=args.full)
    except KeyboardInterrupt:
        print("Filesystem watch stopped")
        return 0
    except Exception as error:
        print(f"Filesystem sync failed: {error}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0 if not summary.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
