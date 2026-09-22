"""Repost open Slack action tickets without reviving completed work."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import SlackChannel, SlackChannelSyncSetting, SlackMessage, SlackReaction
from .slack_api import SlackClient

TICKET = re.compile(r"^ACTION-(\d{3})\b", re.IGNORECASE)
REPOST = re.compile(r"^\[EFDS archive repost: ACTION-(\d{3})\]", re.IGNORECASE)
CHECK = "white_check_mark"
OPEN = "x"
REPOST_INTERVAL_SECONDS = 21 * 24 * 60 * 60


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
    if not setting.last_successful_sync_at:
        raise ValueError("Sync #actions-tickets successfully before reposting")

    originals: dict[str, SlackMessage] = {}
    rows = session.scalars(select(SlackMessage).where(
        SlackMessage.channel_id == channel_id,
        SlackMessage.is_deleted.is_(False),
    )).all()
    for row in rows:
        match = TICKET.match(row.message_text or "")
        if match and (match.group(1) not in originals or float(row.slack_ts) > float(originals[match.group(1)].slack_ts)):
            originals[match.group(1)] = row

    if not originals:
        return []
    original_reactions: dict[str, set[str]] = {}
    ids = [row.id for row in originals.values()]
    for reaction in session.scalars(select(SlackReaction).where(SlackReaction.message_id.in_(ids))).all():
        original_reactions.setdefault(str(reaction.message_id), set()).add(reaction.name.lower())

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

    clock = time.time() if now is None else now
    candidates = []
    for ticket_id, source in sorted(originals.items()):
        related_reposts = reposts.get(ticket_id, [])
        current = live_originals.get(ticket_id)
        archived_status = original_reactions.get(str(source.id), set())
        live_status = reaction_names(current) if current else set()
        source_status = live_status if current else archived_status
        # A check on any visible version wins. When Slack has hidden the
        # original, its last archived check still prevents resurrection.
        if CHECK in source_status or any(CHECK in reaction_names(row) for row in related_reposts):
            continue
        if OPEN not in source_status and not related_reposts:
            continue
        if current and not source_status and not any(OPEN in reaction_names(row) for row in related_reposts):
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
    return (f"[EFDS archive repost: {ticket.ticket_id}]\n"
            f":x: Still open. Reposted by the EFDS bot so this ticket stays visible. "
            f"React with :white_check_mark: on this copy when complete.\n"
            f"{source}\n\n{ticket.original_text}")


def execute_reposts(client: SlackClient, channel_id: str, candidates: list[TicketCandidate], *, sleep=time.sleep) -> list[tuple[str, str]]:
    posted = []
    for index, ticket in enumerate(candidates):
        if index:
            sleep(1.1)
        timestamp = client.post_message(channel_id, repost_text(ticket))
        posted.append((ticket.ticket_id, timestamp))
    return posted
