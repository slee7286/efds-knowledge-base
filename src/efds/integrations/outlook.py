"""Sender-limited Outlook evidence ingestion with provenance and deletion checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import IngestionRun, OutlookMessage, OutlookSyncCheckpoint
from efds.integrations.outlook_graph import OutlookGraphClient, normalize_sender
from efds.retrieval.indexer import rebuild_retrieval_index

SOURCE_TYPE = "outlook_messages"
MAX_BODY_CHARS = 50_000
MAX_RECHECKS_PER_SENDER = 200
MISSING_RECHECK_INTERVAL = timedelta(hours=6)


@dataclass(frozen=True)
class OutlookEvidence:
    graph_message_id: str
    internet_message_id: str | None
    sender_address: str
    subject: str
    body_text: str
    body_truncated: bool
    received_at: datetime
    source_modified_at: datetime | None
    web_link: str | None
    content_hash: str


@dataclass(frozen=True)
class OutlookSyncSummary:
    mailbox_graph_id: str
    senders: int
    seen: int
    rechecked: int
    created: int
    updated: int
    unchanged: int
    missing: int
    deleted: int
    dry_run: bool


def _timestamp(raw: object, *, required: bool) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        if required:
            raise ValueError("Graph message lacks a required timestamp")
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Graph message has an invalid timestamp") from exc
    if value.tzinfo is None:
        raise ValueError("Graph message timestamp lacks a timezone")
    return value.astimezone(timezone.utc)


def _outlook_link(raw: object) -> str | None:
    if not isinstance(raw, str) or len(raw) > 4096:
        return None
    parsed = urlsplit(raw)
    if (parsed.scheme == "https" and parsed.hostname in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"}
            and parsed.username is None and parsed.password is None and parsed.port is None):
        return raw
    return None


def parse_graph_message(payload: dict, allowed_sender: str) -> OutlookEvidence:
    """Reject any message outside the requested exact sender before persistence."""
    sender = normalize_sender(allowed_sender)
    from_value = payload.get("from")
    email_value = from_value.get("emailAddress") if isinstance(from_value, dict) else None
    address = email_value.get("address") if isinstance(email_value, dict) else None
    if not isinstance(address, str) or normalize_sender(address) != sender:
        raise ValueError("Graph returned a message outside the sender allowlist")
    graph_id = payload.get("id")
    if not isinstance(graph_id, str) or not graph_id or len(graph_id) > 2048:
        raise ValueError("Graph message lacks a usable immutable ID")
    received_at = _timestamp(payload.get("receivedDateTime"), required=True)
    assert received_at is not None
    modified_at = _timestamp(payload.get("lastModifiedDateTime"), required=False)
    subject = str(payload.get("subject") or "(No subject)").strip()[:500]
    body = payload.get("body")
    content = body.get("content") if isinstance(body, dict) else None
    content_type = str(body.get("contentType") or "").casefold() if isinstance(body, dict) else ""
    if not isinstance(content, str):
        content = ""
    if content_type == "html":
        content = BeautifulSoup(content, "html.parser").get_text("\n", strip=True)
    normalized_body = content.replace("\r\n", "\n").strip()
    truncated = len(normalized_body) > MAX_BODY_CHARS
    digest = hashlib.sha256(json.dumps([subject, normalized_body], ensure_ascii=False).encode()).hexdigest()
    internet_id = payload.get("internetMessageId")
    return OutlookEvidence(
        graph_message_id=graph_id,
        internet_message_id=internet_id[:1000] if isinstance(internet_id, str) else None,
        sender_address=sender,
        subject=subject,
        body_text=normalized_body[:MAX_BODY_CHARS],
        body_truncated=truncated,
        received_at=received_at,
        source_modified_at=modified_at,
        web_link=_outlook_link(payload.get("webLink")),
        content_hash=digest,
    )


def _match_mailbox(identity: dict, expected_email: str) -> str:
    expected = normalize_sender(expected_email)
    identities = {str(identity.get(field) or "").strip().casefold() for field in ("mail", "userPrincipalName")}
    mailbox_graph_id = identity.get("id")
    if expected not in identities or not isinstance(mailbox_graph_id, str) or not mailbox_graph_id:
        raise ValueError("The delegated Graph identity does not match the configured mailbox")
    return mailbox_graph_id


def _assign(message: OutlookMessage, item: OutlookEvidence, now: datetime) -> bool:
    changed = message.content_hash != item.content_hash or message.is_deleted
    message.internet_message_id = item.internet_message_id
    message.sender_address = item.sender_address
    message.subject = item.subject
    message.body_text = item.body_text
    message.body_truncated = item.body_truncated
    message.received_at = item.received_at
    message.source_modified_at = item.source_modified_at
    message.web_link = item.web_link
    message.content_hash = item.content_hash
    message.is_deleted = False
    message.missing_observations = 0
    message.last_missing_checked_at = None
    message.last_seen_at = now
    return changed


def _recheck_candidates(
    known: list[OutlookMessage], seen_ids: set[str], now: datetime,
) -> list[OutlookMessage]:
    """Rotate bounded point checks, prioritizing a second missing observation."""
    # A successful check refreshes last_seen_at; a 404 refreshes
    # last_missing_checked_at. The later timestamp rotates either result.
    candidates = [
        row for row in known
        if row.graph_message_id not in seen_ids
        and (row.last_missing_checked_at is None
             or now - row.last_missing_checked_at >= MISSING_RECHECK_INTERVAL)
    ]
    candidates.sort(key=lambda row: (
        not (row.missing_observations > 0 and not row.is_deleted),
        max(row.last_seen_at, row.last_missing_checked_at or row.last_seen_at),
        row.graph_message_id,
    ))
    return candidates[:MAX_RECHECKS_PER_SENDER]


def sync_outlook(
    session: Session,
    client: OutlookGraphClient,
    *,
    expected_mailbox_email: str,
    allowed_senders: tuple[str, ...],
    since: datetime | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> OutlookSyncSummary:
    """Fetch fully before writing; only two exact senders may enter the archive."""
    senders = tuple(normalize_sender(sender) for sender in allowed_senders)
    if len(senders) != 2 or len(set(senders)) != 2:
        raise ValueError("Configure exactly two distinct allowed Outlook senders")
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    now = now or datetime.now(timezone.utc)
    mailbox_graph_id = _match_mailbox(client.identity(), expected_mailbox_email)
    fetched: dict[str, list[OutlookEvidence]] = {}
    checkpoints: dict[str, OutlookSyncCheckpoint | None] = {}
    known: dict[str, list[OutlookMessage]] = {}
    inspected: dict[str, dict[str, OutlookEvidence | None]] = {}

    for sender in senders:
        checkpoint = session.get(OutlookSyncCheckpoint, (mailbox_graph_id, sender))
        checkpoints[sender] = checkpoint
        lower_bound = since or (
            checkpoint.last_received_at - timedelta(days=3)
            if checkpoint and checkpoint.last_received_at else now - timedelta(days=90)
        )
        fetched[sender] = [parse_graph_message(item, sender) for item in client.iter_sender_messages(sender, lower_bound)]
        known[sender] = list(session.scalars(select(OutlookMessage).where(
            OutlookMessage.mailbox_graph_id == mailbox_graph_id,
            OutlookMessage.sender_address == sender,
        )).all())
        seen_ids = {item.graph_message_id for item in fetched[sender]}
        inspected[sender] = {}
        for row in _recheck_candidates(known[sender], seen_ids, now):
            payload = client.get_message(row.graph_message_id)
            inspected[sender][row.graph_message_id] = parse_graph_message(payload, sender) if payload is not None else None

    rechecked = sum(map(len, inspected.values()))
    created = updated = unchanged = missing = deleted = 0
    if dry_run:
        return OutlookSyncSummary(mailbox_graph_id, len(senders), sum(map(len, fetched.values())),
                                  rechecked, 0, 0, 0, 0, 0, True)

    run = IngestionRun(source_type=SOURCE_TYPE, source_path=None, status="running",
                       metadata_={"mailbox_graph_id": mailbox_graph_id, "sender_count": len(senders),
                                  "rechecked": rechecked})
    session.add(run)
    session.flush()
    for sender in senders:
        existing = {row.graph_message_id: row for row in known[sender]}
        for item in [*fetched[sender], *(value for value in inspected[sender].values() if value is not None)]:
            row = existing.get(item.graph_message_id)
            if row is None:
                row = OutlookMessage(mailbox_graph_id=mailbox_graph_id, graph_message_id=item.graph_message_id,
                                     sender_address=sender, subject=item.subject, body_text=item.body_text,
                                     received_at=item.received_at, content_hash=item.content_hash)
                session.add(row)
                existing[item.graph_message_id] = row
                created += 1
            elif _assign(row, item, now):
                updated += 1
                continue
            else:
                unchanged += 1
                continue
            _assign(row, item, now)
        for row in known[sender]:
            if row.graph_message_id not in inspected[sender] or inspected[sender][row.graph_message_id] is not None:
                continue
            row.last_missing_checked_at = now
            if row.is_deleted:
                continue
            row.missing_observations += 1
            missing += 1
            if row.missing_observations >= 2:
                row.is_deleted = True
                deleted += 1
        checkpoint = checkpoints[sender]
        if checkpoint is None:
            checkpoint = OutlookSyncCheckpoint(mailbox_graph_id=mailbox_graph_id, sender_address=sender)
            session.add(checkpoint)
        received = [item.received_at for item in fetched[sender]]
        checkpoint.last_received_at = max([*received, *([checkpoint.last_received_at] if checkpoint.last_received_at else [])], default=checkpoint.last_received_at)
        checkpoint.last_successful_at = now

    session.flush()
    rebuild_retrieval_index(session, source="outlook_message")
    run.status = "completed"
    run.finished_at = now
    run.records_seen = sum(map(len, fetched.values()))
    run.records_created = created
    run.records_updated = updated
    run.records_skipped = unchanged
    return OutlookSyncSummary(mailbox_graph_id, len(senders), run.records_seen,
                              rechecked, created, updated, unchanged, missing, deleted, False)
