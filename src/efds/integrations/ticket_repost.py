"""Repost open Slack action tickets without reviving completed work."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import OperationalRecord, SlackChannel, SlackChannelSyncSetting, SlackMessage, SlackReaction
from .slack_api import SlackClient

TICKET = re.compile(r"^ACTION-(\d{3})\b", re.IGNORECASE)
REPOST = re.compile(r"^\[EFDS archive repost: ACTION-(\d{3})\]", re.IGNORECASE)
CHECK = "white_check_mark"
OPEN = "x"
REPOST_INTERVAL_SECONDS = 21 * 24 * 60 * 60
MAX_SYNC_AGE_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class TicketCandidate:
    ticket_id: str
    original_ts: str
    original_text: str
    original_permalink: str | None
    last_repost_ts: str | None


def reaction_names(message: dict) -> set[str]:
    return {str(row.get("name") or "").lower() for row in (message.get("reactions") or [])}


def plan_reposts(session: Session, client: SlackClient, channel_id: str, *, now: float | None = None) -> list[TicketCandidate]:
    """Use archived original text and current Slack reactions to select open tickets."""
    channel = session.get(SlackChannel, channel_id)
    setting = session.get(SlackChannelSyncSetting, channel_id)
    if not channel or channel.name != "actions-tickets" or not setting or not setting.enabled:
        raise ValueError("The enabled #actions-tickets archive is required")
    synced_at = setting.last_successful_sync_at
    if not synced_at:
        raise ValueError("Sync #actions-tickets successfully before reposting")
    clock = time.time() if now is None else now
    if not isinstance(synced_at, datetime):
        raise ValueError("The #actions-tickets sync checkpoint is invalid")
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=timezone.utc)
    if clock - synced_at.timestamp() > MAX_SYNC_AGE_SECONDS:
        raise ValueError("The #actions-tickets archive is stale; sync it before reposting")

    originals: dict[str, SlackMessage] = {}
    archived_reposts: dict[str, list[SlackMessage]] = {}
    rows = session.scalars(select(SlackMessage).where(
        SlackMessage.channel_id == channel_id,
        SlackMessage.is_deleted.is_(False),
    )).all()
    for row in rows:
        repost = REPOST.match(row.message_text or "")
        if repost:
            archived_reposts.setdefault(repost.group(1), []).append(row)
            continue
        match = TICKET.match(row.message_text or "")
        if match and (match.group(1) not in originals or float(row.slack_ts) > float(originals[match.group(1)].slack_ts)):
            originals[match.group(1)] = row

    if not originals:
        return []
    # Committee edits are authoritative too. Never resurrect work that the
    # dashboard has already marked completed or cancelled.
    closed_codes = {
        str(record.metadata_.get("slack_ticket_code", "")).upper()
        for record in session.scalars(select(OperationalRecord).where(
            OperationalRecord.record_type == "action_item",
            OperationalRecord.execution_status.in_(("completed", "cancelled")),
        )).all()
        if record.metadata_.get("origin") == "slack_actions_tickets"
    }
    archived_reactions: dict[str, set[str]] = {}
    ids = [row.id for row in originals.values()] + [row.id for group in archived_reposts.values() for row in group]
    for reaction in session.scalars(select(SlackReaction).where(SlackReaction.message_id.in_(ids))).all():
        archived_reactions.setdefault(str(reaction.message_id), set()).add(reaction.name.lower())

    # A fresh read catches checks added after the last database sync. It also
    # sees our prior reposts if a process died after posting but before syncing.
    live = client.history(channel_id)
    live_originals: dict[str, dict] = {}
    reposts: dict[str, list[dict]] = {}
    for row in live:
        value = row.get("text") or ""
        repost = REPOST.match(value)
        if repost:
            reposts.setdefault(repost.group(1), []).append(row)
        else:
            original = TICKET.match(value)
            if original:
                previous = live_originals.get(original.group(1))
                if previous is None or float(row.get("ts") or 0) > float(previous.get("ts") or 0):
                    live_originals[original.group(1)] = row

    candidates = []
    for ticket_id, source in sorted(originals.items()):
        if f"ACTION-{ticket_id}" in closed_codes:
            continue
        related_reposts = reposts.get(ticket_id, [])
        visible_reposts = {str(row.get("ts")): row for row in related_reposts}
        current = live_originals.get(ticket_id)
        archived_status = archived_reactions.get(str(source.id), set())
        live_status = reaction_names(current) if current else set()
        source_status = live_status if current else archived_status
        # A check on any visible version wins. When Slack has hidden the
        # original or a repost, its last archived check still prevents resurrection.
        if (CHECK in source_status
            or any(CHECK in reaction_names(row) for row in related_reposts)
            or any(CHECK in (reaction_names(visible_reposts[row.slack_ts]) if row.slack_ts in visible_reposts
                             else archived_reactions.get(str(row.id), set()))
                   for row in archived_reposts.get(ticket_id, []))):
            continue
        if (OPEN not in source_status
            and not any(OPEN in reaction_names(row) for row in related_reposts)
            and not any(OPEN in (reaction_names(visible_reposts[row.slack_ts]) if row.slack_ts in visible_reposts
                                 else archived_reactions.get(str(row.id), set()))
                        for row in archived_reposts.get(ticket_id, []))):
            continue
        latest = max(related_reposts, key=lambda row: float(row.get("ts") or 0), default=None)
        last_ts = str(latest.get("ts")) if latest else None
        if last_ts and clock - float(last_ts) < REPOST_INTERVAL_SECONDS:
            continue
        candidates.append(TicketCandidate(
            ticket_id=f"ACTION-{ticket_id}", original_ts=source.slack_ts,
            original_text=source.message_text or "", original_permalink=source.permalink,
            last_repost_ts=last_ts,
        ))
    return candidates


def repost_text(ticket: TicketCandidate) -> str:
    source = f"Original: <{ticket.original_permalink}|view source>" if ticket.original_permalink else f"Original Slack timestamp: {ticket.original_ts}"
    # Archived text can contain user/channel mentions. Escape Slack's special
    # characters so a reminder cannot ping people or re-expand old links.
    safe_text = ticket.original_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_text = re.sub(r"@(?=(?:here|channel|everyone)\b)", "@\u200b", safe_text, flags=re.IGNORECASE)
    if len(safe_text) > 3500:
        safe_text = safe_text[:3500].rstrip() + "\n[Long source truncated; open the EFDS ticket archive for the full record.]"
    return (f"[EFDS archive repost: {ticket.ticket_id}]\n"
            f":x: Still open. Reposted by the EFDS bot so this ticket stays visible. "
            f"React with :white_check_mark: on this copy when complete.\n"
            f"{source}\n\n{safe_text}")


def execute_reposts(client: SlackClient, channel_id: str, candidates: list[TicketCandidate], *, sleep=time.sleep) -> list[tuple[str, str]]:
    posted = []
    for index, ticket in enumerate(candidates):
        if index:
            sleep(1.1)
        timestamp = client.post_message(channel_id, repost_text(ticket))
        posted.append((ticket.ticket_id, timestamp))
    return posted
