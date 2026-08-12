"""Enable or disable one previously discovered Slack channel for archival."""

from __future__ import annotations

import argparse
import sys

from efds.db.models import SlackChannel, SlackChannelSyncSetting
from efds.db.session import session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure the EFDS Slack archive allowlist.")
    parser.add_argument("channel_id")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable", action="store_true")
    action.add_argument("--disable", action="store_true")
    args = parser.parse_args(argv)
    with session_scope() as session:
        channel = session.get(SlackChannel, args.channel_id)
        if channel is None:
            print("Channel is not discovered. Run sync_slack.py --list-channels first.", file=sys.stderr)
            return 1
        setting = session.get(SlackChannelSyncSetting, args.channel_id)
        if setting is None:
            setting = SlackChannelSyncSetting(channel_id=args.channel_id)
            session.add(setting)
        setting.enabled = bool(args.enable)
        print(f"{channel.name} ({channel.id}) sync enabled={setting.enabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
