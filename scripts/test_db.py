"""Check the configured PostgreSQL connection without exposing credentials."""

from __future__ import annotations

import re
import sys

from sqlalchemy import text

from efds.config import get_settings
from efds.db.session import get_engine


def main() -> int:
    try:
        with get_engine().connect() as connection:
            database_name, current_user, version, current_timestamp = connection.execute(
                text("SELECT current_database(), current_user, version(), current_timestamp")
            ).one()
        version_summary = re.split(r"\s+", str(version), maxsplit=1)[0:2]
        print(f"Database: {database_name}")
        print(f"User: {current_user}")
        print(f"PostgreSQL: {' '.join(version_summary)}")
        print(f"Server timestamp: {current_timestamp}")
        return 0
    except Exception as error:
        message = str(error)
        try:
            message = message.replace(get_settings().database_url, "<redacted>")
        except RuntimeError:
            pass
        print(f"Database connection failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
