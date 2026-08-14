"""Add the PostgreSQL-native unified retrieval index.

Revision ID: 0010_unified_retrieval
Revises: 0009_auth_exception_magic_links
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_unified_retrieval"
down_revision: Union[str, None] = "0009_auth_exception_magic_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("source_parent_id", sa.Text()),
        sa.Column("source_version_id", sa.Text()),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("index_hash", sa.Text(), nullable=False),
        sa.Column("source_area", sa.Text()),
        sa.Column("topic", sa.Text()),
        sa.Column("channel", sa.Text()),
        sa.Column("author", sa.Text()),
        sa.Column("visibility", sa.Text(), nullable=False, server_default=sa.text("'internal'")),
        sa.Column("review_status", sa.Text()),
        sa.Column("authority", sa.Text(), nullable=False, server_default=sa.text("'source_record'")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("source_url", sa.Text()),
        sa.Column("permalink", sa.Text()),
        sa.Column("relative_path", sa.Text()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("stable_key", name="uq_retrieval_units_stable_key"),
    )
    op.create_index(
        "ix_retrieval_units_search_vector", "retrieval_units", ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index("ix_retrieval_units_source_current", "retrieval_units", ["source_type", "is_current"])
    op.create_index("ix_retrieval_units_visibility_state", "retrieval_units", ["visibility", "review_status", "is_current"])
    op.create_index("ix_retrieval_units_source_record", "retrieval_units", ["source_type", "source_record_id"])
    op.create_index("ix_retrieval_units_source_version", "retrieval_units", ["source_version_id"])
    op.create_index("ix_retrieval_units_area", "retrieval_units", ["source_area"])
    op.create_index("ix_retrieval_units_channel", "retrieval_units", ["channel"])
    op.create_index("ix_retrieval_units_occurred_at", "retrieval_units", ["occurred_at"])
    op.execute(
        """
        CREATE FUNCTION public.retrieval_units_search_vector_trigger()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          NEW.search_vector :=
            setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(NEW.content, '')), 'C') ||
            setweight(to_tsvector('simple', coalesce(NEW.source_area, '') || ' ' || coalesce(NEW.channel, '') || ' ' || coalesce(NEW.author, '')), 'B');
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER retrieval_units_search_vector_before_write
        BEFORE INSERT OR UPDATE OF title, content, source_area, channel, author
        ON public.retrieval_units
        FOR EACH ROW EXECUTE FUNCTION public.retrieval_units_search_vector_trigger()
        """
    )

    op.execute("ALTER TABLE public.retrieval_units ENABLE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT ON public.retrieval_units TO anon, authenticated")
    # This existing SECURITY DEFINER helper only returns a boolean and is
    # needed by the invoker RPC/RLS predicates for an anonymous public query.
    op.execute("GRANT EXECUTE ON FUNCTION public.has_efds_role(text) TO anon")
    op.execute(
        """
        CREATE POLICY retrieval_units_public_select
        ON public.retrieval_units FOR SELECT TO anon, authenticated
        USING (
          source_type NOT IN ('document', 'slack_message')
          AND visibility = 'public'
          AND review_status = 'approved'
          AND is_current
          AND NOT is_stale
          AND NOT is_deleted
        )
        """
    )
    op.execute(
        """
        CREATE POLICY retrieval_units_member_select
        ON public.retrieval_units FOR SELECT TO authenticated
        USING (
          public.has_efds_role('member')
          AND source_type NOT IN ('document', 'slack_message')
          AND visibility IN ('member', 'public')
          AND review_status = 'approved'
          AND is_current
          AND NOT is_stale
          AND NOT is_deleted
        )
        """
    )
    op.execute(
        """
        CREATE POLICY retrieval_units_committee_select
        ON public.retrieval_units FOR SELECT TO authenticated
        USING (
          public.has_efds_role('committee')
          AND source_type NOT IN ('document', 'slack_message')
          AND visibility IN ('committee', 'member', 'public')
          AND review_status = 'approved'
          AND is_current
          AND NOT is_stale
          AND NOT is_deleted
        )
        """
    )
    op.execute(
        """
        CREATE POLICY retrieval_units_admin_select
        ON public.retrieval_units FOR SELECT TO authenticated
        USING (public.has_efds_role('admin'))
        """
    )

    # The function is SECURITY INVOKER.  RLS remains the final data boundary;
    # these predicates also make the access intent explicit in the RPC.
    op.execute(
        """
        CREATE FUNCTION public.search_retrieval_units(
          search_query text,
          requested_source_types text[] DEFAULT NULL,
          requested_area text DEFAULT NULL,
          requested_topic text DEFAULT NULL,
          requested_channel text DEFAULT NULL,
          requested_author text DEFAULT NULL,
          requested_from timestamptz DEFAULT NULL,
          requested_to timestamptz DEFAULT NULL,
          include_history boolean DEFAULT false,
          result_limit integer DEFAULT 20,
          result_offset integer DEFAULT 0
        )
        RETURNS TABLE (
          retrieval_unit_id uuid,
          source_type text,
          source_record_id text,
          source_parent_id text,
          source_version_id text,
          title text,
          snippet text,
          score real,
          source_area text,
          topic text,
          channel text,
          author text,
          occurred_at timestamptz,
          source_updated_at timestamptz,
          review_status text,
          visibility text,
          is_current boolean,
          is_stale boolean,
          source_url text,
          permalink text,
          relative_path text,
          content_hash text,
          metadata jsonb
        )
        LANGUAGE sql STABLE SECURITY INVOKER
        SET search_path = public, pg_temp
        AS $$
          WITH query AS (
            SELECT websearch_to_tsquery('simple', trim(search_query)) AS tsq
          ),
          ranked AS (
            SELECT
              r.*,
              ts_rank_cd(r.search_vector, q.tsq, 32)
                + CASE WHEN lower(r.title) = lower(trim(search_query)) THEN 2.0 ELSE 0.0 END
                + CASE WHEN r.title ILIKE '%' || trim(search_query) || '%' THEN 0.35 ELSE 0.0 END
                + CASE r.authority
                    WHEN 'approved_operational' THEN 0.35
                    WHEN 'icu_source' THEN 0.20
                    WHEN 'governance' THEN 0.20
                    WHEN 'committee_record' THEN 0.10
                    ELSE 0.0
                  END
                + CASE WHEN r.is_current THEN 0.15 ELSE 0.0 END
                + CASE WHEN r.source_type = 'slack_message'
                       THEN greatest(0.0, 0.05 * (1.0 - extract(epoch FROM (now() - coalesce(r.occurred_at, r.source_updated_at, r.updated_at))) / 31536000.0))
                       ELSE 0.0 END
                AS computed_score
            FROM public.retrieval_units AS r
            CROSS JOIN query AS q
            WHERE trim(search_query) <> ''
              AND r.search_vector @@ q.tsq
              AND (CAST(requested_source_types AS text[]) IS NULL OR r.source_type = ANY(CAST(requested_source_types AS text[])))
              AND (requested_area IS NULL OR r.source_area = requested_area)
              AND (requested_topic IS NULL OR r.topic = requested_topic)
              AND (requested_channel IS NULL OR r.channel = requested_channel)
              AND (requested_author IS NULL OR r.author = requested_author)
              AND (requested_from IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) >= requested_from)
              AND (requested_to IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) < requested_to)
              AND (include_history AND public.has_efds_role('admin') OR r.is_current)
              AND (
                public.has_efds_role('admin')
                OR (
                  r.source_type NOT IN ('document', 'slack_message')
                  AND r.review_status = 'approved'
                  AND NOT r.is_stale
                  AND NOT r.is_deleted
                  AND (
                    (r.visibility = 'public')
                    OR (r.visibility = 'member' AND public.has_efds_role('member'))
                    OR (r.visibility = 'committee' AND public.has_efds_role('committee'))
                  )
                )
              )
          )
          SELECT
            id, source_type, source_record_id, source_parent_id, source_version_id,
            title,
            ts_headline('simple', content, websearch_to_tsquery('simple', trim(search_query)),
              'MaxFragments=2,MaxWords=50,MinWords=15,StartSel=<mark>,StopSel=</mark>'),
            computed_score::real, source_area, topic, channel, author,
            occurred_at, source_updated_at, review_status, visibility,
            is_current, is_stale, source_url, permalink, relative_path,
            content_hash, metadata
          FROM ranked
          ORDER BY computed_score DESC, coalesce(occurred_at, source_updated_at, updated_at) DESC, id
          LIMIT least(greatest(coalesce(result_limit, 20), 1), 50)
          OFFSET greatest(coalesce(result_offset, 0), 0)
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION public.search_retrieval_units(text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer) TO anon, authenticated")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.has_efds_role(text) FROM anon")
    op.execute("REVOKE EXECUTE ON FUNCTION public.search_retrieval_units(text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer) FROM anon, authenticated")
    op.execute("DROP FUNCTION IF EXISTS public.search_retrieval_units(text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer)")
    op.execute("DROP POLICY IF EXISTS retrieval_units_admin_select ON public.retrieval_units")
    op.execute("DROP POLICY IF EXISTS retrieval_units_committee_select ON public.retrieval_units")
    op.execute("DROP POLICY IF EXISTS retrieval_units_member_select ON public.retrieval_units")
    op.execute("DROP POLICY IF EXISTS retrieval_units_public_select ON public.retrieval_units")
    op.execute("REVOKE SELECT ON public.retrieval_units FROM anon, authenticated")
    op.execute("DROP TRIGGER IF EXISTS retrieval_units_search_vector_before_write ON public.retrieval_units")
    op.execute("DROP FUNCTION IF EXISTS public.retrieval_units_search_vector_trigger()")
    op.drop_index("ix_retrieval_units_occurred_at", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_channel", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_area", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_source_version", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_source_record", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_visibility_state", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_source_current", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_search_vector", table_name="retrieval_units")
    op.drop_table("retrieval_units")
