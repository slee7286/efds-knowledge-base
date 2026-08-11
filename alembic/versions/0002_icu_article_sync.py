"""Add ICU article lifecycle fields and queryable change history.

Revision ID: 0002_icu_article_sync
Revises: 0001_initial_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_icu_article_sync"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

jsonb = postgresql.JSONB(astext_type=sa.Text())
uuid = postgresql.UUID(as_uuid=True)


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "knowledge_articles",
        sa.Column("source_type", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
    )
    op.add_column(
        "knowledge_articles",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "knowledge_articles",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "knowledge_articles",
        sa.Column("last_changed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_knowledge_articles_source_type", "knowledge_articles", ["source_type"])
    op.create_unique_constraint(
        "uq_knowledge_articles_source_external_id",
        "knowledge_articles",
        ["source_type", "external_id"],
    )

    op.create_table(
        "knowledge_article_changes",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "knowledge_article_id",
            uuid,
            sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ingestion_run_id",
            uuid,
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("previous_content_hash", sa.Text()),
        sa.Column("new_content_hash", sa.Text()),
        sa.Column("previous_source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("new_source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("fields_changed", jsonb, nullable=False, server_default=_json_default("[]")),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_knowledge_article_changes_article_id",
        "knowledge_article_changes",
        ["knowledge_article_id"],
    )
    op.create_index(
        "ix_knowledge_article_changes_detected_at",
        "knowledge_article_changes",
        ["detected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_article_changes_detected_at", table_name="knowledge_article_changes")
    op.drop_index("ix_knowledge_article_changes_article_id", table_name="knowledge_article_changes")
    op.drop_table("knowledge_article_changes")
    op.drop_constraint(
        "uq_knowledge_articles_source_external_id", "knowledge_articles", type_="unique"
    )
    op.drop_index("ix_knowledge_articles_source_type", table_name="knowledge_articles")
    op.drop_column("knowledge_articles", "last_changed_at")
    op.drop_column("knowledge_articles", "last_checked_at")
    op.drop_column("knowledge_articles", "first_seen_at")
    op.drop_column("knowledge_articles", "source_type")
