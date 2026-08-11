"""SQLAlchemy models for the EFDS institutional memory schema."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

JsonType = JSON().with_variant(JSONB, "postgresql")


def uuid_column() -> Mapped[uuid.UUID]:
    """Define a UUID primary key with a PostgreSQL server-side default."""

    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class Officer(Base, TimestampMixin):
    __tablename__ = "officers"
    __table_args__ = (
        UniqueConstraint("name", "role", "academic_year", name="uq_officer_identity"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    academic_year: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    owned_action_items: Mapped[list["ActionItem"]] = relationship(back_populates="owner")
    meetings: Mapped[list["Meeting"]] = relationship(
        secondary="meeting_attendees", back_populates="attendees"
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_document_type", "document_type"),
        Index("ix_documents_source_type", "source_type"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    academic_year: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    transcript_meetings: Mapped[list["Meeting"]] = relationship(
        foreign_keys="Meeting.transcript_document_id", back_populates="transcript_document"
    )
    minutes_meetings: Mapped[list["Meeting"]] = relationship(
        foreign_keys="Meeting.minutes_document_id", back_populates="minutes_document"
    )


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"
    __table_args__ = (Index("ix_meetings_meeting_date", "meeting_date"),)

    id: Mapped[uuid.UUID] = uuid_column()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    meeting_type: Mapped[str | None] = mapped_column(Text)
    meeting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    minutes_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    transcript_document: Mapped[Document | None] = relationship(
        foreign_keys=[transcript_document_id], back_populates="transcript_meetings"
    )
    minutes_document: Mapped[Document | None] = relationship(
        foreign_keys=[minutes_document_id], back_populates="minutes_meetings"
    )
    attendees: Mapped[list[Officer]] = relationship(
        secondary="meeting_attendees", back_populates="meetings"
    )
    decisions: Mapped[list["Decision"]] = relationship(back_populates="meeting")
    action_items: Mapped[list["ActionItem"]] = relationship(back_populates="meeting")


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    officer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("officers.id", ondelete="CASCADE"), primary_key=True
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = uuid_column()
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    decision_text: Mapped[str] = mapped_column(Text, nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    meeting: Mapped[Meeting | None] = relationship(back_populates="decisions")


class ActionItem(Base, TimestampMixin):
    __tablename__ = "action_items"
    __table_args__ = (
        Index("ix_action_items_owner_id", "owner_id"),
        Index("ix_action_items_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("officers.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'not_started'"))
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'medium'"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    meeting: Mapped[Meeting | None] = relationship(back_populates="action_items")
    owner: Mapped[Officer | None] = relationship(back_populates="owned_action_items")


class SlackChannel(Base):
    __tablename__ = "slack_channels"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )


class SlackMessage(Base):
    __tablename__ = "slack_messages"
    __table_args__ = (
        Index("ix_slack_messages_channel_id", "channel_id"),
        Index("ix_slack_messages_thread_ts", "thread_ts"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    slack_ts: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("slack_channels.id", ondelete="SET NULL")
    )
    user_slack_id: Mapped[str | None] = mapped_column(Text)
    thread_ts: Mapped[str | None] = mapped_column(Text)
    message_text: Mapped[str | None] = mapped_column(Text)
    raw_event: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    channel: Mapped[SlackChannel | None] = relationship()


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"
    __table_args__ = (
        Index("ix_knowledge_articles_url", "url"),
        Index("ix_knowledge_articles_source_type", "source_type"),
        UniqueConstraint(
            "source_type", "external_id", name="uq_knowledge_articles_source_external_id"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    source_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, unique=True)
    category: Mapped[str | None] = mapped_column(Text)
    folder: Mapped[str | None] = mapped_column(Text)
    raw_html: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    efds_relevance: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'low'")
    )
    relevance_confidence: Mapped[float | None] = mapped_column(Float)
    relevance_method: Mapped[str | None] = mapped_column(Text)
    relevance_review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'proposed'")
    )
    relevance_evidence: Mapped[str | None] = mapped_column(Text)
    relevance_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_extracted_content_hash: Mapped[str | None] = mapped_column(Text)
    last_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_extraction_runs.id", ondelete="SET NULL")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    changes: Mapped[list["KnowledgeArticleChange"]] = relationship(
        back_populates="knowledge_article", cascade="all, delete-orphan"
    )


class KnowledgeArticleChange(Base):
    __tablename__ = "knowledge_article_changes"
    __table_args__ = (
        Index("ix_knowledge_article_changes_article_id", "knowledge_article_id"),
        Index("ix_knowledge_article_changes_detected_at", "detected_at"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    knowledge_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), nullable=False
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    previous_content_hash: Mapped[str | None] = mapped_column(Text)
    new_content_hash: Mapped[str | None] = mapped_column(Text)
    previous_source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    new_source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fields_changed: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, server_default=text("'[]'::jsonb")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    knowledge_article: Mapped[KnowledgeArticle] = relationship(back_populates="changes")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = uuid_column()
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(Text)
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_log: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, server_default=text("'[]'::jsonb")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )


class KnowledgeTopic(Base, TimestampMixin):
    __tablename__ = "knowledge_topics"

    id: Mapped[uuid.UUID] = uuid_column()
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class KnowledgeRole(Base, TimestampMixin):
    __tablename__ = "knowledge_roles"

    id: Mapped[uuid.UUID] = uuid_column()
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    role_group: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'efds_committee'"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class KnowledgeArticleTopic(Base):
    __tablename__ = "knowledge_article_topics"

    knowledge_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_topics.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'deterministic'"))


class KnowledgeArticleRole(Base):
    __tablename__ = "knowledge_article_roles"

    knowledge_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_roles.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'deterministic'"))


class KnowledgeExtractionRun(Base):
    __tablename__ = "knowledge_extraction_runs"

    id: Mapped[uuid.UUID] = uuid_column()
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'icu_freshdesk'"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deterministic_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    semantic_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    proposed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    model_provider: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(Text)
    error_log: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, server_default=text("'[]'::jsonb")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )


class DerivedKnowledgeMixin:
    """Common source-version, evidence, extraction, and review fields."""

    source_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_extraction_runs.id", ondelete="SET NULL")
    )
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    extraction_method: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'deterministic'"))
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'proposed'"))
    reviewed_by_officer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("officers.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )


class KnowledgeRequirement(Base, TimestampMixin, DerivedKnowledgeMixin):
    __tablename__ = "knowledge_requirements"
    __table_args__ = (
        Index("ix_knowledge_requirements_source", "source_article_id", "source_content_hash"),
        Index("ix_knowledge_requirements_review", "review_status", "is_stale"),
        UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_requirement_version_fingerprint"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_type: Mapped[str] = mapped_column(Text, nullable=False)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_topics.id", ondelete="SET NULL"))
    applies_to: Mapped[str | None] = mapped_column(Text)
    mandatory: Mapped[bool | None] = mapped_column(Boolean)


class KnowledgeRequirementRole(Base):
    __tablename__ = "knowledge_requirement_roles"

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_requirements.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_roles.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeTimingRule(Base, TimestampMixin, DerivedKnowledgeMixin):
    __tablename__ = "knowledge_timing_rules"
    __table_args__ = (
        Index("ix_knowledge_timing_rules_source", "source_article_id", "source_content_hash"),
        Index("ix_knowledge_timing_rules_review", "review_status", "is_stale"),
        UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_timing_version_fingerprint"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    deadline_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    absolute_date: Mapped[date | None] = mapped_column(Date)
    notice_period_value: Mapped[int | None] = mapped_column(Integer)
    notice_period_unit: Mapped[str | None] = mapped_column(Text)
    working_days: Mapped[bool | None] = mapped_column(Boolean)
    recurrence_rule: Mapped[str | None] = mapped_column(Text)
    relative_to_event_type: Mapped[str | None] = mapped_column(Text)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_topics.id", ondelete="SET NULL"))


class KnowledgeProcess(Base, TimestampMixin, DerivedKnowledgeMixin):
    __tablename__ = "knowledge_processes"
    __table_args__ = (
        Index("ix_knowledge_processes_source", "source_article_id", "source_content_hash"),
        Index("ix_knowledge_processes_review", "review_status", "is_stale"),
        UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_process_version_fingerprint"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_topics.id", ondelete="SET NULL"))


class KnowledgeProcessStep(Base, TimestampMixin, DerivedKnowledgeMixin):
    __tablename__ = "knowledge_process_steps"
    __table_args__ = (
        Index("ix_knowledge_process_steps_process_order", "process_id", "step_number"),
        UniqueConstraint("process_id", "step_number", name="uq_process_step_order"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    process_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_processes.id", ondelete="CASCADE"), nullable=False
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str | None] = mapped_column(Text)


class KnowledgeProcessRole(Base):
    __tablename__ = "knowledge_process_roles"

    process_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_processes.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_roles.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeResource(Base, TimestampMixin, DerivedKnowledgeMixin):
    __tablename__ = "knowledge_resources"
    __table_args__ = (
        Index("ix_knowledge_resources_source", "source_article_id", "source_content_hash"),
        Index("ix_knowledge_resources_type", "resource_type"),
        UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_resource_version_fingerprint"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    system_name: Mapped[str | None] = mapped_column(Text)
    anchor_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_topics.id", ondelete="SET NULL"))


class KnowledgeContact(Base, TimestampMixin, DerivedKnowledgeMixin):
    __tablename__ = "knowledge_contacts"
    __table_args__ = (
        Index("ix_knowledge_contacts_email", "email"),
        UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_contact_version_fingerprint"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    name: Mapped[str | None] = mapped_column(Text)
    organisation: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    contact_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'email'"))
    url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class KnowledgeProcessResource(Base):
    __tablename__ = "knowledge_process_resources"

    process_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_processes.id", ondelete="CASCADE"), primary_key=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_resources.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeArticleRelationship(Base):
    __tablename__ = "knowledge_article_relationships"
    __table_args__ = (
        UniqueConstraint("source_article_id", "target_article_id", "relationship_type", name="uq_article_relationship"),
    )

    source_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True
    )
    target_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_type: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    source_content_hash: Mapped[str | None] = mapped_column(Text)
