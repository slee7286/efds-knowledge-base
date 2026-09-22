"""Deterministic source-to-retrieval-unit builders and rebuild operation."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import (
    Document, DocumentVersion, KnowledgeArticle, KnowledgeContact, KnowledgeProcess,
    KnowledgeProcessStep, KnowledgeRequirement, KnowledgeResource, KnowledgeTimingRule,
    Meeting, MeetingArtifact, MeetingTranscriptSegment, RetrievalUnit, SlackChannel,
    SlackChannelSyncSetting, SlackMessage, SlackUser, OperationalRecord,
)

from .chunking import chunk_text

RETRIEVAL_SOURCE_TYPES = (
    "icu_article", "knowledge_requirement", "knowledge_timing_rule", "knowledge_process",
    "knowledge_process_step", "knowledge_resource", "knowledge_contact", "document", "slack_message",
    "meeting_transcript", "meeting_summary", "meeting_notes", "operational_decision",
    "operational_action", "operational_commitment", "operational_question", "operational_status",
)


@dataclass(slots=True)
class RetrievalUnitData:
    stable_key: str
    source_type: str
    source_record_id: str
    title: str
    content: str
    content_hash: str
    index_hash: str
    chunk_index: int = 0
    source_parent_id: str | None = None
    source_version_id: str | None = None
    source_area: str | None = None
    topic: str | None = None
    channel: str | None = None
    author: str | None = None
    visibility: str = "internal"
    review_status: str | None = None
    authority: str = "source_record"
    is_current: bool = True
    is_stale: bool = False
    is_deleted: bool = False
    occurred_at: datetime | None = None
    source_updated_at: datetime | None = None
    source_url: str | None = None
    permalink: str | None = None
    relative_path: str | None = None
    metadata: dict[str, Any] | None = None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _unit_key(source_type: str, record_id: Any, version_id: Any, chunk_index: int) -> str:
    return f"{source_type}:{record_id}:{version_id or '-'}:{chunk_index}"


def _unit_id(stable_key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"efds-retrieval:{stable_key}")


def _make_units(*, source_type: str, record_id: Any, version_id: Any, title: str, content: str,
                metadata: dict[str, Any] | None = None, **kwargs: Any) -> list[RetrievalUnitData]:
    pieces = chunk_text(content)
    return [
        RetrievalUnitData(
            stable_key=_unit_key(source_type, record_id, version_id, index), source_type=source_type,
            source_record_id=str(record_id), source_version_id=str(version_id) if version_id else None,
            chunk_index=index, title=title, content=piece,
            content_hash=hashlib.sha256(piece.encode()).hexdigest(),
            index_hash=_hash({"content": piece, "metadata": metadata or {}, **kwargs}),
            metadata=metadata or {}, **kwargs,
        ) for index, piece in enumerate(pieces)
    ]


def build_retrieval_units(session: Session, source: str | None = None) -> list[RetrievalUnitData]:
    """Build current index data from canonical rows only."""

    units: list[RetrievalUnitData] = []
    wanted = set(RETRIEVAL_SOURCE_TYPES if source is None else (source,))
    if "icu_article" in wanted:
        for row in session.scalars(select(KnowledgeArticle)).all():
            units.extend(_make_units(
                source_type="icu_article", record_id=row.id, version_id=row.content_hash,
                title=row.title, content=row.markdown or row.raw_html or "",
                metadata={"category": row.category, "folder": row.folder, "external_id": row.external_id},
                source_area=row.category, visibility="internal", review_status=row.relevance_review_status,
                authority="icu_source", source_updated_at=row.source_updated_at, source_url=row.url,
            ))

    derived_specs = [
        ("knowledge_requirement", KnowledgeRequirement, "requirement_text", "requirement_text"),
        ("knowledge_timing_rule", KnowledgeTimingRule, "description", "description"),
        ("knowledge_process", KnowledgeProcess, "description", "name"),
        ("knowledge_resource", KnowledgeResource, "description", "name"),
        ("knowledge_contact", KnowledgeContact, "description", "name"),
    ]
    for source_type, model, body_field, title_field in derived_specs:
        if source_type not in wanted:
            continue
        for row in session.scalars(select(model)).all():
            body = getattr(row, body_field) or getattr(row, title_field) or ""
            title = getattr(row, title_field) or source_type.replace("_", " ").title()
            units.extend(_make_units(
                source_type=source_type, record_id=row.id, version_id=row.source_content_hash,
                title=title, content=f"{title}\n\n{body}",
                metadata={"evidence": row.evidence_text, "confidence": row.confidence,
                          "source_article_id": str(row.source_article_id), "extraction_method": row.extraction_method},
                source_parent_id=str(row.source_article_id),
                topic=str(row.topic_id) if getattr(row, "topic_id", None) else None,
                visibility=row.visibility, review_status=row.review_status, authority="approved_operational",
                is_stale=row.is_stale, source_updated_at=row.source_updated_at, source_url=row.source_url,
            ))

    if "knowledge_process_step" in wanted:
        for row in session.scalars(select(KnowledgeProcessStep)).all():
            title = row.title or f"Process step {row.step_number}"
            content = f"{title}\n\n{row.instruction}" + (f"\n\nCondition: {row.condition}" if row.condition else "")
            units.extend(_make_units(
                source_type="knowledge_process_step", record_id=row.id, version_id=row.source_content_hash,
                title=title, content=content, source_parent_id=str(row.process_id),
                metadata={"step_number": row.step_number}, visibility=row.visibility,
                review_status=row.review_status, authority="approved_operational", is_stale=row.is_stale,
                source_updated_at=row.source_updated_at, source_url=row.source_url,
            ))

    if "document" in wanted:
        statement = select(Document, DocumentVersion).join(
            DocumentVersion, Document.current_version_id == DocumentVersion.id
        ).where(Document.source_type == "onedrive_filesystem", Document.is_missing.is_(False),
                Document.is_unavailable.is_(False), DocumentVersion.extraction_status == "extracted")
        for document, version in session.execute(statement):
            units.extend(_make_units(
                source_type="document", record_id=document.id, version_id=version.id, title=document.title,
                content=version.raw_text or "", metadata={"mime_type": version.mime_type, "document_type": document.document_type},
                source_area=document.source_area, visibility="internal", authority="governance",
                source_updated_at=version.source_modified_at or document.filesystem_modified_at,
                source_url=document.source_url, relative_path=document.relative_path,
            ))

    if "slack_message" in wanted:
        statement = select(SlackMessage, SlackChannel, SlackUser).join(
            SlackChannel, SlackMessage.channel_id == SlackChannel.id
        ).outerjoin(SlackUser, SlackMessage.author_user_id == SlackUser.id).join(
            SlackChannelSyncSetting, SlackChannelSyncSetting.channel_id == SlackChannel.id
        ).where(SlackChannelSyncSetting.enabled.is_(True), SlackMessage.is_deleted.is_(False))
        for message, channel, user in session.execute(statement):
            units.extend(_make_units(
                source_type="slack_message", record_id=message.id, version_id=message.content_hash,
                title=f"#{channel.name}", content=message.message_text or "",
                metadata={"slack_ts": message.slack_ts, "thread_ts": message.thread_ts, "subtype": message.subtype},
                source_parent_id=channel.id, channel=channel.name,
                author=(user.display_name or user.real_name) if user else None, visibility="internal",
                authority="committee_record", occurred_at=message.source_posted_at,
                source_updated_at=message.source_edited_at, permalink=message.permalink,
            ))

    if wanted.intersection({"meeting_transcript", "meeting_summary", "meeting_notes"}):
        statement = select(Meeting, MeetingArtifact).join(
            MeetingArtifact, MeetingArtifact.meeting_id == Meeting.id
        ).where(Meeting.source_type.in_(("meetily", "google_docs_meetings")), Meeting.is_missing.is_(False), MeetingArtifact.is_current.is_(True))
        for meeting, artifact in session.execute(statement):
            source_type = f"meeting_{artifact.artifact_type}"
            if source_type not in wanted:
                continue
            authority = {
                "meeting_transcript": "meeting_transcript",
                "meeting_summary": "meeting_summary",
                "meeting_notes": "meeting_notes",
            }[source_type]
            metadata = {
                "meeting_id": str(meeting.id), "meeting_title": meeting.title, "meeting_source_type": meeting.source_type,
                "artifact_id": str(artifact.id), "artifact_type": artifact.artifact_type,
                "source_reference": artifact.source_reference, "artifact_content_hash": artifact.content_hash,
                "generated_by": artifact.generated_by, "ai_generated": artifact.artifact_type == "summary",
                "summary_template": artifact.summary_template,
            }
            if artifact.artifact_type == "transcript":
                segment_rows = session.scalars(select(MeetingTranscriptSegment).where(MeetingTranscriptSegment.artifact_id == artifact.id).order_by(MeetingTranscriptSegment.sequence)).all()
                if segment_rows:
                    grouped: list[str] = []
                    group_start: int | None = None
                    group_end: int | None = None
                    chunk_index = 0
                    for segment in segment_rows:
                        prefix = f"[{_format_ms(segment.start_ms)}] " if segment.start_ms is not None else ""
                        speaker = f"{segment.speaker}: " if segment.speaker else ""
                        candidate = f"{prefix}{speaker}{segment.text}"
                        if grouped and len("\n".join(grouped)) + len(candidate) + 1 > 2400:
                            content = "\n".join(grouped)
                            units.append(_meeting_unit(meeting, artifact, source_type, authority, content, chunk_index, group_start, group_end, metadata))
                            chunk_index += 1; grouped = []; group_start = None
                        grouped.append(candidate)
                        group_start = segment.start_ms if group_start is None else group_start
                        group_end = segment.end_ms or segment.start_ms
                    if grouped:
                        units.append(_meeting_unit(meeting, artifact, source_type, authority, "\n".join(grouped), chunk_index, group_start, group_end, metadata))
                else:
                    units.extend(_make_units(source_type=source_type, record_id=meeting.id, version_id=artifact.id, title=meeting.title, content=artifact.content, metadata=metadata, source_parent_id=str(meeting.id), source_updated_at=artifact.source_updated_at or meeting.source_updated_at, authority=authority, visibility="internal", review_status=artifact.review_status, occurred_at=meeting.started_at))
            else:
                units.extend(_make_units(source_type=source_type, record_id=meeting.id, version_id=artifact.id, title=f"{meeting.title} — {artifact.artifact_type}", content=artifact.content, source_url=artifact.source_reference if meeting.source_type == "google_docs_meetings" else None, metadata=metadata, source_parent_id=str(meeting.id), source_updated_at=artifact.source_updated_at or meeting.source_updated_at, authority=authority, visibility="internal", review_status=artifact.review_status, occurred_at=meeting.started_at))

    operational_types = {
        "decision": "operational_decision", "action_item": "operational_action",
        "commitment": "operational_commitment", "open_question": "operational_question",
        "status_update": "operational_status",
    }
    wanted_operational = wanted.intersection(operational_types.values())
    if wanted_operational:
        for row in session.scalars(select(OperationalRecord).where(OperationalRecord.is_current.is_(True))).all():
            source_type = operational_types[row.record_type]
            if source_type not in wanted_operational:
                continue
            owner = row.owner_text or ""
            content = "\n\n".join(value for value in (
                row.title, row.description or "", f"Workstream: {row.workstream}" if row.workstream else "",
                f"Owner: {owner}" if owner else "", f"Due: {row.due_text or row.due_at}" if row.due_text or row.due_at else "",
                f"Status: {row.execution_status}" if row.execution_status else "",
            ) if value)
            units.extend(_make_units(
                source_type=source_type, record_id=row.id, version_id=str(row.review_version),
                title=row.title, content=content,
                metadata={"record_type": row.record_type, "execution_status": row.execution_status,
                          "review_status": row.review_status, "owner_profile_id": str(row.owner_profile_id) if row.owner_profile_id else None,
                          "owner_officer_id": str(row.owner_officer_id) if row.owner_officer_id else None,
                          "due_text": row.due_text, "due_at": row.due_at},
                visibility=row.visibility, review_status=row.review_status,
                authority="approved_operational" if row.review_status == "approved" else "operational_candidate",
                is_stale=not row.is_current, source_updated_at=row.updated_at, occurred_at=row.occurred_at,
            ))
    return units


def _format_ms(value: int | None) -> str:
    if value is None:
        return ""
    seconds = max(value, 0) // 1000
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _meeting_unit(meeting: Meeting, artifact: MeetingArtifact, source_type: str, authority: str, content: str, chunk_index: int, start_ms: int | None, end_ms: int | None, metadata: dict[str, Any]) -> RetrievalUnitData:
    enriched = {**metadata, "start_ms": start_ms, "end_ms": end_ms}
    return RetrievalUnitData(
        stable_key=_unit_key(source_type, meeting.id, f"{artifact.id}:{chunk_index}", 0),
        source_type=source_type, source_record_id=str(meeting.id), source_parent_id=str(meeting.id),
        source_version_id=str(artifact.id), chunk_index=chunk_index, title=meeting.title, content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(), index_hash=_hash({"content": content, "metadata": enriched}),
        visibility="internal", review_status=artifact.review_status, authority=authority,
        occurred_at=meeting.started_at, source_updated_at=artifact.source_updated_at or meeting.source_updated_at,
        metadata=enriched,
    )


def rebuild_retrieval_index(session: Session, *, source: str | None = None, dry_run: bool = False) -> dict[str, int]:
    desired = build_retrieval_units(session, source)
    desired_keys = {item.stable_key for item in desired}
    existing = {row.stable_key: row for row in session.scalars(select(RetrievalUnit)).all()}
    stats = {"discovered": len(desired), "created": 0, "updated": 0, "unchanged": 0, "retired": 0}
    for item in desired:
        row = existing.get(item.stable_key)
        if row is None:
            stats["created"] += 1
            if dry_run:
                continue
            row = RetrievalUnit(id=_unit_id(item.stable_key), stable_key=item.stable_key)
            session.add(row)
        elif row.index_hash == item.index_hash and row.is_current and not row.is_deleted:
            stats["unchanged"] += 1
            continue
        else:
            stats["updated"] += 1
        if dry_run:
            continue
        for field in ("source_type", "source_record_id", "source_parent_id", "source_version_id", "chunk_index",
                      "title", "content", "content_hash", "index_hash", "source_area", "topic", "channel",
                      "author", "visibility", "review_status", "authority", "is_current", "is_stale",
                      "is_deleted", "occurred_at", "source_updated_at", "source_url", "permalink", "relative_path"):
            setattr(row, field, getattr(item, field))
        row.metadata_ = item.metadata or {}
        session.flush()
    if not dry_run:
        for row in existing.values():
            if (source is None or row.source_type == source) and row.is_current and row.stable_key not in desired_keys:
                row.is_current = False
                stats["retired"] += 1
    return stats
