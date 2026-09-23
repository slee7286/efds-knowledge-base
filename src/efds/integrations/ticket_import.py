"""One-time, source-linked import of archived #actions-tickets messages."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import (
    Officer, OperationalRecord, OperationalRecordEvidence, OperationalTicketAssignee,
    RetrievalUnit, SlackChannel, SlackChannelSyncSetting, SlackMessage, SlackReaction,
)

HEADER = re.compile(r"^(ACTION-\d{3})\s*[—–-]\s*(.+)$", re.IGNORECASE)
FIELDS = ("People Assigned", "Due", "Priority", "Status")
WORKSTREAMS = {
    **{number: "Operations & governance" for number in (1, 2, 4, 5, 9, 12, 13, 16, 17, 22, 23)},
    **{number: "Events" for number in (3, 6, 7, 8, 18, 19, 20, 21)},
    10: "Industry & partnerships", 11: "Communications", 14: "Mentorship", 15: "Research",
}


@dataclass(frozen=True)
class TicketDraft:
    code: str
    title: str
    description: str
    owner_text: str | None
    due_text: str | None
    priority: str
    status: str
    workstream: str | None
    officer_ids: tuple[UUID, ...]


def parse_ticket(text: str, reactions: set[str], officers: list[Officer]) -> TicketDraft | None:
    lines = text.splitlines()
    if not lines or not (match := HEADER.match(html.unescape(lines[0].strip()))):
        return None
    code, title = match.group(1).upper(), match.group(2).strip()
    fields: dict[str, str] = {}
    body_start = 1
    for index, line in enumerate(lines[1:], start=1):
        key, separator, value = line.partition(":")
        if separator and key.strip() in FIELDS:
            fields[key.strip()] = html.unescape(value.strip())
            body_start = index + 1
        elif line.strip() and len(fields) >= 3:
            break
    description = html.unescape("\n".join(lines[body_start:]).strip())
    owner_text = fields.get("People Assigned") or None
    priority = fields.get("Priority", "medium").lower()
    priority = priority if priority in {"low", "medium", "high", "urgent"} else "medium"
    status_text = fields.get("Status", "").lower()
    status = "completed" if "white_check_mark" in reactions else "in_progress" if "in progress" in status_text else "open"
    assigned: list[Officer]
    if owner_text and re.search(r"\ball committee\b|\beveryone\b", owner_text, re.IGNORECASE):
        assigned = officers
    else:
        owner_words = (owner_text or "").casefold()
        assigned = [officer for officer in officers if re.search(rf"\b{re.escape(officer.name.split()[0].casefold())}\b", owner_words)]
        nik = next((officer for officer in officers if officer.name.startswith("Nikodem ")), None)
        if nik and re.search(r"\bnik(?:o)?\b", owner_words) and nik not in assigned:
            assigned.append(nik)
    return TicketDraft(
        code=code, title=f"{code} — {title}", description=description,
        owner_text=owner_text, due_text=fields.get("Due") or None,
        priority=priority, status=status,
        workstream=WORKSTREAMS.get(int(code[-3:])),
        officer_ids=tuple(officer.id for officer in assigned),
    )


def import_archived_tickets(session: Session, *, dry_run: bool = True) -> dict[str, object]:
    channels = session.scalars(select(SlackChannel).where(SlackChannel.name == "actions-tickets")).all()
    if len(channels) != 1:
        raise ValueError("Expected exactly one #actions-tickets archive channel")
    channel = channels[0]
    setting = session.get(SlackChannelSyncSetting, channel.id)
    if not setting or not setting.enabled or not setting.last_successful_sync_at:
        raise ValueError("Synchronise the enabled #actions-tickets channel before importing")
    officers = session.scalars(select(Officer).where(Officer.active.is_(True), Officer.academic_year == "2026-27").order_by(Officer.name)).all()
    messages = session.scalars(select(SlackMessage).where(
        SlackMessage.channel_id == channel.id, SlackMessage.is_deleted.is_(False),
        SlackMessage.parent_message_id.is_(None),
    ).order_by(SlackMessage.source_posted_at)).all()
    message_ids = [message.id for message in messages]
    reactions: dict[UUID, set[str]] = {}
    if message_ids:
        for reaction in session.scalars(select(SlackReaction).where(SlackReaction.message_id.in_(message_ids))):
            reactions.setdefault(reaction.message_id, set()).add(reaction.name.lower())
    drafts: list[tuple[SlackMessage, TicketDraft, RetrievalUnit]] = []
    for message in messages:
        draft = parse_ticket(message.message_text or "", reactions.get(message.id, set()), officers)
        if not draft:
            continue
        unit = session.scalars(select(RetrievalUnit).where(
            RetrievalUnit.source_type == "slack_message", RetrievalUnit.source_record_id == str(message.id),
            RetrievalUnit.is_current.is_(True), RetrievalUnit.is_deleted.is_(False),
        ).order_by(RetrievalUnit.chunk_index).limit(1)).first()
        if not unit:
            raise ValueError(f"Retrieval evidence is missing for {draft.code}")
        drafts.append((message, draft, unit))
    existing = {
        str(row.metadata_.get("slack_message_id")) for row in session.scalars(
            select(OperationalRecord).where(OperationalRecord.record_type == "action_item")
        ) if row.metadata_.get("slack_message_id")
    }
    pending = [(message, draft, unit) for message, draft, unit in drafts if str(message.id) not in existing]
    if not dry_run:
        for message, draft, unit in pending:
            record = OperationalRecord(
                record_type="action_item", title=draft.title, description=draft.description or None,
                owner_officer_id=draft.officer_ids[0] if draft.officer_ids else None,
                owner_text=draft.owner_text, due_text=draft.due_text,
                occurred_at=message.source_posted_at, workstream=draft.workstream,
                priority=draft.priority, execution_status=draft.status,
                review_status="approved", visibility="committee",
                metadata_={"origin": "slack_actions_tickets", "slack_ticket_code": draft.code,
                           "slack_message_id": str(message.id), "source_content_hash": message.content_hash},
            )
            session.add(record)
            session.flush()
            session.add(OperationalRecordEvidence(
                operational_record_id=record.id, retrieval_unit_id=unit.id,
                source_type="slack_message", source_record_id=str(message.id),
                source_version_id=unit.source_version_id, evidence_role="primary",
                evidence_text=draft.title, metadata_={"title": unit.title},
            ))
            for officer_id in draft.officer_ids:
                session.add(OperationalTicketAssignee(ticket_id=record.id, officer_id=officer_id))
    return {
        "dry_run": dry_run, "source_tickets": len(drafts), "already_imported": len(drafts) - len(pending),
        "to_import": len(pending), "imported": 0 if dry_run else len(pending),
        "open": sum(draft.status != "completed" for _, draft, _ in drafts),
        "completed": sum(draft.status == "completed" for _, draft, _ in drafts),
        "codes": [draft.code for _, draft, _ in pending],
    }
