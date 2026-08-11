"""Add the corpus-informed ICU operational knowledge layer.

Revision ID: 0003_icu_knowledge_layer
Revises: 0002_icu_article_sync
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_icu_knowledge_layer"
down_revision: str | None = "0002_icu_article_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

jsonb = postgresql.JSONB(astext_type=sa.Text())
uuid = postgresql.UUID(as_uuid=True)


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def _derived_columns() -> list[sa.Column]:
    return [
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_article_id", uuid, sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_content_hash", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("evidence_start", sa.Integer()),
        sa.Column("evidence_end", sa.Integer()),
        sa.Column("extraction_run_id", uuid, sa.ForeignKey("knowledge_extraction_runs.id", ondelete="SET NULL")),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("extraction_method", sa.Text(), nullable=False, server_default=sa.text("'deterministic'")),
        sa.Column("confidence", sa.Float()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("reviewed_by_officer_id", uuid, sa.ForeignKey("officers.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    op.create_table(
        "knowledge_topics",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "knowledge_roles",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("role_group", sa.Text(), nullable=False, server_default=sa.text("'efds_committee'")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "knowledge_extraction_runs",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_type", sa.Text(), nullable=False, server_default=sa.text("'icu_freshdesk'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'running'")),
        sa.Column("article_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deterministic_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("semantic_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("proposed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("approved_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_unchanged", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model_provider", sa.Text()),
        sa.Column("model_name", sa.Text()),
        sa.Column("extractor_version", sa.Text()),
        sa.Column("error_log", jsonb, nullable=False, server_default=_json_default("[]")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )
    op.add_column("knowledge_articles", sa.Column("efds_relevance", sa.Text(), nullable=False, server_default=sa.text("'low'")))
    op.add_column("knowledge_articles", sa.Column("relevance_confidence", sa.Float()))
    op.add_column("knowledge_articles", sa.Column("relevance_method", sa.Text()))
    op.add_column("knowledge_articles", sa.Column("relevance_review_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")))
    op.add_column("knowledge_articles", sa.Column("relevance_evidence", sa.Text()))
    op.add_column("knowledge_articles", sa.Column("relevance_updated_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_articles", sa.Column("last_extracted_content_hash", sa.Text()))
    op.add_column("knowledge_articles", sa.Column("last_extracted_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_articles", sa.Column("last_extraction_run_id", uuid, sa.ForeignKey("knowledge_extraction_runs.id", ondelete="SET NULL")))

    op.create_table(
        "knowledge_article_topics",
        sa.Column("knowledge_article_id", uuid, sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("topic_id", uuid, sa.ForeignKey("knowledge_topics.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("confidence", sa.Float()),
        sa.Column("method", sa.Text(), nullable=False, server_default=sa.text("'deterministic'")),
    )
    op.create_table(
        "knowledge_article_roles",
        sa.Column("knowledge_article_id", uuid, sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", uuid, sa.ForeignKey("knowledge_roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("confidence", sa.Float()),
        sa.Column("method", sa.Text(), nullable=False, server_default=sa.text("'deterministic'")),
    )

    op.create_table(
        "knowledge_requirements",
        *_derived_columns(),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("requirement_type", sa.Text(), nullable=False),
        sa.Column("topic_id", uuid, sa.ForeignKey("knowledge_topics.id", ondelete="SET NULL")),
        sa.Column("applies_to", sa.Text()),
        sa.Column("mandatory", sa.Boolean()),
        sa.UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_requirement_version_fingerprint"),
    )
    op.create_index("ix_knowledge_requirements_source", "knowledge_requirements", ["source_article_id", "source_content_hash"])
    op.create_index("ix_knowledge_requirements_review", "knowledge_requirements", ["review_status", "is_stale"])

    op.create_table(
        "knowledge_timing_rules",
        *_derived_columns(),
        sa.Column("deadline_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("absolute_date", sa.Date()),
        sa.Column("notice_period_value", sa.Integer()),
        sa.Column("notice_period_unit", sa.Text()),
        sa.Column("working_days", sa.Boolean()),
        sa.Column("recurrence_rule", sa.Text()),
        sa.Column("relative_to_event_type", sa.Text()),
        sa.Column("topic_id", uuid, sa.ForeignKey("knowledge_topics.id", ondelete="SET NULL")),
        sa.UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_timing_version_fingerprint"),
    )
    op.create_index("ix_knowledge_timing_rules_source", "knowledge_timing_rules", ["source_article_id", "source_content_hash"])
    op.create_index("ix_knowledge_timing_rules_review", "knowledge_timing_rules", ["review_status", "is_stale"])

    op.create_table(
        "knowledge_processes",
        *_derived_columns(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("topic_id", uuid, sa.ForeignKey("knowledge_topics.id", ondelete="SET NULL")),
        sa.UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_process_version_fingerprint"),
    )
    op.create_index("ix_knowledge_processes_source", "knowledge_processes", ["source_article_id", "source_content_hash"])
    op.create_index("ix_knowledge_processes_review", "knowledge_processes", ["review_status", "is_stale"])

    op.create_table(
        "knowledge_process_steps",
        *_derived_columns(),
        sa.Column("process_id", uuid, sa.ForeignKey("knowledge_processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("condition", sa.Text()),
        sa.UniqueConstraint("process_id", "step_number", name="uq_process_step_order"),
    )
    op.create_index("ix_knowledge_process_steps_process_order", "knowledge_process_steps", ["process_id", "step_number"])

    op.create_table(
        "knowledge_resources",
        *_derived_columns(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("url", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("system_name", sa.Text()),
        sa.Column("anchor_text", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("topic_id", uuid, sa.ForeignKey("knowledge_topics.id", ondelete="SET NULL")),
        sa.UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_resource_version_fingerprint"),
    )
    op.create_index("ix_knowledge_resources_source", "knowledge_resources", ["source_article_id", "source_content_hash"])
    op.create_index("ix_knowledge_resources_type", "knowledge_resources", ["resource_type"])

    op.create_table(
        "knowledge_contacts",
        *_derived_columns(),
        sa.Column("name", sa.Text()),
        sa.Column("organisation", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("contact_type", sa.Text(), nullable=False, server_default=sa.text("'email'")),
        sa.Column("url", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.UniqueConstraint("source_article_id", "source_content_hash", "fingerprint", name="uq_contact_version_fingerprint"),
    )
    op.create_index("ix_knowledge_contacts_email", "knowledge_contacts", ["email"])

    op.create_table(
        "knowledge_requirement_roles",
        sa.Column("requirement_id", uuid, sa.ForeignKey("knowledge_requirements.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", uuid, sa.ForeignKey("knowledge_roles.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "knowledge_process_roles",
        sa.Column("process_id", uuid, sa.ForeignKey("knowledge_processes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", uuid, sa.ForeignKey("knowledge_roles.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "knowledge_process_resources",
        sa.Column("process_id", uuid, sa.ForeignKey("knowledge_processes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("resource_id", uuid, sa.ForeignKey("knowledge_resources.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "knowledge_article_relationships",
        sa.Column("source_article_id", uuid, sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_article_id", uuid, sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relationship_type", sa.Text(), primary_key=True),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("source_content_hash", sa.Text()),
        sa.UniqueConstraint("source_article_id", "target_article_id", "relationship_type", name="uq_article_relationship"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_article_relationships")
    op.drop_table("knowledge_process_resources")
    op.drop_table("knowledge_process_roles")
    op.drop_table("knowledge_requirement_roles")
    op.drop_index("ix_knowledge_contacts_email", table_name="knowledge_contacts")
    op.drop_table("knowledge_contacts")
    op.drop_index("ix_knowledge_resources_type", table_name="knowledge_resources")
    op.drop_index("ix_knowledge_resources_source", table_name="knowledge_resources")
    op.drop_table("knowledge_resources")
    op.drop_index("ix_knowledge_process_steps_process_order", table_name="knowledge_process_steps")
    op.drop_table("knowledge_process_steps")
    op.drop_index("ix_knowledge_processes_review", table_name="knowledge_processes")
    op.drop_index("ix_knowledge_processes_source", table_name="knowledge_processes")
    op.drop_table("knowledge_processes")
    op.drop_index("ix_knowledge_timing_rules_review", table_name="knowledge_timing_rules")
    op.drop_index("ix_knowledge_timing_rules_source", table_name="knowledge_timing_rules")
    op.drop_table("knowledge_timing_rules")
    op.drop_index("ix_knowledge_requirements_review", table_name="knowledge_requirements")
    op.drop_index("ix_knowledge_requirements_source", table_name="knowledge_requirements")
    op.drop_table("knowledge_requirements")
    op.drop_table("knowledge_article_roles")
    op.drop_table("knowledge_article_topics")
    op.drop_column("knowledge_articles", "last_extraction_run_id")
    op.drop_column("knowledge_articles", "last_extracted_at")
    op.drop_column("knowledge_articles", "last_extracted_content_hash")
    op.drop_column("knowledge_articles", "relevance_updated_at")
    op.drop_column("knowledge_articles", "relevance_evidence")
    op.drop_column("knowledge_articles", "relevance_review_status")
    op.drop_column("knowledge_articles", "relevance_method")
    op.drop_column("knowledge_articles", "relevance_confidence")
    op.drop_column("knowledge_articles", "efds_relevance")
    op.drop_table("knowledge_extraction_runs")
    op.drop_table("knowledge_roles")
    op.drop_table("knowledge_topics")
