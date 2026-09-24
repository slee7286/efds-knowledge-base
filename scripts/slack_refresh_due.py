"""Gate scheduled Slack refreshes on the oldest enabled channel checkpoint."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import psycopg

MAX_AGE = timedelta(hours=12)


def refresh_due(
    checkpoints: list[datetime | None], now: datetime, *, force: bool = False
) -> bool:
    if not checkpoints:
        raise ValueError("No Slack channels are enabled for synchronization.")
    if force or any(checkpoint is None for checkpoint in checkpoints):
        return True
    return (
        min(checkpoint for checkpoint in checkpoints if checkpoint is not None)
        <= now - MAX_AGE
    )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Missing DATABASE_URL repository secret", file=sys.stderr)
        return 2
    try:
        with (
            psycopg.connect(database_url, connect_timeout=10) as conn,
            conn.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT last_successful_sync_at FROM public.slack_channel_sync_settings WHERE enabled"
            )
            checkpoints = [row[0] for row in cursor.fetchall()]
        due = refresh_due(
            checkpoints,
            datetime.now(UTC),
            force=os.environ.get("FORCE_REFRESH") == "true",
        )
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    except psycopg.Error:
        print("Slack freshness database check failed", file=sys.stderr)
        return 2

    line = f"refresh={'true' if due else 'false'}\n"
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(line)
    print(
        f"Enabled Slack channels: {len(checkpoints)}; refresh {'due' if due else 'not due'}."
    )
    if not due and (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(
                "Slack refresh skipped: every enabled channel has a successful sync within 12 hours.\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
