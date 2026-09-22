"""Retired entry point: meeting sources now come from Slack-linked Google Docs."""
import sys


def main(argv=None):
    print('Meetily sync has been removed. Run scripts/sync_slack.py --all-enabled, then '
          'scripts/sync_google_docs_meetings.py instead. Existing meeting history is retained.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
