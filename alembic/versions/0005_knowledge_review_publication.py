"""Add review audit, publication visibility, and admin mutation policies.

Revision ID: 0005_knowledge_review_publication
Revises: 0004_auth_profiles_and_rls
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_knowledge_review_publication"
down_revision: str | None = "0004_auth_profiles_and_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
jsonb = postgresql.JSONB(astext_type=sa.Text())

DERIVED_TABLES = (
    "knowledge_requirements",
    "knowledge_timing_rules",
    "knowledge_processes",
    "knowledge_process_steps",
    "knowledge_resources",
    "knowledge_contacts",
)


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    # The initial migration created alembic_version.version_num as varchar(32),
    # but this and later descriptive revision IDs are longer than 32 chars.
    # Widen it before Alembic records this revision.
    op.execute("ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE varchar(255)")
    for table in DERIVED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "reviewed_by_profile_id",
                uuid,
                sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "visibility",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'internal'"),
            ),
        )
        op.add_column(table, sa.Column("published_at", sa.DateTime(timezone=True)))
        op.add_column(
            table,
            sa.Column(
                "published_by_profile_id",
                uuid,
                sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            ),
        )
        op.create_index(
            f"ix_{table}_visibility_review",
            table,
            ["visibility", "review_status", "is_stale"],
        )
        op.create_check_constraint(
            f"ck_{table}_review_status",
            table,
            "review_status IN ('proposed', 'approved', 'rejected', 'needs_review', 'superseded')",
        )
        op.create_check_constraint(
            f"ck_{table}_visibility",
            table,
            "visibility IN ('internal', 'committee', 'member', 'public')",
        )

    op.create_table(
        "knowledge_review_events",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("knowledge_type", sa.Text(), nullable=False),
        sa.Column("knowledge_record_id", uuid, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("previous_status", sa.Text()),
        sa.Column("new_status", sa.Text()),
        sa.Column(
            "reviewer_profile_id",
            uuid,
            sa.ForeignKey("profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text()),
        sa.Column("changes", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "knowledge_type IN ('requirement', 'timing_rule', 'process', 'process_step', 'resource', 'contact')",
            name="ck_knowledge_review_events_type",
        ),
    )
    op.create_index(
        "ix_knowledge_review_events_record",
        "knowledge_review_events",
        ["knowledge_type", "knowledge_record_id"],
    )
    op.create_index(
        "ix_knowledge_review_events_created_at",
        "knowledge_review_events",
        ["created_at"],
    )
    op.create_index(
        "ix_knowledge_review_events_reviewer",
        "knowledge_review_events",
        ["reviewer_profile_id"],
    )

    op.execute("ALTER TABLE public.knowledge_review_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY knowledge_review_events_select_admin
        ON public.knowledge_review_events FOR SELECT TO authenticated
        USING (public.has_efds_role('admin'));

        CREATE POLICY knowledge_review_events_insert_admin
        ON public.knowledge_review_events FOR INSERT TO authenticated
        WITH CHECK (
          public.has_efds_role('admin')
          AND reviewer_profile_id = (
            SELECT p.id FROM public.profiles AS p
            WHERE p.auth_user_id = auth.uid() AND p.active
            LIMIT 1
          )
        );
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON public.knowledge_review_events TO authenticated"
    )
    op.execute("REVOKE ALL ON public.knowledge_review_events FROM anon")

    for table in DERIVED_TABLES:
        op.execute(
            f"""
            CREATE POLICY {table}_update_admin
            ON public.{table} FOR UPDATE TO authenticated
            USING (public.has_efds_role('admin'))
            WITH CHECK (public.has_efds_role('admin'));

            CREATE POLICY {table}_select_published_member
            ON public.{table} FOR SELECT TO authenticated
            USING (
              review_status = 'approved'
              AND NOT is_stale
              AND visibility IN ('member', 'public')
            );
            """
        )
        op.execute(f"GRANT UPDATE ON public.{table} TO authenticated")

    op.execute(
        """
        CREATE OR REPLACE VIEW public.public_knowledge_resources AS
        SELECT
          r.id,
          r.name,
          r.resource_type,
          r.url,
          r.system_name,
          r.anchor_text,
          r.description,
          r.topic_id,
          a.title AS source_article_title,
          a.url AS source_article_url,
          r.published_at
        FROM public.knowledge_resources AS r
        JOIN public.knowledge_articles AS a ON a.id = r.source_article_id
        WHERE r.review_status = 'approved'
          AND NOT r.is_stale
          AND r.visibility = 'public';
        GRANT SELECT ON public.public_knowledge_resources TO anon, authenticated;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.public_knowledge_resources")
    for table in DERIVED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_select_published_member ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_update_admin ON public.{table}")
        op.drop_constraint(f"ck_{table}_visibility", table, type_="check")
        op.drop_constraint(f"ck_{table}_review_status", table, type_="check")
        op.drop_index(f"ix_{table}_visibility_review", table_name=table)
        op.drop_column(table, "published_by_profile_id")
        op.drop_column(table, "published_at")
        op.drop_column(table, "visibility")
        op.drop_column(table, "reviewed_by_profile_id")

    op.execute("DROP POLICY IF EXISTS knowledge_review_events_insert_admin ON public.knowledge_review_events")
    op.execute("DROP POLICY IF EXISTS knowledge_review_events_select_admin ON public.knowledge_review_events")
    op.execute("ALTER TABLE public.knowledge_review_events DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.knowledge_review_events FROM authenticated, anon")
    op.drop_index("ix_knowledge_review_events_reviewer", table_name="knowledge_review_events")
    op.drop_index("ix_knowledge_review_events_created_at", table_name="knowledge_review_events")
    op.drop_index("ix_knowledge_review_events_record", table_name="knowledge_review_events")
    op.drop_table("knowledge_review_events")
    op.execute("ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE varchar(32)")
