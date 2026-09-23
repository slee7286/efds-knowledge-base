"""Import the archived #actions-tickets roots into the committee ticket register."""

from __future__ import annotations

import argparse
import json
import sys

from efds.db.session import session_scope
from efds.integrations.ticket_import import import_archived_tickets


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write missing tickets; default only previews the import")
    args = parser.parse_args(argv)
    try:
        with session_scope() as session:
            report = import_archived_tickets(session, dry_run=not args.apply)
        print(json.dumps(report, indent=2))
        return 0
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else f"Ticket import failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
