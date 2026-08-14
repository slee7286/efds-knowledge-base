"""Add Meetily meeting artifacts, transcript segments, and admin-only access.

Revision ID: 0011_meetily_meeting_ingestion
Revises: 0010_unified_retrieval
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011_meetily_meeting_ingestion"
down_revision: str | None = "0010_unified_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
jsonb = postgresql.JSONB()


def upgrade() -> None:
    op.add_column("meetings", sa.Column("source_type", sa.Text(), nullable=False, server_default=sa.text("'manual'")))
    op.add_column("meetings", sa.Column("external_meeting_id", sa.Text()))
    op.add_column("meetings", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("meetings", sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.add_column("meetings", sa.Column("duration_seconds", sa.Integer()))
    op.add_column("meetings", sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")))
    op.add_column("meetings", sa.Column("source_created_at", sa.DateTime(timezone=True)))
    op.add_column("meetings", sa.Column("source_updated_at", sa.DateTime(timezone=True)))
    op.add_column("meetings", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("meetings", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("meetings", sa.Column("last_changed_at", sa.DateTime(timezone=True)))
    op.add_column("meetings", sa.Column("is_missing", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_unique_constraint("uq_meetings_source_external_id", "meetings", ["source_type", "external_meeting_id"])
    op.create_index("ix_meetings_source_identity", "meetings", ["source_type", "external_meeting_id"])
    op.create_index("ix_meetings_started_at", "meetings", ["started_at"])
    op.create_index("ix_meetings_missing", "meetings", ["is_missing"])

    op.create_table(
        "meeting_artifacts",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", uuid, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text()),
        sa.Column("source_reference", sa.Text()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("format", sa.Text()),
        sa.Column("source_created_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("generated_by", sa.Text()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default=sa.text("'source_generated'")),
        sa.Column("summary_template", sa.Text()),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("meeting_id", "artifact_type", "content_hash", name="uq_meeting_artifact_version"),
    )
    op.create_index("ix_meeting_artifacts_current", "meeting_artifacts", ["meeting_id", "artifact_type", "is_current"])
    op.create_index("ix_meeting_artifacts_hash", "meeting_artifacts", ["content_hash"])

    op.create_table(
        "meeting_transcript_segments",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", uuid, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", uuid, sa.ForeignKey("meeting_artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer()),
        sa.Column("end_ms", sa.Integer()),
        sa.Column("speaker", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("artifact_id", "sequence", name="uq_meeting_transcript_segment_order"),
    )
    op.create_index("ix_meeting_transcript_segments_meeting", "meeting_transcript_segments", ["meeting_id", "sequence"])

    op.create_table(
        "meeting_source_changes",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", uuid, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("previous_hash", sa.Text()),
        sa.Column("new_hash", sa.Text()),
        sa.Column("previous_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ingestion_run_id", uuid, sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL")),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_meeting_source_changes_meeting", "meeting_source_changes", ["meeting_id", "detected_at"])
    op.create_index("ix_meeting_source_changes_type", "meeting_source_changes", ["change_type", "detected_at"])

    # Existing 0004 exposed the placeholder meetings table to committee users.
    # Meetily content is sensitive, so the evolved meeting source is admin-only.
    op.execute("DROP POLICY IF EXISTS meetings_select_committee ON public.meetings")
    op.execute("CREATE POLICY meetings_select_admin ON public.meetings FOR SELECT TO authenticated USING (public.has_efds_role('admin'))")
    for table in ("meeting_artifacts", "meeting_transcript_segments", "meeting_source_changes"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT ON public.{table} TO authenticated")
        op.execute(f"CREATE POLICY {table}_select_admin ON public.{table} FOR SELECT TO authenticated USING (public.has_efds_role('admin'))")
    op.execute("GRANT SELECT ON public.meetings TO authenticated")

    # Rebuild the existing retrieval RPC with meeting source types explicitly
    # excluded from non-admin access and with meeting authority adjustments.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.search_retrieval_units(
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
          occurred_at timestamptz, source_updated_at timestamptz, review_status text,
          visibility text, is_current boolean, is_stale boolean, source_url text,
          permalink text, relative_path text, content_hash text, metadata jsonb
        )
        LANGUAGE sql STABLE SECURITY INVOKER
        SET search_path = public, pg_temp
        AS $$
          WITH query AS (
            SELECT websearch_to_tsquery('simple', trim(search_query)) AS tsq
          ), ranked AS (
            SELECT r.*,
              ts_rank_cd(r.search_vector, q.tsq, 32)
                + CASE WHEN lower(r.title) = lower(trim(search_query)) THEN 2.0 ELSE 0.0 END
                + CASE WHEN r.title ILIKE '%' || trim(search_query) || '%' THEN 0.35 ELSE 0.0 END
                + CASE r.authority
                    WHEN 'approved_operational' THEN 0.35
                    WHEN 'icu_source' THEN 0.20
                    WHEN 'governance' THEN 0.20
                    WHEN 'committee_record' THEN 0.10
                    WHEN 'meeting_transcript' THEN 0.08
                    WHEN 'meeting_summary' THEN 0.03
                    ELSE 0.0
                  END
                + CASE WHEN r.is_current THEN 0.15 ELSE 0.0 END
                + CASE WHEN r.source_type = 'slack_message'
                       THEN greatest(0.0, 0.05 * (1.0 - extract(epoch FROM (now() - coalesce(r.occurred_at, r.source_updated_at, r.updated_at))) / 31536000.0))
                       ELSE 0.0 END AS computed_score
            FROM public.retrieval_units AS r
            CROSS JOIN query AS q
            WHERE trim(search_query) <> ''
              AND r.search_vector @@ q.tsq
              AND (CAST(requested_source_types AS text[]) IS NULL OR r.source_type = ANY(CAST(requested_source_types AS text[])))
              AND (CAST(requested_area AS text) IS NULL OR r.source_area = CAST(requested_area AS text))
              AND (CAST(requested_topic AS text) IS NULL OR r.topic = CAST(requested_topic AS text))
              AND (CAST(requested_channel AS text) IS NULL OR r.channel = CAST(requested_channel AS text))
              AND (CAST(requested_author AS text) IS NULL OR r.author = CAST(requested_author AS text))
              AND (CAST(requested_from AS timestamptz) IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) >= CAST(requested_from AS timestamptz))
              AND (CAST(requested_to AS timestamptz) IS NULL OR coalesce(r.occurred_at, r.source_updated_at, r.updated_at) < CAST(requested_to AS timestamptz))
              AND (include_history AND public.has_efds_role('admin') OR r.is_current)
              AND (
                public.has_efds_role('admin')
                OR (r.source_type NOT IN ('document', 'slack_message', 'meeting_transcript', 'meeting_summary', 'meeting_notes')
                    AND r.review_status = 'approved' AND NOT r.is_stale AND NOT r.is_deleted
                    AND (r.visibility = 'public'
                      OR (r.visibility = 'member' AND public.has_efds_role('member'))
                      OR (r.visibility = 'committee' AND public.has_efds_role('committee'))))
              )
          )
          SELECT id, source_type, source_record_id, source_parent_id, source_version_id, title,
            ts_headline('simple', content, websearch_to_tsquery('simple', trim(search_query)),
              'MaxFragments=2,MaxWords=50,MinWords=15,StartSel=<mark>,StopSel=</mark>'),
            computed_score::real, source_area, topic, channel, author, occurred_at,
            source_updated_at, review_status, visibility, is_current, is_stale,
            source_url, permalink, relative_path, content_hash, metadata
          FROM ranked
          ORDER BY computed_score DESC, coalesce(occurred_at, source_updated_at, updated_at) DESC, id
          LIMIT least(greatest(coalesce(result_limit, 20), 1), 50)
          OFFSET greatest(coalesce(result_offset, 0), 0)
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS meetings_select_admin ON public.meetings")
    op.execute("CREATE POLICY meetings_select_committee ON public.meetings FOR SELECT TO authenticated USING (public.has_efds_role('committee'))")
    for table in ("meeting_artifacts", "meeting_transcript_segments", "meeting_source_changes"):
        op.execute(f"DROP POLICY IF EXISTS {table}_select_admin ON public.{table}")
        op.execute(f"REVOKE SELECT ON public.{table} FROM authenticated")
    op.drop_index("ix_meeting_source_changes_type", table_name="meeting_source_changes")
    op.drop_index("ix_meeting_source_changes_meeting", table_name="meeting_source_changes")
    op.drop_table("meeting_source_changes")
    op.drop_index("ix_meeting_transcript_segments_meeting", table_name="meeting_transcript_segments")
    op.drop_table("meeting_transcript_segments")
    op.drop_index("ix_meeting_artifacts_hash", table_name="meeting_artifacts")
    op.drop_index("ix_meeting_artifacts_current", table_name="meeting_artifacts")
    op.drop_table("meeting_artifacts")
    op.drop_index("ix_meetings_missing", table_name="meetings")
    op.drop_index("ix_meetings_started_at", table_name="meetings")
    op.drop_index("ix_meetings_source_identity", table_name="meetings")
    op.drop_constraint("uq_meetings_source_external_id", "meetings", type_="unique")
    for column in ("is_missing", "last_changed_at", "last_seen_at", "first_seen_at", "source_updated_at", "source_created_at", "status", "duration_seconds", "ended_at", "started_at", "external_meeting_id", "source_type"):
        op.drop_column("meetings", column)
