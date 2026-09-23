"""Expose a restricted, named audit trail for committee ticket changes."""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_committee_ticket_history"
down_revision: str | None = "0019_restrict_profile_trigger_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Committee members cannot SELECT other people's profiles. This narrow RPC
    # returns a display name only for an audited edit to a visible ticket.
    op.execute("""
CREATE FUNCTION public.committee_ticket_history(p_ticket_id uuid DEFAULT NULL)
RETURNS TABLE(event_id uuid, ticket_id uuid, action text, actor_name text, changes jsonb, occurred_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
BEGIN
  IF auth.uid() IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.profiles caller
    WHERE caller.auth_user_id = auth.uid() AND caller.active
      AND caller.access_role IN ('committee', 'admin')
  ) THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized';
  END IF;

  RETURN QUERY
  SELECT e.id, e.operational_record_id, e.action,
    coalesce(nullif(btrim(actor.full_name), ''), nullif(btrim(o.name), ''),
      nullif(split_part(actor.email, '@', 1), ''), 'Committee member'),
    e.changes, e.created_at
  FROM public.operational_review_events e
  JOIN public.operational_records ticket ON ticket.id = e.operational_record_id
  JOIN public.profiles actor ON actor.id = e.reviewer_profile_id
  LEFT JOIN public.officers o ON o.id = actor.officer_id
  WHERE ticket.record_type = 'action_item'
    AND ticket.review_status = 'approved' AND ticket.is_current
    AND ticket.visibility = 'committee'
    AND e.action IN ('committee_create', 'committee_update', 'committee_assign', 'committee_status')
    AND (p_ticket_id IS NULL OR ticket.id = p_ticket_id)
  ORDER BY e.created_at DESC
  LIMIT 2000;
END;
$function$;
REVOKE ALL ON FUNCTION public.committee_ticket_history(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.committee_ticket_history(uuid) TO authenticated;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION public.committee_ticket_history(uuid)")
