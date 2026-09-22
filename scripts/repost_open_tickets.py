"""Repost only X-marked action tickets to #actions-tickets."""
from __future__ import annotations

import argparse
import json
import sys

from efds.config import get_slack_bot_token
from efds.db.session import session_scope
from efds.integrations.slack_api import SlackClient
from efds.integrations.ticket_repost import execute_reposts, plan_reposts

CHANNEL_ID = "C0BQPDP5T44"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Review eligible ticket IDs without posting")
    args = parser.parse_args(argv)
    try:
        client = SlackClient(get_slack_bot_token())
        with session_scope() as session:
            candidates = plan_reposts(session, client, CHANNEL_ID)
        if args.dry_run:
            print(json.dumps({"channel": "actions-tickets", "eligible": [item.ticket_id for item in candidates], "count": len(candidates)}, indent=2))
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
