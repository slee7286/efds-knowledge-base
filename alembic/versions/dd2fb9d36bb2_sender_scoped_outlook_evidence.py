"""Store sender-allowlisted Outlook evidence without mailbox credentials.

Revision ID: dd2fb9d36bb2
Revises: 48dd27e949db
Create Date: 2026-09-23 20:36:59.517074
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'dd2fb9d36bb2'
down_revision: Union[str, None] = '48dd27e949db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outlook_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("mailbox_graph_id", sa.Text(), nullable=False),
        sa.Column("graph_message_id", sa.Text(), nullable=False),
        sa.Column("internet_message_id", sa.Text()),
        sa.Column("sender_address", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("web_link", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("missing_observations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_missing_checked_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("mailbox_graph_id", "graph_message_id", name="uq_outlook_message_mailbox_graph_id"),
        sa.CheckConstraint("sender_address = lower(sender_address)", name="ck_outlook_messages_sender_lowercase"),
        sa.CheckConstraint("missing_observations >= 0", name="ck_outlook_messages_missing_observations"),
    )
    op.create_index("ix_outlook_messages_mailbox_received", "outlook_messages", ["mailbox_graph_id", "received_at"])
    op.create_index("ix_outlook_messages_sender_received", "outlook_messages", ["sender_address", "received_at"])
    op.create_table(
        "outlook_sync_checkpoints",
        sa.Column("mailbox_graph_id", sa.Text(), primary_key=True),
        sa.Column("sender_address", sa.Text(), primary_key=True),
        sa.Column("last_received_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_at", sa.DateTime(timezone=True)),
    )
    for table in ("outlook_messages", "outlook_sync_checkpoints"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_admin_select ON public.{table} FOR SELECT TO authenticated USING (public.has_efds_role('admin'))")
        op.execute(f"GRANT SELECT ON public.{table} TO authenticated")
        op.execute(f"REVOKE ALL ON public.{table} FROM anon")
    op.execute("""
CREATE FUNCTION public.admin_outlook_ticket_evidence_v1(result_limit integer DEFAULT 10)
RETURNS TABLE (
  retrieval_unit_id uuid, source_type text, source_record_id text,
  source_parent_id text, source_version_id text, title text, snippet text,
  score real, source_area text, topic text, channel text, author text,
  occurred_at timestamptz, source_updated_at timestamptz,
  review_status text, visibility text, is_current boolean, is_stale boolean,
  source_url text, permalink text, relative_path text, content_hash text,
  metadata jsonb, authority text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $function$
BEGIN
  IF (SELECT auth.uid()) IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.profiles caller
    WHERE caller.auth_user_id=(SELECT auth.uid()) AND caller.active
      AND caller.access_role='admin'
  ) THEN
    RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized';
  END IF;
  RETURN QUERY
  SELECT r.id, 'outlook_message'::text, r.source_record_id,
    r.source_parent_id, r.source_version_id, m.subject,
    left(r.content, 1400)::text,
    (1.0 - greatest(0.0, extract(epoch FROM (now()-m.received_at)) / 7776000.0))::real,
    NULL::text, NULL::text, NULL::text, m.sender_address,
    m.received_at, m.source_modified_at,
    'source_generated'::text, 'internal'::text,
    r.is_current, r.is_stale, m.web_link, NULL::text,
    NULL::text, r.content_hash,
    jsonb_build_object('body_truncated',m.body_truncated),
    'outlook_mail'::text
  FROM public.retrieval_units r
  JOIN public.outlook_messages m ON m.id::text=r.source_record_id
  WHERE r.source_type='outlook_message' AND r.is_current AND NOT r.is_stale
    AND NOT r.is_deleted AND NOT m.is_deleted
    AND r.source_version_id=m.content_hash AND r.chunk_index=0
    AND m.received_at >= now()-interval '90 days'
  ORDER BY m.received_at DESC
  LIMIT least(greatest(coalesce(result_limit,10),1),12);
END;
$function$;
REVOKE ALL ON FUNCTION public.admin_outlook_ticket_evidence_v1(integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_outlook_ticket_evidence_v1(integer)
  TO authenticated;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION public.admin_outlook_ticket_evidence_v1(integer)")
    op.drop_table("outlook_sync_checkpoints")
    op.drop_index("ix_outlook_messages_sender_received", table_name="outlook_messages")
    op.drop_index("ix_outlook_messages_mailbox_received", table_name="outlook_messages")
    op.drop_table("outlook_messages")
