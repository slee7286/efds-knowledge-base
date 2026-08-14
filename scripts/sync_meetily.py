"""Synchronise Meetily local meetings or staged exports into EFDS PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from efds.db.session import session_scope
from efds.integrations.meetily import MeetilyReader, read_export_directory, sync_meetily


def default_source() -> Path:
    configured = os.getenv("MEETILY_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    appdata = os.getenv("APPDATA", "")
    if appdata:
        return Path(appdata) / "com.meetily.ai" / "meeting_minutes.sqlite"
    return Path("meeting_minutes.sqlite")


def load_source(path: Path):
    if path.is_dir():
        return read_export_directory(path), "export_directory"
    return MeetilyReader(path).meetings(), str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Meetily meetings into the EFDS meeting archive.")
    parser.add_argument("--source", type=Path, default=default_source(), help="Meetily SQLite database or export directory")
    parser.add_argument("--check", action="store_true", help="Check source readability and report meeting count")
    parser.add_argument("--list", action="store_true", help="List discovered meetings without writing PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Parse and compare without database writes")
    parser.add_argument("--full", action="store_true", help="Mark absent prior Meetily meetings missing")
    parser.add_argument("--meeting", help="Only import one Meetily meeting ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        meetings, source_reference = load_source(args.source.expanduser())
    except Exception as error:
        print(f"Meetily source unavailable: {error}", file=sys.stderr)
        return 2
    if args.check:
        print(f"Meetily source readable: {args.source}")
        print(f"Meetings discovered: {len(meetings)}")
        return 0
    if args.list:
        print(f"{'ID':36}  {'TITLE':40}  CREATED")
        for meeting in meetings:
            print(f"{meeting.external_id:36}  {meeting.title[:40]:40}  {meeting.created_at or '-'}")
        return 0
    try:
        with session_scope() as session:
            summary = sync_meetily(session, meetings, source_reference=source_reference, full=args.full, meeting_id=args.meeting, dry_run=args.dry_run)
    except Exception as error:
        print(f"Meetily sync failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Run {summary.run_id or '(dry run)'}: {summary.status}; "
        f"meetings={summary.meetings_discovered}, created={summary.meetings_created}, "
        f"updated={summary.meetings_updated}, unchanged={summary.meetings_unchanged}, "
        f"transcripts={summary.transcripts_created + summary.transcripts_updated}, "
        f"summaries={summary.summaries_created + summary.summaries_updated}, "
        f"notes={summary.notes_created + summary.notes_updated}, "
        f"segments={summary.segments_created}, missing={summary.missing}, restored={summary.restored}, "
        f"errors={len(summary.errors)}"
    )
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
