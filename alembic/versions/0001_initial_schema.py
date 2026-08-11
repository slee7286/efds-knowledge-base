"""Create the initial EFDS institutional memory schema.

Revision ID: 0001_initial_schema
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

jsonb = postgresql.JSONB(astext_type=sa.Text())
uuid = postgresql.UUID(as_uuid=True)


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "officers",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("academic_year", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", "role", "academic_year", name="uq_officer_identity"),
    )

    op.create_table(
        "documents",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text()),
        sa.Column("source_type", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("file_path", sa.Text()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("academic_year", sa.Text()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_source_type", "documents", ["source_type"])

    op.create_table(
        "meetings",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("meeting_type", sa.Text()),
        sa.Column("meeting_date", sa.DateTime(timezone=True)),
        sa.Column("transcript_document_id", uuid, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("minutes_document_id", uuid, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_meetings_meeting_date", "meetings", ["meeting_date"])

    op.create_table(
        "meeting_attendees",
        sa.Column("meeting_id", uuid, sa.ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("officer_id", uuid, sa.ForeignKey("officers.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "decisions",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", uuid, sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("decision_text", sa.Text(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "action_items",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", uuid, sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("owner_id", uuid, sa.ForeignKey("officers.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("due_date", sa.Date()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'not_started'")),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_action_items_owner_id", "action_items", ["owner_id"])
    op.create_index("ix_action_items_status", "action_items", ["status"])

    op.create_table(
        "slack_channels",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )

    op.create_table(
        "slack_messages",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slack_ts", sa.Text(), nullable=False, unique=True),
        sa.Column("channel_id", sa.Text(), sa.ForeignKey("slack_channels.id", ondelete="SET NULL")),
        sa.Column("user_slack_id", sa.Text()),
        sa.Column("thread_ts", sa.Text()),
        sa.Column("message_text", sa.Text()),
        sa.Column("raw_event", jsonb),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_slack_messages_channel_id", "slack_messages", ["channel_id"])
    op.create_index("ix_slack_messages_thread_ts", "slack_messages", ["thread_ts"])

    op.create_table(
        "knowledge_articles",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("external_id", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), unique=True),
        sa.Column("category", sa.Text()),
        sa.Column("folder", sa.Text()),
        sa.Column("raw_html", sa.Text()),
        sa.Column("markdown", sa.Text()),
        sa.Column("content_hash", sa.Text()),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )
    op.create_index("ix_knowledge_articles_url", "knowledge_articles", ["url"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text()),
        sa.Column("records_seen", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_created", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_log", jsonb, nullable=False, server_default=_json_default("[]")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_index("ix_knowledge_articles_url", table_name="knowledge_articles")
    op.drop_table("knowledge_articles")
    op.drop_index("ix_slack_messages_thread_ts", table_name="slack_messages")
    op.drop_index("ix_slack_messages_channel_id", table_name="slack_messages")
    op.drop_table("slack_messages")
    op.drop_table("slack_channels")
    op.drop_index("ix_action_items_status", table_name="action_items")
    op.drop_index("ix_action_items_owner_id", table_name="action_items")
    op.drop_table("action_items")
    op.drop_table("decisions")
    op.drop_table("meeting_attendees")
    op.drop_index("ix_meetings_meeting_date", table_name="meetings")
    op.drop_table("meetings")
    op.drop_index("ix_documents_source_type", table_name="documents")
    op.drop_index("ix_documents_document_type", table_name="documents")
    op.drop_table("documents")
    op.drop_table("officers")

