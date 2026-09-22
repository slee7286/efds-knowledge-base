"""Read Google Docs linked from an enabled Slack meeting channel into private notes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from sqlalchemy import select
from efds.db.models import (IngestionRun, Meeting, MeetingArtifact, MeetingSourceChange,
                            SlackChannel, SlackChannelSyncSetting, SlackMessage)

SOURCE_TYPE = "google_docs_meetings"
DOC_PATTERN = re.compile(r'https://docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)(?=[/?#\s<>|"\\]|$)')
MAX_BYTES = 10 * 1024 * 1024


@dataclass
class DocumentLink:
    document_id: str
    references: list[dict] = field(default_factory=list)

    @property
    def url(self):
        return f"https://docs.google.com/document/d/{self.document_id}/edit"


def discover_documents(session, channel_id):
    channel = session.get(SlackChannel, channel_id)
    setting = session.get(SlackChannelSyncSetting, channel_id)
    if not channel or not setting or not setting.enabled:
        raise ValueError("Meeting channel must be discovered and explicitly enabled for Slack archival")
    documents = {}
    messages = session.scalars(select(SlackMessage).where(
        SlackMessage.channel_id == channel_id, SlackMessage.is_deleted.is_(False)
    ).order_by(SlackMessage.slack_ts)).all()
    for message in messages:
        # Rich blocks, unfurls and shared-file metadata can contain links absent from text.
        text = (message.message_text or "") + " " + json.dumps(message.raw_event or {})
        for document_id in sorted(set(DOC_PATTERN.findall(text))):
            doc = documents.setdefault(document_id, DocumentLink(document_id))
            doc.references.append({"message_id": str(message.id), "channel_id": channel_id,
                                   "channel_name": channel.name, "slack_ts": message.slack_ts,
                                   "permalink": message.permalink})
    return list(documents.values())


def fetch_document(document_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', document_id):
        raise ValueError("Invalid Google document ID")
    request = Request(f"https://docs.google.com/document/d/{document_id}/export?format=txt",
                      headers={"Accept": "text/plain", "User-Agent": "EFDS-meeting-archive/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            host = urlsplit(response.geturl()).hostname or ""
            if host == "accounts.google.com" or response.headers.get_content_type() != "text/plain":
                raise ValueError("Document requires Google access or did not return a plain-text export")
            content = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        raise ValueError(f"Google Docs export returned HTTP {error.code}; check document access") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Google Docs export could not be reached") from None
    if len(content) > MAX_BYTES:
        raise ValueError("Document exceeds the 10 MiB export limit")
    text = content.decode("utf-8-sig").replace("\r\n", "\n").strip()
    if not text or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise ValueError("Document export was empty or HTML, not meeting notes")
    return text


def save_document(session, doc, content, run_id):
    """Version notes without duplicating an earlier version if the source reverts."""
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(content.encode()).hexdigest()
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    title = next((line for line in lines if "quick notes" not in line.casefold()), lines[0])[:240]
    meeting = session.scalar(select(Meeting).where(
        Meeting.source_type == SOURCE_TYPE, Meeting.external_meeting_id == doc.document_id))
    created = meeting is None
    if created:
        # Slack posting time is provenance, not an invented meeting date.
        meeting = Meeting(title=title, source_type=SOURCE_TYPE, external_meeting_id=doc.document_id,
                          metadata_={"source_url": doc.url, "slack_references": doc.references})
        session.add(meeting)
        session.flush()
    current = session.scalar(select(MeetingArtifact).where(
        MeetingArtifact.meeting_id == meeting.id, MeetingArtifact.artifact_type == "notes",
        MeetingArtifact.is_current.is_(True)))
    meeting.last_seen_at = now
    meeting.title = title
    meeting.is_missing = False
    meeting.metadata_ = {**(meeting.metadata_ or {}), "source_url": doc.url, "slack_references": doc.references}
    if current and current.content_hash == digest:
        return "unchanged"
    previous_hash = current.content_hash if current else None
    if current:
        current.is_current = False
    version = session.scalar(select(MeetingArtifact).where(
        MeetingArtifact.meeting_id == meeting.id, MeetingArtifact.artifact_type == "notes",
        MeetingArtifact.content_hash == digest))
    if version:
        version.is_current = True
    else:
        session.add(MeetingArtifact(meeting_id=meeting.id, artifact_type="notes", content=content,
                    content_hash=digest, source_record_id=doc.document_id, source_reference=doc.url,
                    format="text", review_status="source_generated", metadata_={"slack_references": doc.references}))
    meeting.last_changed_at = now
    session.add(MeetingSourceChange(meeting_id=meeting.id, change_type="created" if created else "notes_updated",
                previous_hash=previous_hash, new_hash=digest, ingestion_run_id=run_id,
                metadata_={"source_url": doc.url}))
    session.flush()
    return "created" if created else "updated"


def sync_google_docs_meetings(session, channel_id, *, dry_run=False, fetch=fetch_document):
    documents = discover_documents(session, channel_id)
    summary = {"source_type": SOURCE_TYPE, "channel_id": channel_id, "documents_found": len(documents),
               "created": 0, "updated": 0, "unchanged": 0, "fetched": 0, "errors": [], "documents": []}
    run = None
    if not dry_run:
        run = IngestionRun(source_type=SOURCE_TYPE, source_path=channel_id, status="running")
        session.add(run)
        session.commit()
        summary["run_id"] = str(run.id)
    for doc in documents:
        try:
            content = fetch(doc.document_id)
            if dry_run:
                outcome = "fetched"
            else:
                with session.begin_nested():
                    outcome = save_document(session, doc, content, run.id)
                session.commit()
            summary[outcome] += 1
            summary["documents"].append({"document_id": doc.document_id, "url": doc.url,
                                         "status": outcome, "characters": len(content)})
        except Exception as error:
            summary["errors"].append({"document_id": doc.document_id,
                "error": str(error) if isinstance(error, ValueError) else type(error).__name__})
    summary["status"] = "completed_with_errors" if summary["errors"] else "completed_dry_run" if dry_run else "completed"
    if run:
        run.status = summary["status"]
        run.finished_at = datetime.now(timezone.utc)
        run.records_seen = len(documents)
        run.records_created = summary["created"]
        run.records_updated = summary["updated"]
        run.records_skipped = summary["unchanged"]
        run.records_failed = len(summary["errors"])
        run.metadata_ = summary
        run.error_log = summary["errors"]
        session.commit()
    return summary
