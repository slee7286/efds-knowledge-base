"""Flag account notices that were never claimed by the scheduled worker."""

from collections.abc import Sequence

from alembic import op

revision: str = "972c30a34f67"
down_revision: str | None = "277acbd83911"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
CREATE OR REPLACE FUNCTION public.account_status_notice_health()
RETURNS TABLE(pending bigint, sending bigint, accepted_7d bigint,
              needs_attention bigint, oldest_pending_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT public.has_efds_role('admin') THEN
    RAISE EXCEPTION 'admin required' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
    SELECT count(*) FILTER (WHERE n.state = 'pending'),
           count(*) FILTER (WHERE n.state = 'sending'
             AND n.attempted_at >= now() - interval '2 minutes'),
           count(*) FILTER (WHERE n.state = 'accepted'
             AND n.finished_at >= now() - interval '7 days'),
           count(*) FILTER (WHERE n.state IN ('rejected','uncertain')
             OR (n.state = 'sending'
                 AND n.attempted_at < now() - interval '2 minutes')
             OR (n.state = 'pending'
                 AND n.created_at < now() - interval '5 minutes')),
           min(n.created_at) FILTER (WHERE n.state = 'pending')
    FROM public.account_status_notices n;
END;
$$;
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    raise RuntimeError("Account email alerts require a reviewed forward migration")
