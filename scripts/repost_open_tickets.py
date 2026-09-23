"""Repost only X-marked action tickets to #actions-tickets."""
from __future__ import annotations

import argparse
import json
import os
import sys

from efds.config import get_slack_bot_token
from efds.db.session import session_scope
from efds.integrations.slack_api import SlackClient
from efds.integrations.ticket_repost import execute_reposts, plan_reposts

CHANNEL_ID = "C0BQPDP5T44"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Review eligible ticket IDs without posting")
    parser.add_argument("--limit", type=int, default=5, help="Maximum reminders per run, from 1 to 10 (default: 5)")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 10:
        parser.error("--limit must be between 1 and 10")
    try:
        client = SlackClient(get_slack_bot_token())
        with session_scope() as session:
            candidates = plan_reposts(session, client, CHANNEL_ID)[:args.limit]
        if args.dry_run:
            print(json.dumps({"channel": "actions-tickets", "eligible": [item.ticket_id for item in candidates], "count": len(candidates)}, indent=2))
            return 0
        if not client.has_scope("chat:write"):
            print(json.dumps({"channel": "actions-tickets", "eligible": [item.ticket_id for item in candidates], "posting_skipped": "missing_chat_write_scope"}, indent=2))
            print("::warning::EFDS Slack bot lacks chat:write; no ticket reminders were posted.")
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(summary, "a", encoding="utf-8") as stream:
                    stream.write("Ticket reminders paused: the bot token lacks `chat:write`. Add the bot scope in the EFDS Slack app and reinstall it, then update the encrypted `SLACK_BOT_TOKEN` secret if Slack issues a new token.\n")
            return 0
        posted = execute_reposts(client, CHANNEL_ID, candidates)
        print(json.dumps({"channel": "actions-tickets", "posted": [{"ticket": ticket, "ts": ts} for ticket, ts in posted], "count": len(posted)}, indent=2))
        return 0
    except Exception as error:
        # The Slack client strips tokens from its own errors. Avoid including
        # database connection details from an unexpected SQL exception.
        print(str(error) if isinstance(error, ValueError) else f"Ticket repost failed: {type(error).__name__}: {error if type(error).__name__ == 'SlackApiError' else 'check backend logs'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
