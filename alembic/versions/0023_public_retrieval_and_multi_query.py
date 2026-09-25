"""Restore anonymous/public retrieval and add multi-query hybrid retrieval.

Two live defects are fixed here.

1. `search_retrieval_units` and `search_retrieval_units_semantic` are granted to
   `anon`, but their predicates call `public.has_efds_role(...)`. Migration 0018
   revoked EXECUTE on that helper from `anon`, so every anonymous call now fails
   with SQLSTATE 42501 ("permission denied for function has_efds_role") instead
   of returning the public slice. PostgreSQL checks function EXECUTE privilege
   when the query is planned, not when a branch is evaluated, so guarding the
   call behind a CASE/CURRENT_USER test does not help: the privileged helper
   must not be *referenced* at all on the anonymous path. The public function
   below therefore hardcodes the public predicate and never references role
   helpers or `auth.uid()`.

   Revoking `anon` alone cannot close such a function, because the inherited
   PUBLIC grant still permits execution - the same trap migration 0018
   documents. Both layers are revoked here.

2. `public.public_knowledge_resources` is evaluated with invoker rights in
   production, so anonymous readers fail on the base table
   ("permission denied for table knowledge_resources") even though they hold
   SELECT on the view. The view is pinned to owner-rights evaluation here; its
   own WHERE clause already restricts the result to approved, non-stale, public
   rows, so this does not widen access.

The public function is SECURITY DEFINER because anonymous callers have no
privilege on `retrieval_embeddings`. That privilege bypass is safe only because
the public predicate is a literal constant in the function body - it accepts no
role, scope or visibility argument - and because `search_path` is pinned.
RLS remains the boundary for the SECURITY INVOKER function.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023_public_retrieval_and_multi_query"
down_revision: str | None = "82afe16026f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Source types withheld from retrieval for non-admin callers. Mirrors the
# predicate used by search_retrieval_units and the retrieval_units RLS policies.
RESTRICTED_SOURCE_TYPES = "('document', 'slack_message', 'meeting_transcript', 'meeting_summary', 'meeting_notes')"

# Type list for the public function, in declaration order. GRANT/REVOKE take
# types only - passing the named-argument form is a syntax error.
PUBLIC_SIGNATURE = (
    "text[], vector, text[], text, text, text, text, timestamp with time zone, "
    "timestamp with time zone, text, text, text, real, integer, integer"
)

AUTHENTICATED_SIGNATURE = (
    "text[], vector, text[], text, text, text, text, timestamp with time zone, "
    "timestamp with time zone, text, text, text, real, boolean, integer, integer"
)

LEGACY_LEXICAL_SIGNATURE = (
    "text, text[], text, text, text, text, timestamp with time zone, "
    "timestamp with time zone, boolean, integer, integer"
)

LEGACY_SEMANTIC_SIGNATURE = (
    "vector, text, text, text, text, text[], text, text, text, text, "
    "timestamp with time zone, timestamp with time zone, boolean, integer, integer"
)

# The authoritative public slice. This is the same predicate as the
# `retrieval_units_public_select` RLS policy, inlined so that an anonymous
# caller never touches a privileged helper.
PUBLIC_PREDICATE = f"""
        r.source_type NOT IN {RESTRICTED_SOURCE_TYPES}
    AND r.review_status = 'approved'
    AND NOT r.is_stale
    AND NOT r.is_deleted
    AND r.visibility = 'public'
    AND r.is_current
"""

# Role-aware predicate for the authenticated function. Kept deliberately
# identical in shape to search_retrieval_units so the two cannot drift.
AUTHENTICATED_PREDICATE = f"""
        (
            public.has_efds_role('admin')
            OR (
                r.source_type NOT IN {RESTRICTED_SOURCE_TYPES}
                AND r.review_status = 'approved'
                AND NOT r.is_stale
                AND NOT r.is_deleted
                AND (
                    r.visibility = 'public'
                    OR (r.visibility = 'member' AND public.has_efds_role('member'))
                    OR (r.visibility = 'committee' AND public.has_efds_role('committee'))
                )
            )
        )
"""

RETURNS_COLUMNS = """
    id uuid,
    source_type text,
    source_record_id text,
    source_parent_id text,
    source_version_id text,
    title text,
    content text,
    content_hash text,
    source_area text,
    topic text,
    channel text,
    author text,
    occurred_at timestamp with time zone,
    source_updated_at timestamp with time zone,
    review_status text,
    visibility text,
    is_current boolean,
    is_stale boolean,
    authority text,
    source_url text,
    permalink text,
    relative_path text,
    metadata jsonb,
    lexical_score real,
    semantic_score real,
    semantic_similarity real,
    fused_score real,
    matched_queries integer,
    best_lexical_rank integer,
    semantic_rank integer
