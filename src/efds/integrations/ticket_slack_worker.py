"""Bounded scheduled website-ticket publisher (never imports Slack-origin tickets)."""
from __future__ import annotations

import argparse
import os
import sys

import psycopg

from efds.integrations.slack_api import SlackClient
from efds.integrations.ticket_slack_publisher import Publisher
from efds.integrations.ticket_slack_store import OutboxStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish newly created website tickets to Slack")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")
    database_url = os.environ.get("DATABASE_URL")
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not database_url or not token:
        print("DATABASE_URL and SLACK_BOT_TOKEN are required", file=sys.stderr)
        return 2
    counts = {"posted": 0, "reconciled": 0, "retry": 0, "uncertain": 0}
    try:
        with psycopg.connect(database_url, connect_timeout=10) as conn:
            publisher = Publisher(OutboxStore(conn), SlackClient(token),
                                  os.environ.get("WEBSITE_URL", "https://www.imperial-efds.com"))
            for _ in range(args.limit):
                result = publisher.run_once()
                if result == "empty":
                    break
                counts[result] += 1
    except (psycopg.Error, ValueError, RuntimeError):
        # psycopg exceptions may include a DSN; never print exception details.
        print("Ticket publisher failed; inspect outbox state before retrying", file=sys.stderr)
        return 1
    print("Ticket publisher: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
    return 1 if counts["retry"] or counts["uncertain"] else 0
