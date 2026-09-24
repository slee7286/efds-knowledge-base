"""SQLAlchemy models for the EFDS institutional memory schema."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
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


class Profile(Base, TimestampMixin):
    """EFDS application profile mapped to a Supabase Auth user.

    ``auth_user_id`` intentionally has no managed foreign key to ``auth.users``.
    Supabase owns that schema; this repository only stores the stable UUID.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(
            "email = lower(email)", name="ck_profiles_email_lowercase"
        ),
        CheckConstraint(
            "member_type IN ('imperial', 'external', 'alumni', 'departmental_representative', 'other')",
            name="ck_profiles_member_type",
        ),
        CheckConstraint(
            "access_role IN ('viewer', 'member', 'efds_member', 'committee', 'admin')",
            name="ck_profiles_access_role",
        ),
        CheckConstraint(
            "efds_verification_status IN ('pending', 'approved', 'declined')",
            name="profiles_efds_verification_status_check",
        ),
        CheckConstraint("length(efds_verification_claim) <= 500", name="profiles_efds_verification_claim_check"),
        CheckConstraint("access_version > 0", name="profiles_access_version_check"),
        CheckConstraint(
            "avatar_path IS NULL OR ("
            "avatar_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\\.webp$' "
            "AND split_part(avatar_path, '/', 1) = auth_user_id::text)",
            name="ck_profiles_avatar_owner",
        ),
        Index("ix_profiles_auth_user_id", "auth_user_id"),
        Index("ix_profiles_access_role", "access_role"),
        Index("ix_profiles_officer_id", "officer_id"),
        Index("profiles_active_officer_identity", "officer_id", unique=True, postgresql_where=text("officer_id IS NOT NULL AND active")),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    auth_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    full_name: Mapped[str | None] = mapped_column(Text)
    avatar_path: Mapped[str | None] = mapped_column(Text)
    member_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'imperial'")
    )
    access_role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'member'")
    )
    efds_verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    efds_verification_claim: Mapped[str | None] = mapped_column(Text)
    efds_verified_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL")
    )
    efds_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    officer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("officers.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    officer: Mapped[Officer | None] = relationship()
    created_exceptions: Mapped[list["AuthAccessException"]] = relationship(
        back_populates="created_by_profile", foreign_keys="AuthAccessException.created_by_profile_id"
    )


class AccountAccessEvent(Base):
    """Immutable record of a reviewed EFDS membership or access decision."""

    __tablename__ = "account_access_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('verify', 'decline', 'promote_committee', 'promote_admin', 'demote_member', 'link_officer')",
            name="account_access_events_action_check",
        ),
        Index("account_access_events_target_time", "target_profile_id", text("occurred_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    target_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    actor_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    previous_role: Mapped[str] = mapped_column(Text, nullable=False)
    new_role: Mapped[str] = mapped_column(Text, nullable=False)
    previous_verification_status: Mapped[str] = mapped_column(Text, nullable=False)
    new_verification_status: Mapped[str] = mapped_column(Text, nullable=False)
    previous_officer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    new_officer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class AuthAccessException(Base, TimestampMixin):
    """Explicit access allowlist for non-Imperial identities."""

    __tablename__ = "auth_access_exceptions"
    __table_args__ = (
        CheckConstraint(
            "email = lower(email)", name="ck_auth_access_exceptions_email_lowercase"
        ),
        CheckConstraint(
            "access_role IN ('viewer', 'member', 'efds_member', 'committee', 'admin')",
            name="ck_auth_access_exceptions_access_role",
        ),
        CheckConstraint(
            "member_type IS NULL OR member_type IN ('imperial', 'external', 'alumni', 'departmental_representative', 'other')",
            name="ck_auth_access_exceptions_member_type",
        ),
        Index("ix_auth_access_exceptions_active_expiry", "active", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    access_role: Mapped[str] = mapped_column(Text, nullable=False)
    member_type: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_by_profile: Mapped[Profile | None] = relationship(
        back_populates="created_exceptions", foreign_keys=[created_by_profile_id]
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_document_type", "document_type"),
        Index("ix_documents_source_type", "source_type"),
        Index("ix_documents_onedrive_area", "source_type", "source_area"),
        Index("ix_documents_onedrive_status", "source_type", "is_missing", "is_unavailable"),
        Index("ix_documents_onedrive_hash", "source_type", "content_hash"),
        UniqueConstraint(
            "source_type",
            "source_root",
            "normalized_relative_path",
            name="uq_documents_source_path",
        ),
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
    content_hash: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_root: Mapped[str | None] = mapped_column(Text)
    relative_path: Mapped[str | None] = mapped_column(Text)
    normalized_relative_path: Mapped[str | None] = mapped_column(Text)
    source_area: Mapped[str | None] = mapped_column(Text)
    filesystem_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filesystem_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_unavailable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    extraction_status: Mapped[str | None] = mapped_column(Text)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", foreign_keys="DocumentVersion.document_id"
    )
    current_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )

    transcript_meetings: Mapped[list["Meeting"]] = relationship(
        foreign_keys="Meeting.transcript_document_id", back_populates="transcript_document"
    )
    minutes_meetings: Mapped[list["Meeting"]] = relationship(
        foreign_keys="Meeting.minutes_document_id", back_populates="minutes_document"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uq_document_version_hash"),
        Index("ix_document_versions_document", "document_id", "ingested_at"),
        Index("ix_document_versions_hash", "content_hash"),
        Index("ix_document_versions_extraction", "extraction_status"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )

    document: Mapped[Document] = relationship(
        back_populates="versions", foreign_keys=[document_id]
    )


class DocumentSourceChange(Base):
    __tablename__ = "document_source_changes"
    __table_args__ = (
        Index("ix_document_source_changes_document", "document_id", "detected_at"),
        Index("ix_document_source_changes_type", "change_type", "detected_at"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    previous_path: Mapped[str | None] = mapped_column(Text)
    new_path: Mapped[str | None] = mapped_column(Text)
    previous_content_hash: Mapped[str | None] = mapped_column(Text)
    new_content_hash: Mapped[str | None] = mapped_column(Text)
    previous_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_size: Mapped[int | None] = mapped_column(BigInteger)
    new_size: Mapped[int | None] = mapped_column(BigInteger)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_meeting_date", "meeting_date"),
        Index("ix_meetings_source_identity", "source_type", "external_meeting_id"),
        Index("ix_meetings_started_at", "started_at"),
        Index("ix_meetings_missing", "is_missing"),
        UniqueConstraint("source_type", "external_meeting_id", name="uq_meetings_source_external_id"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    meeting_type: Mapped[str | None] = mapped_column(Text)
    meeting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'"))
    external_meeting_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
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
    artifacts: Mapped[list["MeetingArtifact"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    transcript_segments: Mapped[list["MeetingTranscriptSegment"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    source_changes: Mapped[list["MeetingSourceChange"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")


class MeetingArtifact(Base):
    """Immutable version of a Meetily transcript, summary, or note artifact."""

    __tablename__ = "meeting_artifacts"
    __table_args__ = (
        UniqueConstraint("meeting_id", "artifact_type", "content_hash", name="uq_meeting_artifact_version"),
        Index("ix_meeting_artifacts_current", "meeting_id", "artifact_type", "is_current"),
        Index("ix_meeting_artifacts_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str | None] = mapped_column(Text)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    generated_by: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'source_generated'"))
    summary_template: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=sql_text("'{}'::jsonb"))

    meeting: Mapped[Meeting] = relationship(back_populates="artifacts")
    transcript_segments: Mapped[list["MeetingTranscriptSegment"]] = relationship(back_populates="artifact", cascade="all, delete-orphan")


class MeetingTranscriptSegment(Base):
    __tablename__ = "meeting_transcript_segments"
    __table_args__ = (
        UniqueConstraint("artifact_id", "sequence", name="uq_meeting_transcript_segment_order"),
        Index("ix_meeting_transcript_segments_meeting", "meeting_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meeting_artifacts.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=sql_text("'{}'::jsonb"))

    meeting: Mapped[Meeting] = relationship(back_populates="transcript_segments")
    artifact: Mapped[MeetingArtifact] = relationship(back_populates="transcript_segments")


class MeetingSourceChange(Base):
    __tablename__ = "meeting_source_changes"
    __table_args__ = (
        Index("ix_meeting_source_changes_meeting", "meeting_id", "detected_at"),
        Index("ix_meeting_source_changes_type", "change_type", "detected_at"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(Text)
    new_hash: Mapped[str | None] = mapped_column(Text)
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))

    meeting: Mapped[Meeting] = relationship(back_populates="source_changes")


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


class OperationalRecord(Base, TimestampMixin):
    """Reviewed operational interpretation supported by source evidence."""

    __tablename__ = "operational_records"
    __table_args__ = (
        CheckConstraint("record_type IN ('decision', 'action_item', 'commitment', 'open_question', 'status_update')", name="ck_operational_records_type"),
        CheckConstraint("review_status IN ('proposed', 'approved', 'rejected', 'needs_review', 'superseded')", name="ck_operational_records_review_status"),
        CheckConstraint("execution_status IS NULL OR execution_status IN ('open', 'in_progress', 'blocked', 'completed', 'cancelled', 'answered', 'resolved', 'closed')", name="ck_operational_records_execution_status"),
        CheckConstraint("visibility IN ('internal', 'committee', 'member', 'public')", name="ck_operational_records_visibility"),
        Index("ix_operational_records_type_review", "record_type", "review_status", "is_current"),
        Index("ix_operational_records_execution", "execution_status", "due_at"),
        Index("ix_operational_records_owner", "owner_profile_id", "owner_officer_id"),
        Index("ix_operational_records_workstream", "workstream"),
        Index("ix_operational_records_visibility", "visibility", "review_status", "is_current"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    owner_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    owner_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("officers.id", ondelete="SET NULL"))
    owner_text: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_text: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workstream: Mapped[str | None] = mapped_column(Text)
    execution_status: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'internal'"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    reviewed_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operational_records.id", ondelete="SET NULL"))
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))

    evidence: Mapped[list["OperationalRecordEvidence"]] = relationship(back_populates="operational_record", cascade="all, delete-orphan", foreign_keys="OperationalRecordEvidence.operational_record_id")


class OperationalTicketAssignee(Base):
    __tablename__ = "operational_ticket_assignees"
    __table_args__ = (
        Index("ix_operational_ticket_assignees_officer", "officer_id"),
        Index("ix_operational_ticket_assignees_actor", "assigned_by_profile_id"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operational_records.id", ondelete="CASCADE"), primary_key=True)
    officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("officers.id", ondelete="CASCADE"), primary_key=True)
    assigned_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class OperationalRecordEvidence(Base):
    __tablename__ = "operational_record_evidence"
    __table_args__ = (
        UniqueConstraint("operational_record_id", "retrieval_unit_id", "evidence_role", name="uq_operational_record_evidence_unit_role"),
        Index("ix_operational_record_evidence_record", "operational_record_id"),
        Index("ix_operational_record_evidence_unit", "retrieval_unit_id"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    operational_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operational_records.id", ondelete="CASCADE"), nullable=False)
    retrieval_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("retrieval_units.id", ondelete="RESTRICT"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_version_id: Mapped[str | None] = mapped_column(Text)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    evidence_role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'supporting'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))

    operational_record: Mapped[OperationalRecord] = relationship(back_populates="evidence", foreign_keys=[operational_record_id])


class OperationalReviewEvent(Base):
    __tablename__ = "operational_review_events"
    __table_args__ = (
        Index("ix_operational_review_events_record", "operational_record_id", "created_at"),
        Index("ix_operational_review_events_reviewer", "reviewer_profile_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    operational_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operational_records.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    previous_review_status: Mapped[str | None] = mapped_column(Text)
    new_review_status: Mapped[str | None] = mapped_column(Text)
    reviewer_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")


class SlackWorkspace(Base):
    __tablename__ = "slack_workspaces"
    __table_args__ = (Index("ix_slack_workspaces_last_synced", "last_synced_at"),)

    id: Mapped[uuid.UUID] = uuid_column()
    slack_team_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))


class SlackUser(Base):
    __tablename__ = "slack_users"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slack_user_id", name="uq_slack_user_workspace_identity"),
        Index("ix_slack_users_workspace", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False)
    slack_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    real_name: Mapped[str | None] = mapped_column(Text)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("officers.id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))


class SlackChannel(Base):
    __tablename__ = "slack_channels"
    __table_args__ = (
        Index("ix_slack_channels_workspace", "workspace_id"),
        Index("ix_slack_channels_sync", "last_synced_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slack_workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))


class SlackMessage(Base):
    __tablename__ = "slack_messages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel_id", "slack_ts", name="uq_slack_message_identity"),
        Index("ix_slack_messages_workspace_channel", "workspace_id", "channel_id"),
        Index("ix_slack_messages_thread_ts", "thread_ts"),
        Index("ix_slack_messages_posted_at", "source_posted_at"),
        Index("ix_slack_messages_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slack_workspaces.id", ondelete="CASCADE"))
    slack_ts: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("slack_channels.id", ondelete="SET NULL"))
    user_slack_id: Mapped[str | None] = mapped_column(Text)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slack_users.id", ondelete="SET NULL"))
    thread_ts: Mapped[str | None] = mapped_column(Text)
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slack_messages.id", ondelete="SET NULL"))
    message_text: Mapped[str | None] = mapped_column(Text)
    subtype: Mapped[str | None] = mapped_column(Text)
    source_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permalink: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_event: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    channel: Mapped[SlackChannel | None] = relationship()
    parent: Mapped["SlackMessage | None"] = relationship(remote_side="SlackMessage.id")


class OutlookMessage(Base):
    """Sender-allowlisted, read-only evidence from one delegated mailbox."""

    __tablename__ = "outlook_messages"
    __table_args__ = (
        UniqueConstraint("mailbox_graph_id", "graph_message_id", name="uq_outlook_message_mailbox_graph_id"),
        Index("ix_outlook_messages_mailbox_received", "mailbox_graph_id", "received_at"),
        Index("ix_outlook_messages_sender_received", "sender_address", "received_at"),
        CheckConstraint("sender_address = lower(sender_address)", name="ck_outlook_messages_sender_lowercase"),
        CheckConstraint("missing_observations >= 0", name="ck_outlook_messages_missing_observations"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    mailbox_graph_id: Mapped[str] = mapped_column(Text, nullable=False)
    graph_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    internet_message_id: Mapped[str | None] = mapped_column(Text)
    sender_address: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    web_link: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    missing_observations: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_missing_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class OutlookSyncCheckpoint(Base):
    __tablename__ = "outlook_sync_checkpoints"

    mailbox_graph_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sender_address: Mapped[str] = mapped_column(Text, primary_key=True)
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SlackChannelSyncSetting(Base):
    __tablename__ = "slack_channel_sync_settings"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channels.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    include_threads: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    include_file_metadata: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_message_ts: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")


class SlackReaction(Base):
    __tablename__ = "slack_reactions"
    __table_args__ = (Index("ix_slack_reactions_message", "message_id"),)

    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slack_messages.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    slack_user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slack_users.id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")


class SlackMessageLink(Base):
    __tablename__ = "slack_message_links"
    __table_args__ = (UniqueConstraint("message_id", "normalized_url", name="uq_slack_message_link"), Index("ix_slack_message_links_domain", "domain"))

    id: Mapped[uuid.UUID] = uuid_column()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slack_messages.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))


class SlackFile(Base):
    __tablename__ = "slack_files"

    slack_file_id: Mapped[str] = mapped_column(Text, primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slack_messages.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    file_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    permalink: Mapped[str | None] = mapped_column(Text)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))


class SlackMessageChange(Base):
    __tablename__ = "slack_message_changes"
    __table_args__ = (Index("ix_slack_message_changes_message", "message_id"), Index("ix_slack_message_changes_detected", "detected_at"))

    id: Mapped[uuid.UUID] = uuid_column()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slack_messages.id", ondelete="CASCADE"), nullable=False)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    previous_content_hash: Mapped[str | None] = mapped_column(Text)
    new_content_hash: Mapped[str | None] = mapped_column(Text)
    previous_text: Mapped[str | None] = mapped_column(Text)
    new_text: Mapped[str | None] = mapped_column(Text)
    source_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb"))


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
    reviewed_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'internal'")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL")
    )
    review_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )


class KnowledgeReviewEvent(Base):
    """Append-only audit record for a review or publication transition."""

    __tablename__ = "knowledge_review_events"
    __table_args__ = (
        Index("ix_knowledge_review_events_record", "knowledge_type", "knowledge_record_id"),
        Index("ix_knowledge_review_events_created_at", "created_at"),
        Index("ix_knowledge_review_events_reviewer", "reviewer_profile_id"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    knowledge_type: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    previous_status: Mapped[str | None] = mapped_column(Text)
    new_status: Mapped[str | None] = mapped_column(Text)
    reviewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
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


class RetrievalUnit(Base, TimestampMixin):
    """Derived, permission-filtered search unit over canonical source records.

    ``source_record_id`` is deliberately text rather than a foreign key: the
    canonical sources use both UUID identities (ICU, documents, Slack
    messages) and Slack's text channel identities.  The stable key and
    provenance columns retain the link without introducing a polymorphic
    foreign-key abstraction into the source schema.
    """

    __tablename__ = "retrieval_units"
    __table_args__ = (
        UniqueConstraint("stable_key", name="uq_retrieval_units_stable_key"),
        Index("ix_retrieval_units_source_current", "source_type", "is_current"),
        Index("ix_retrieval_units_visibility_state", "visibility", "review_status", "is_current"),
        Index("ix_retrieval_units_source_record", "source_type", "source_record_id"),
        Index("ix_retrieval_units_source_version", "source_version_id"),
        Index("ix_retrieval_units_area", "source_area"),
        Index("ix_retrieval_units_channel", "channel"),
        Index("ix_retrieval_units_occurred_at", "occurred_at"),
        Index(
            "ix_retrieval_units_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_parent_id: Mapped[str | None] = mapped_column(Text)
    source_version_id: Mapped[str | None] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    index_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_area: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'internal'"))
    review_status: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'source_record'"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(Text)
    relative_path: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, server_default=text("'{}'::jsonb")
    )
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)


class RetrievalEmbedding(Base, TimestampMixin):
    """Versioned derived vector; the PostgreSQL column is supplied by migration."""

    __tablename__ = "retrieval_embeddings"
    __table_args__ = (
        UniqueConstraint("retrieval_unit_id", "provider", "model", "model_version", name="uq_retrieval_embeddings_model"),
        Index("ix_retrieval_embeddings_unit_model", "retrieval_unit_id", "provider", "model", "model_version"),
    )

    id: Mapped[uuid.UUID] = uuid_column()
    retrieval_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("retrieval_units.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[str] = mapped_column(Text, nullable=False)  # raw vector is written with PostgreSQL CAST