"""

# Column order must match RETURNS_COLUMNS position for position. `content` is
# included so the caller can ground an answer without a second round trip; v1
# returned only ts_headline fragments, so the model never saw the passage.
PROJECTION = """
        r.id,
        r.source_type,
        r.source_record_id,
        r.source_parent_id,
        r.source_version_id,
        r.title,
        r.content,
        r.content_hash,
        r.source_area,
        r.topic,
        r.channel,
        r.author,
        r.occurred_at,
        r.source_updated_at,
        r.review_status,
        r.visibility,
        r.is_current,
        r.is_stale,
        r.authority,
        r.source_url,
        r.permalink,
        r.relative_path,
        r.metadata,
        f.lexical_score,
        f.semantic_score,
        f.semantic_similarity,
        f.fused_score,
        f.matched_queries,
        f.best_lexical_rank,
        f.semantic_rank
"""


def _body(predicate: str) -> str:
    """Build the multi-query hybrid retrieval body.

    Lexical retrieval runs once per requested sub-query and the per-query ranks
    are fused with reciprocal rank fusion (k = 60). Semantic retrieval runs once
    against the supplied query embedding. Fusion is rank-based on purpose:
    ts_rank_cd and cosine distance are not on a comparable scale, and RRF needs
    no calibration.

    Reciprocal rank fusion is scale-blind, so a candidate that matched only
    weakly still earns rank credit comparable to a strong lexical hit
    (1/(60+rank) differs by little across the head of the list). Left unbounded,
    the semantic branch returns *every* embedded unit and floods the result set
    with rows that merely exist. Two guards prevent that: a similarity floor,
    and a ceiling on how many semantic candidates may enter the fusion at all.
    `semantic_floor` is a parameter rather than a constant because the right
    value depends on the embedding model and corpus, and must be calibrated by
    the eval harness.
    """
    return f"""
        WITH requested AS (
            SELECT DISTINCT lower(trim(candidate)) AS query_text
            FROM unnest(coalesce(search_queries, ARRAY[]::text[])) AS candidate
            WHERE trim(candidate) <> ''
        ),
        filtered AS (
            SELECT r.id
            FROM public.retrieval_units AS r
            WHERE {predicate}
              AND (requested_source_types IS NULL OR r.source_type = ANY(requested_source_types))
              AND (requested_area IS NULL OR r.source_area = requested_area)
              AND (requested_topic IS NULL OR r.topic = requested_topic)
              AND (requested_channel IS NULL OR r.channel = requested_channel)
              AND (requested_author IS NULL OR r.author = requested_author)
              AND (requested_from IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) >= requested_from)
              AND (requested_to IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) < requested_to)
        ),
        lexical AS (
            SELECT
                f.id,
                q.query_text,
                ts_rank_cd(r.search_vector, websearch_to_tsquery('simple', q.query_text), 32) AS raw_score,
                row_number() OVER (
                    PARTITION BY q.query_text
                    ORDER BY ts_rank_cd(r.search_vector, websearch_to_tsquery('simple', q.query_text), 32) DESC, f.id
                ) AS lexical_rank
            FROM filtered AS f
            JOIN public.retrieval_units AS r ON r.id = f.id
            JOIN requested AS q ON r.search_vector @@ websearch_to_tsquery('simple', q.query_text)
        ),
        lexical_fused AS (
            SELECT
                lexical.id,
                sum(1.0 / (60.0 + lexical.lexical_rank)) AS lexical_score,
                max(lexical.raw_score) AS best_raw_score,
                min(lexical.lexical_rank) AS best_lexical_rank,
                count(*) AS matched_queries
            FROM lexical
            GROUP BY lexical.id
        ),
        semantic_pool AS (
            SELECT
                f.id,
                1.0 - (e.embedding <=> query_embedding) AS similarity
            FROM filtered AS f
            JOIN public.retrieval_embeddings AS e ON e.retrieval_unit_id = f.id
            WHERE query_embedding IS NOT NULL
              AND (1.0 - (e.embedding <=> query_embedding)) >= semantic_floor
              AND e.provider = coalesce(
                    requested_provider,
                    (SELECT profile.provider FROM public.retrieval_embedding_profile() AS profile))
              AND e.model = coalesce(
                    requested_model,
                    (SELECT profile.model FROM public.retrieval_embedding_profile() AS profile))
              AND e.model_version = coalesce(
                    requested_model_version,
                    (SELECT profile.model_version FROM public.retrieval_embedding_profile() AS profile))
            ORDER BY e.embedding <=> query_embedding, f.id
            LIMIT greatest(coalesce(result_limit, 20) * 4, 40)
        ),
        semantic_ranked AS (
            SELECT
                semantic_pool.id,
                semantic_pool.similarity,
                row_number() OVER (
                    ORDER BY semantic_pool.similarity DESC, semantic_pool.id
                ) AS semantic_rank
            FROM semantic_pool
        ),
        semantic_fused AS (
            SELECT
                semantic_ranked.id,
                semantic_ranked.similarity,
                semantic_ranked.semantic_rank,
                1.0 / (60.0 + semantic_ranked.semantic_rank) AS rank_score
            FROM semantic_ranked
        ),
        fused AS (
            SELECT
                coalesce(lexical_fused.id, semantic_fused.id) AS id,
                coalesce(lexical_fused.lexical_score, 0.0) AS lexical_score,
                coalesce(semantic_fused.similarity, 0.0) AS semantic_similarity,
                coalesce(semantic_fused.rank_score, 0.0) AS semantic_score,
                coalesce(lexical_fused.best_lexical_rank, 2147483647) AS best_lexical_rank,
                semantic_fused.semantic_rank,
                coalesce(lexical_fused.matched_queries, 0) AS matched_queries,
                coalesce(lexical_fused.lexical_score, 0.0)
                    + coalesce(semantic_fused.rank_score, 0.0) AS fused_score
            FROM lexical_fused
            FULL OUTER JOIN semantic_fused ON semantic_fused.id = lexical_fused.id
        )
        SELECT {PROJECTION}
        FROM fused AS f
        JOIN public.retrieval_units AS r ON r.id = f.id
        ORDER BY
            f.fused_score DESC,
            (CASE r.authority
                WHEN 'approved_operational' THEN 0.35
                WHEN 'icu_source' THEN 0.20
                WHEN 'governance' THEN 0.20
                WHEN 'committee_record' THEN 0.10
                WHEN 'meeting_transcript' THEN 0.08
                WHEN 'meeting_summary' THEN 0.03
                ELSE 0.0
             END) DESC,
            coalesce(r.occurred_at, r.source_updated_at, r.updated_at) DESC,
            f.id
        LIMIT least(greatest(coalesce(result_limit, 20), 1), 50)
        OFFSET greatest(coalesce(result_offset, 0), 0)
    """


PUBLIC_FUNCTION_ARGS = """
    search_queries text[],
    query_embedding vector,
    requested_source_types text[] DEFAULT NULL,
    requested_area text DEFAULT NULL,
    requested_topic text DEFAULT NULL,
    requested_channel text DEFAULT NULL,
    requested_author text DEFAULT NULL,
    requested_from timestamp with time zone DEFAULT NULL,
    requested_to timestamp with time zone DEFAULT NULL,
    requested_provider text DEFAULT NULL,
    requested_model text DEFAULT NULL,
    requested_model_version text DEFAULT NULL,
    semantic_floor real DEFAULT 0.30,
    result_limit integer DEFAULT 20,
    result_offset integer DEFAULT 0
