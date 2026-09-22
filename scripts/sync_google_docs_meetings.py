"""Import Google Docs meeting logs linked from the enabled Slack meetings channel."""
import argparse
import json
import sys
from sqlalchemy import select
from efds.db.models import SlackChannel
from efds.db.session import session_scope
from efds.integrations.google_docs_meetings import sync_google_docs_meetings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', help='Slack channel ID; defaults to the unique channel named meetings')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        with session_scope() as session:
            channel_id = args.channel
            if not channel_id:
                channels = session.scalars(select(SlackChannel).where(SlackChannel.name == 'meetings')).all()
                if len(channels) != 1:
                    raise ValueError('Specify --channel: expected exactly one discovered meetings channel')
                channel_id = channels[0].id
            summary = sync_google_docs_meetings(session, channel_id, dry_run=args.dry_run)
        print(json.dumps(summary, indent=2))
        return 1 if summary['errors'] else 0
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else f'Meeting sync failed: {type(error).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
