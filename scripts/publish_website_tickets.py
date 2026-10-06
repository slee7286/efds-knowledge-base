"""Entrypoint for hourly outbox delivery; requires backend installation."""
from efds.integrations.ticket_slack_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
