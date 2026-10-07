"""At-most-once ticket notification delivery with hidden block-ID reconciliation.

The outbox store commits a `sending` claim *before* any Slack POST. If a process
vanishes between claim and acknowledgement, the store quarantines it as uncertain;
operators may reconcile the Slack block ID, but an absent ID never licenses a blind retry.
"""
from __future__ import annotations

from html import escape
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from efds.integrations.slack_api import SlackApiError


CHANNEL = "C0BQPDP5T44"
LABELS = {"open": "Open", "in_progress": "In progress", "blocked": "Blocked",
          "completed": "Completed", "cancelled": "Cancelled"}
ACTIONS = {"committee_create": "Created", "committee_assign": "Assignment changed",
           "committee_status": "Status changed", "committee_update": "Details updated"}


def _safe(value: Any) -> str:
    text = escape(str(value or "").replace("\n", " ").replace("\r", " "), quote=False)
    return re.sub(r"@(?=(?:here|channel|everyone)\b)", "@\u200b", text, flags=re.IGNORECASE)


def _split_sections(text: str, limit: int = 2800) -> list[str]:
    sections: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while line:
            remaining = limit - len(current)
            if len(line) <= remaining:
                current += line
                line = ""
            elif current:
                sections.append(current)
                current = ""
            else:
                sections.append(line[:limit])
                line = line[limit:]
    if current:
        sections.append(current)
    return sections or [text]


def _message_blocks(text: str, event_id: str, *, preview: bool = False) -> list[dict[str, Any]]:
    prefix = (f"efds-ticket-preview:{event_id}:{uuid4().hex}"
              if preview else f"efds-ticket-event:{event_id}")
    return [{
        "type": "section",
        "block_id": f"{prefix}:{index}",
        "text": {"type": "mrkdwn", "text": section},
    } for index, section in enumerate(_split_sections(text))]


def _legacy_marker(event: dict[str, Any]) -> str:
    return f"[EFDS ticket:{event['ticket_id']} event:{event['event_id']}]"


def _has_event_block(message: dict[str, Any], event: dict[str, Any]) -> bool:
    prefix = f"efds-ticket-event:{event['event_id']}:"
    blocks = message.get("blocks")
    return isinstance(blocks, list) and any(
        isinstance(block, dict) and str(block.get("block_id") or "").startswith(prefix)
        for block in blocks
    )


def format_root(event: dict[str, Any], website_url: str) -> str:
    snapshot = event["snapshot"]
    base = website_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("WEBSITE_URL must be an HTTPS origin")
    ticket_id = str(UUID(str(event["ticket_id"])))
    assignees = ", ".join(_safe(name) for name in snapshot.get("assignees", [])) or "Unassigned"
    due = str(snapshot.get("due_at") or "")
    due = due[:10] if due else "Not set"
    description = _safe(snapshot.get("description") or "Not provided")
    return (f"{_safe(snapshot['title'])}\n"
            f"Status: {_safe(LABELS.get(snapshot.get('execution_status'), snapshot.get('execution_status') or 'Open'))}\n"
            f"Assignees: {assignees}\nDue: {_safe(due)}\nDescription: {description}\n"
            f"<{base}/dashboard/tickets/{ticket_id}|Open on website>")


def format_event(event: dict[str, Any]) -> str:
    snapshot = event["snapshot"]
    label = ACTIONS[event["action"]]
    status = LABELS.get(snapshot.get("execution_status"), snapshot.get("execution_status") or "Open")
    assignees = ", ".join(_safe(name) for name in snapshot.get("assignees", [])) or "Unassigned"
    return (f"{label}: {_safe(snapshot['title'])}\nStatus: {_safe(status)}"
            f" | Assignees: {assignees}")


class Publisher:
    def __init__(self, store: Any, slack: Any, website_url: str):
        self.store = store
        self.slack = slack
        self.website_url = website_url

    def run_once(self) -> str:
        event = self.store.claim()
        if event is None:
            return "empty"
        root_ts = event.get("root_ts")
        channel = event["channel"]
        try:
            messages = self.slack.replies(channel, root_ts) if root_ts else self.slack.history(channel)
        except Exception:
            self.store.retry_later(event)
            return "retry"
        legacy_marker = _legacy_marker(event)
        for message in messages:
            if (_has_event_block(message, event) or legacy_marker in str(message.get("text") or "")) and message.get("ts"):
                ts = str(message["ts"])
                self.store.delivered(event, ts, root_ts or ts)
                return "reconciled"
        if event.get("reconcile_only"):
            self.store.uncertain_delivery(event)
            return "uncertain"
        if root_ts:
            # Use a distinct preview ID so a root update cannot masquerade as the
            # event's thread reply during uncertain-delivery reconciliation.
            root_text = format_root(event, self.website_url)
            try:
                self.slack.update_message(
                    channel, root_ts, root_text,
                    blocks=_message_blocks(root_text, str(event["event_id"]), preview=True),
                )
            except Exception:
                self.store.retry_later(event)
                return "retry"
        text = format_event(event) if root_ts else format_root(event, self.website_url)
        try:
            ts = self.slack.post_message(
                channel, text, thread_ts=root_ts,
                blocks=_message_blocks(text, str(event["event_id"])),
            )
        except SlackApiError as error:
            if error.error_code:  # definitive Slack rejection: safe to retry later
                self.store.retry_later(event)
                return "retry"
            self.store.uncertain_delivery(event)
            return "uncertain"
        except Exception:
            self.store.uncertain_delivery(event)
            return "uncertain"
        self.store.delivered(event, ts, root_ts or ts)
        return "posted"
