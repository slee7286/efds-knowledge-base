"""Add versioned pgvector embeddings and permission-scoped semantic search."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_semantic_retrieval"
down_revision: str | None = "0012_operational_truth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # Supabase/PostgreSQL installations must have pgvector available. The
    # extension is intentionally owned by Alembic, not by application startup.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
      CREATE TABLE retrieval_embeddings (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        retrieval_unit_id uuid NOT NULL REFERENCES retrieval_units(id) ON DELETE CASCADE,
        provider text NOT NULL,
        model text NOT NULL,
        model_version text NOT NULL,
        dimension integer NOT NULL CHECK (dimension > 0),
        input_hash text NOT NULL,
        embedding vector NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_retrieval_embeddings_model UNIQUE (retrieval_unit_id, provider, model, model_version)
      )
    """)
    op.create_index(
        "ix_retrieval_embeddings_unit_model", "retrieval_embeddings",
        ["retrieval_unit_id", "provider", "model", "model_version"],
    )
    op.execute("ALTER TABLE retrieval_embeddings ENABLE ROW LEVEL SECURITY")
    op.execute("""
      CREATE POLICY retrieval_embeddings_select_visible
      ON retrieval_embeddings FOR SELECT TO authenticated
      USING (
        public.has_efds_role('admin')
        OR EXISTS (
          SELECT 1 FROM public.retrieval_units r
          WHERE r.id = retrieval_unit_id
        )
      )
    """)
    op.execute("GRANT SELECT ON retrieval_embeddings TO authenticated")
    op.execute("REVOKE ALL ON retrieval_embeddings FROM anon")

    op.execute(r"""
      CREATE FUNCTION public.search_retrieval_units_semantic(
        query_embedding vector,
        requested_provider text,
        requested_model text,
        requested_model_version text,
        requested_scope text DEFAULT 'admin',
        requested_source_types text[] DEFAULT NULL,
        requested_area text DEFAULT NULL,
        requested_topic text DEFAULT NULL,
        requested_channel text DEFAULT NULL,
        requested_author text DEFAULT NULL,
        requested_from timestamptz DEFAULT NULL,
        requested_to timestamptz DEFAULT NULL,
        include_history boolean DEFAULT false,
        result_limit integer DEFAULT 50,
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
        semantic_similarity real,
        source_area text,
        topic text,
        channel text,
        author text,
        occurred_at timestamptz,
        source_updated_at timestamptz,
        review_status text,
        visibility text,
        authority text,
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
        SELECT r.id, r.source_type, r.source_record_id, r.source_parent_id,
          r.source_version_id, r.title, left(r.content, 700),
          (1.0 - (e.embedding <=> query_embedding))::real,
          (1.0 - (e.embedding <=> query_embedding))::real,
          r.source_area, r.topic, r.channel, r.author, r.occurred_at,
          r.source_updated_at, r.review_status, r.visibility, r.authority, r.is_current,
          r.is_stale, r.source_url, r.permalink, r.relative_path,
          r.content_hash, r.metadata
        FROM public.retrieval_units r
        JOIN public.retrieval_embeddings e ON e.retrieval_unit_id = r.id
        WHERE query_embedding IS NOT NULL
          AND e.provider = requested_provider
          AND e.model = requested_model
          AND e.model_version = requested_model_version
          AND (CAST(requested_source_types AS text[]) IS NULL OR r.source_type = ANY(CAST(requested_source_types AS text[])))
          AND (CAST(requested_area AS text) IS NULL OR r.source_area = CAST(requested_area AS text))
          AND (CAST(requested_topic AS text) IS NULL OR r.topic = CAST(requested_topic AS text))
          AND (CAST(requested_channel AS text) IS NULL OR r.channel = CAST(requested_channel AS text))
          AND (CAST(requested_author AS text) IS NULL OR r.author = CAST(requested_author AS text))
          AND (CAST(requested_from AS timestamptz) IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) >= CAST(requested_from AS timestamptz))
          AND (CAST(requested_to AS timestamptz) IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) < CAST(requested_to AS timestamptz))
          AND ((include_history AND public.has_efds_role('admin')) OR r.is_current)
          AND (
            requested_scope = 'admin'
            OR (
              r.source_type NOT IN ('document', 'slack_message', 'meeting_transcript', 'meeting_summary', 'meeting_notes')
              AND r.review_status = 'approved' AND NOT r.is_stale AND NOT r.is_deleted
              AND (r.visibility = 'public'
                OR (r.visibility = 'member' AND public.has_efds_role('member'))
                OR (r.visibility = 'committee' AND public.has_efds_role('committee')))
            )
          )
        ORDER BY e.embedding <=> query_embedding
        LIMIT least(greatest(coalesce(result_limit, 50), 1), 100)
        OFFSET greatest(coalesce(result_offset, 0), 0)
      $$
    """)
    op.execute("GRANT EXECUTE ON FUNCTION public.search_retrieval_units_semantic(vector, text, text, text, text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer) TO authenticated")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.search_retrieval_units_semantic(vector, text, text, text, text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer) FROM authenticated")
    op.execute("DROP FUNCTION IF EXISTS public.search_retrieval_units_semantic(vector, text, text, text, text, text[], text, text, text, text, timestamptz, timestamptz, boolean, integer, integer)")
    op.execute("DROP POLICY IF EXISTS retrieval_embeddings_select_visible ON retrieval_embeddings")
    op.execute("REVOKE ALL ON retrieval_embeddings FROM authenticated")
    op.drop_index("ix_retrieval_embeddings_unit_model", table_name="retrieval_embeddings")
    op.drop_table("retrieval_embeddings")
    # pgvector may be shared by another application; do not drop the extension.