"""

AUTHENTICATED_FUNCTION_ARGS = """
    search_queries text[],
    query_embedding vector,
    requested_source_types text[] DEFAULT NULL,
    requested_area text DEFAULT NULL,
    requested_topic text DEFAULT NULL,
    requested_channel text DEFAULT NULL,
    requested_author text DEFAULT NULL,
    requested_from timestamp with time zone DEFAULT NULL,
    requested_to timestamp with time zone DEFAULT NULL,
    requested_provider text DEFAULT NULL,
    requested_model text DEFAULT NULL,
    requested_model_version text DEFAULT NULL,
    semantic_floor real DEFAULT 0.30,
    include_history boolean DEFAULT false,
    result_limit integer DEFAULT 20,
    result_offset integer DEFAULT 0
"""


def _create_function(name: str, args: str, returns: str, body: str, security: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION public.{name}(
            {args}
        )
        RETURNS TABLE ({returns})
        LANGUAGE sql STABLE {security}
        SET search_path = public, pg_temp
        AS $function$
        {body}
        $function$
    """


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Embedding profile introspection.
    #
    # The agent must embed the query with the same provider/model the index was
    # built with, otherwise cosine distance is meaningless. Anonymous callers
    # cannot read retrieval_embeddings, so this is SECURITY DEFINER; it exposes
    # only aggregate counts, never content.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.retrieval_embedding_profile()
        RETURNS TABLE (
            provider text,
            model text,
            model_version text,
            dimension integer,
            embedded_units bigint,
            indexed_units bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            WITH dominant AS (
                SELECT e.provider, e.model, e.model_version, e.dimension, count(*) AS embedded_units
                FROM public.retrieval_embeddings AS e
                GROUP BY e.provider, e.model, e.model_version, e.dimension
                ORDER BY count(*) DESC, e.model, e.model_version
                LIMIT 1
            )
            SELECT
                dominant.provider,
                dominant.model,
                dominant.model_version,
                dominant.dimension,
                dominant.embedded_units,
                (SELECT count(*) FROM public.retrieval_units AS r WHERE NOT r.is_deleted)
            FROM dominant
            UNION ALL
            SELECT NULL, NULL, NULL, NULL, 0::bigint,
                   (SELECT count(*) FROM public.retrieval_units AS r WHERE NOT r.is_deleted)
            WHERE NOT EXISTS (SELECT 1 FROM dominant)
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION public.retrieval_embedding_profile() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.retrieval_embedding_profile() TO anon, authenticated")

    # ------------------------------------------------------------------
    # 2. Anonymous-safe public retrieval. No role helper, no auth.uid().
    # ------------------------------------------------------------------
    op.execute(
        _create_function(
            "search_retrieval_units_public",
            PUBLIC_FUNCTION_ARGS,
            RETURNS_COLUMNS,
            _body(PUBLIC_PREDICATE),
            "SECURITY DEFINER",
        )
    )
    op.execute(f"REVOKE EXECUTE ON FUNCTION public.search_retrieval_units_public({PUBLIC_SIGNATURE}) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.search_retrieval_units_public({PUBLIC_SIGNATURE}) TO anon, authenticated"
    )

    # ------------------------------------------------------------------
    # 3. Authenticated multi-query hybrid retrieval. RLS stays the boundary.
    # ------------------------------------------------------------------
    op.execute(
        _create_function(
            "search_retrieval_units_multi",
            AUTHENTICATED_FUNCTION_ARGS,
            RETURNS_COLUMNS,
            _body(AUTHENTICATED_PREDICATE),
            "SECURITY INVOKER",
        )
    )
    op.execute(f"REVOKE EXECUTE ON FUNCTION public.search_retrieval_units_multi({AUTHENTICATED_SIGNATURE}) FROM PUBLIC")
    op.execute(f"REVOKE EXECUTE ON FUNCTION public.search_retrieval_units_multi({AUTHENTICATED_SIGNATURE}) FROM anon")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.search_retrieval_units_multi({AUTHENTICATED_SIGNATURE}) TO authenticated"
    )

    # ------------------------------------------------------------------
    # 4. Withdraw the two known-broken anonymous entry points.
    #
    #    Both reference has_efds_role, which anonymous callers cannot execute,
    #    so for anon they can only ever raise 42501. Revoking `anon` alone is
    #    not enough: the inherited PUBLIC grant still permits execution. The
    #    roles that can legitimately call these keep explicit grants.
    # ------------------------------------------------------------------
    for function_name, signature in (
        ("search_retrieval_units", LEGACY_LEXICAL_SIGNATURE),
        ("search_retrieval_units_semantic", LEGACY_SEMANTIC_SIGNATURE),
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{function_name}({signature}) FROM PUBLIC")
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{function_name}({signature}) FROM anon")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function_name}({signature}) TO authenticated, service_role")

    # ------------------------------------------------------------------
    # 5. Public knowledge resources must evaluate with owner rights. The view
    #    filters to approved, non-stale, public rows already.
    # ------------------------------------------------------------------
    op.execute("ALTER VIEW public.public_knowledge_resources SET (security_invoker = false)")
    op.execute("GRANT SELECT ON public.public_knowledge_resources TO anon, authenticated")


def downgrade() -> None:
    op.execute("ALTER VIEW public.public_knowledge_resources RESET (security_invoker)")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.search_retrieval_units({LEGACY_LEXICAL_SIGNATURE}) TO PUBLIC, anon")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.search_retrieval_units_semantic({LEGACY_SEMANTIC_SIGNATURE}) TO PUBLIC, anon"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.search_retrieval_units_multi({AUTHENTICATED_SIGNATURE})")
    op.execute(f"DROP FUNCTION IF EXISTS public.search_retrieval_units_public({PUBLIC_SIGNATURE})")
    op.execute("DROP FUNCTION IF EXISTS public.retrieval_embedding_profile()")
