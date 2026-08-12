"""Add the private, source-preserving Slack archive.

Revision ID: 0007_slack_institutional_memory
Revises: 0006_transactional_knowledge_review

The existing slack_channels/slack_messages tables were placeholders from the
initial schema. This migration extends them without changing their names and
adds workspace, users, allowlist/checkpoint, reaction, link, file metadata and
message-history tables. All Slack source tables remain admin-only through RLS.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_slack_institutional_memory"
down_revision: str | None = "0006_transactional_knowledge_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
jsonb = postgresql.JSONB(astext_type=sa.Text())


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


SLACK_PRIVATE_TABLES = (
    "slack_workspaces",
    "slack_users",
    "slack_channel_sync_settings",
    "slack_reactions",
    "slack_message_links",
    "slack_files",
    "slack_message_changes",
)


def _admin_policy(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_select_admin
        ON public.{table} FOR SELECT TO authenticated
        USING (public.has_efds_role('admin'));
        GRANT SELECT ON public.{table} TO authenticated;
        REVOKE ALL ON public.{table} FROM anon;
        """
    )


def upgrade() -> None:
    op.create_table(
        "slack_workspaces",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slack_team_id", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )
    op.create_index("ix_slack_workspaces_last_synced", "slack_workspaces", ["last_synced_at"])

    op.add_column("slack_channels", sa.Column("workspace_id", uuid))
    op.create_foreign_key("fk_slack_channels_workspace", "slack_channels", "slack_workspaces", ["workspace_id"], ["id"], ondelete="CASCADE")
    op.add_column("slack_channels", sa.Column("topic", sa.Text()))
    op.add_column("slack_channels", sa.Column("purpose", sa.Text()))
    op.add_column("slack_channels", sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("slack_channels", sa.Column("source_created_at", sa.DateTime(timezone=True)))
    op.add_column("slack_channels", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("slack_channels", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("slack_channels", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    op.create_index("ix_slack_channels_workspace", "slack_channels", ["workspace_id"])
    op.create_index("ix_slack_channels_sync", "slack_channels", ["last_synced_at"])

    op.create_table(
        "slack_users",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", uuid, sa.ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_user_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column("real_name", sa.Text()),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("officer_id", uuid, sa.ForeignKey("officers.id", ondelete="SET NULL")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.UniqueConstraint("workspace_id", "slack_user_id", name="uq_slack_user_workspace_identity"),
    )
    op.create_index("ix_slack_users_workspace", "slack_users", ["workspace_id"])

    op.execute("ALTER TABLE public.slack_messages DROP CONSTRAINT IF EXISTS slack_messages_slack_ts_key")
    op.add_column("slack_messages", sa.Column("workspace_id", uuid))
    op.create_foreign_key("fk_slack_messages_workspace", "slack_messages", "slack_workspaces", ["workspace_id"], ["id"], ondelete="CASCADE")
    op.add_column("slack_messages", sa.Column("author_user_id", uuid))
    op.create_foreign_key("fk_slack_messages_author", "slack_messages", "slack_users", ["author_user_id"], ["id"], ondelete="SET NULL")
    op.add_column("slack_messages", sa.Column("parent_message_id", uuid))
    op.create_foreign_key("fk_slack_messages_parent", "slack_messages", "slack_messages", ["parent_message_id"], ["id"], ondelete="SET NULL")
    op.add_column("slack_messages", sa.Column("subtype", sa.Text()))
    op.add_column("slack_messages", sa.Column("source_posted_at", sa.DateTime(timezone=True)))
    op.add_column("slack_messages", sa.Column("source_edited_at", sa.DateTime(timezone=True)))
    op.add_column("slack_messages", sa.Column("permalink", sa.Text()))
    op.add_column("slack_messages", sa.Column("content_hash", sa.Text()))
    op.add_column("slack_messages", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("slack_messages", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("slack_messages", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("slack_messages", sa.Column("last_changed_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_slack_message_identity", "slack_messages", ["workspace_id", "channel_id", "slack_ts"])
    op.create_index("ix_slack_messages_workspace_channel", "slack_messages", ["workspace_id", "channel_id"])
    op.create_index("ix_slack_messages_posted_at", "slack_messages", ["source_posted_at"])
    op.create_index("ix_slack_messages_content_hash", "slack_messages", ["content_hash"])

    op.create_table(
        "slack_channel_sync_settings",
        sa.Column("channel_id", sa.Text(), sa.ForeignKey("slack_channels.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("include_threads", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("include_file_metadata", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)),
        sa.Column("newest_message_ts", sa.Text()),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "slack_reactions",
        sa.Column("message_id", uuid, sa.ForeignKey("slack_messages.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("slack_user_id", sa.Text(), primary_key=True),
        sa.Column("user_id", uuid, sa.ForeignKey("slack_users.id", ondelete="SET NULL")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_slack_reactions_message", "slack_reactions", ["message_id"])

    op.create_table(
        "slack_message_links",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", uuid, sa.ForeignKey("slack_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text()),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.UniqueConstraint("message_id", "normalized_url", name="uq_slack_message_link"),
    )
    op.create_index("ix_slack_message_links_domain", "slack_message_links", ["domain"])

    op.create_table(
        "slack_files",
        sa.Column("slack_file_id", sa.Text(), primary_key=True),
        sa.Column("message_id", uuid, sa.ForeignKey("slack_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("file_type", sa.Text()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("permalink", sa.Text()),
        sa.Column("source_created_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )

    op.create_table(
        "slack_message_changes",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", uuid, sa.ForeignKey("slack_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("previous_content_hash", sa.Text()),
        sa.Column("new_content_hash", sa.Text()),
        sa.Column("previous_text", sa.Text()),
        sa.Column("new_text", sa.Text()),
        sa.Column("source_edited_at", sa.DateTime(timezone=True)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ingestion_run_id", uuid, sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )
    op.create_index("ix_slack_message_changes_message", "slack_message_changes", ["message_id"])
    op.create_index("ix_slack_message_changes_detected", "slack_message_changes", ["detected_at"])

    for table in SLACK_PRIVATE_TABLES:
        _admin_policy(table)


def downgrade() -> None:
    for table in reversed(SLACK_PRIVATE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_select_admin ON public.{table}")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM authenticated, anon")

    op.drop_index("ix_slack_message_changes_detected", table_name="slack_message_changes")
    op.drop_index("ix_slack_message_changes_message", table_name="slack_message_changes")
    op.drop_table("slack_message_changes")
    op.drop_table("slack_files")
    op.drop_index("ix_slack_message_links_domain", table_name="slack_message_links")
    op.drop_table("slack_message_links")
    op.drop_index("ix_slack_reactions_message", table_name="slack_reactions")
    op.drop_table("slack_reactions")
    op.drop_table("slack_channel_sync_settings")

    op.drop_index("ix_slack_messages_content_hash", table_name="slack_messages")
    op.drop_index("ix_slack_messages_posted_at", table_name="slack_messages")
    op.drop_index("ix_slack_messages_workspace_channel", table_name="slack_messages")
    op.drop_constraint("uq_slack_message_identity", "slack_messages", type_="unique")
    op.drop_constraint("fk_slack_messages_parent", "slack_messages", type_="foreignkey")
    op.drop_constraint("fk_slack_messages_author", "slack_messages", type_="foreignkey")
    op.drop_constraint("fk_slack_messages_workspace", "slack_messages", type_="foreignkey")
    for column in ("last_changed_at", "last_seen_at", "first_seen_at", "is_deleted", "content_hash", "permalink", "source_edited_at", "source_posted_at", "subtype", "parent_message_id", "author_user_id", "workspace_id"):
        op.drop_column("slack_messages", column)
    op.execute("ALTER TABLE public.slack_messages ADD CONSTRAINT slack_messages_slack_ts_key UNIQUE (slack_ts)")

    op.drop_index("ix_slack_users_workspace", table_name="slack_users")
    op.drop_table("slack_users")
    op.drop_index("ix_slack_channels_sync", table_name="slack_channels")
    op.drop_index("ix_slack_channels_workspace", table_name="slack_channels")
    op.drop_constraint("fk_slack_channels_workspace", "slack_channels", type_="foreignkey")
    for column in ("last_synced_at", "last_seen_at", "first_seen_at", "source_created_at", "is_private", "purpose", "topic", "workspace_id"):
        op.drop_column("slack_channels", column)
    op.drop_index("ix_slack_workspaces_last_synced", table_name="slack_workspaces")
    op.drop_table("slack_workspaces")
