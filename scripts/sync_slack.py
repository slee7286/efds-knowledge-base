"""Validate, discover and synchronise the allowlisted Slack archive."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from efds.config import get_settings, get_slack_bot_token
from efds.db.models import SlackChannel, SlackChannelSyncSetting
from efds.db.session import session_scope
from efds.integrations.slack import SlackSynchronizer, sync_slack
from efds.integrations.slack_api import SlackClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronise explicitly enabled EFDS Slack channels.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Validate the backend token and show workspace identity")
    mode.add_argument("--list-channels", action="store_true", help="Discover accessible public/private channels without ingesting messages")
    mode.add_argument("--channel", help="Synchronise exactly one enabled Slack channel ID")
    mode.add_argument("--all-enabled", action="store_true", help="Synchronise every enabled channel")
    parser.add_argument("--full", action="store_true", help="Backfill all history exposed by Slack for selected channels")
    parser.add_argument("--since", help="Incremental lower bound as Slack timestamp or ISO-8601 datetime")
    parser.add_argument("--lookback-days", type=int, default=7, help="Incremental reconciliation window before the checkpoint (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and count without database writes")
    parser.add_argument("--verbose", action="store_true", help="Log channel-level progress")
    return parser


def _since_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        float(value)
        return value
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return str(parsed.timestamp())


def _client() -> SlackClient:
    return SlackClient(get_slack_bot_token())


def _redacted_error(error: Exception) -> str:
    message = str(error)
    try:
        settings = get_settings()
        if settings.slack_bot_token:
            message = message.replace(settings.slack_bot_token, "<redacted>")
        message = message.replace(settings.database_url, "<redacted>")
    except Exception:
        return message
    return message


def _print_channels(session, synchronizer: SlackSynchronizer) -> None:
    channels = synchronizer.discover_channels(persist=True)
    session.commit()
    headers = ("ID", "NAME", "VISIBILITY", "ARCHIVED", "SYNC ENABLED")
    rows: list[tuple[str, str, str, str, str]] = []
    for channel in sorted(channels, key=lambda item: item.name.casefold()):
        setting = session.get(SlackChannelSyncSetting, channel.id)
        visibility = "private" if channel.is_private else "public"
        rows.append((channel.id, channel.name, visibility, str(channel.archived), str(bool(setting and setting.enabled))))
    widths = [max(len(header), *(len(row[index]) for row in rows)) for index, header in enumerate(headers)]
    print("  ".join(f"{header:<{width}}" for header, width in zip(headers, widths)).rstrip())
    for row in rows:
        print("  ".join(f"{value:<{width}}" for value, width in zip(row, widths)).rstrip())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.lookback_days < 0:
        print("--lookback-days must be non-negative", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client = _client()
        if args.check:
            identity = client.validate()
            print("Authenticated successfully")
            print(f"Workspace: {identity.team_name} ({identity.team_id})")
            if identity.team_domain:
                print(f"Domain: {identity.team_domain}")
            if identity.bot_user_id:
                print(f"Bot user: {identity.bot_name or identity.bot_user_id} ({identity.bot_user_id})")
            return 0
        if args.list_channels:
            with session_scope() as session:
                identity = client.validate()
                synchronizer = SlackSynchronizer(session, client, identity)
                _print_channels(session, synchronizer)
            return 0
        with session_scope() as session:
            summary = sync_slack(
                session,
                client,
                channel_id=args.channel,
                all_enabled=args.all_enabled or not args.channel,
                full=args.full,
                since=_since_timestamp(args.since),
                lookback_days=args.lookback_days,
                dry_run=args.dry_run,
            )
    except Exception as error:
        print(_redacted_error(error), file=sys.stderr)
        return 1
    print(
        f"Run {summary.run_id or '(dry run)'}: {summary.status}; "
        f"channels={summary.channels_succeeded}/{summary.channels_attempted}, "
        f"messages seen={summary.messages_seen}, created={summary.messages_created}, "
        f"updated={summary.messages_updated}, skipped={summary.messages_skipped}, "
        f"deleted={summary.messages_deleted}, links={summary.links_extracted}, "
        f"files={summary.files_seen}, reactions={summary.reactions_seen}, "
        f"errors={len(summary.errors)}"
    )
    return 1 if summary.channels_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
