"""Version the read-only retrieval contract consumed by efds-agent."""

from alembic import op

revision: str = "0014_agent_retrieval_contract"
down_revision: str = "0013_semantic_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION public.search_retrieval_units_v1(
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
        retrieval_unit_id uuid, source_type text, source_record_id text,
        source_parent_id text, source_version_id text, title text, snippet text,
        score real, source_area text, topic text, channel text, author text,
        occurred_at timestamptz, source_updated_at timestamptz,
        review_status text, visibility text, is_current boolean, is_stale boolean,
        source_url text, permalink text, relative_path text, content_hash text,
        metadata jsonb, authority text
      )
      LANGUAGE sql STABLE SECURITY INVOKER
      SET search_path = public, pg_temp
      AS $$
        SELECT base.retrieval_unit_id, base.source_type, base.source_record_id,
          base.source_parent_id, base.source_version_id, base.title, base.snippet,
          base.score, base.source_area, base.topic, base.channel, base.author,
          base.occurred_at, base.source_updated_at, base.review_status,
          base.visibility, base.is_current, base.is_stale, base.source_url,
          base.permalink, base.relative_path, base.content_hash, base.metadata,
          unit.authority
        FROM public.search_retrieval_units(
          search_query, requested_source_types, requested_area, requested_topic,
          requested_channel, requested_author, requested_from, requested_to,
          include_history, result_limit, result_offset
        ) AS base
        JOIN public.retrieval_units AS unit ON unit.id = base.retrieval_unit_id
      $$
    """)
    op.execute("GRANT EXECUTE ON FUNCTION public.search_retrieval_units_v1(text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer) TO anon, authenticated")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.search_retrieval_units_v1(text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer) FROM anon, authenticated")
    op.execute("DROP FUNCTION IF EXISTS public.search_retrieval_units_v1(text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer)")
