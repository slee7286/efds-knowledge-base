"""Transactional outbox for new, website-origin committee tickets only.

No existing records or events are scanned or backfilled. A create event after the
migration cutover enrolls a ticket; only enrolled tickets can emit later changes.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0025_website_ticket_slack_outbox"
down_revision: str | None = "0024_committee_ticket_reference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTBOX_SQL = """
CREATE TABLE public.ticket_slack_cutover (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  installed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO public.ticket_slack_cutover (singleton) VALUES (true);
ALTER TABLE public.ticket_slack_cutover ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ticket_slack_cutover FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.ticket_slack_cutover TO service_role;

CREATE TABLE public.ticket_slack_outbox (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id uuid NOT NULL UNIQUE REFERENCES public.operational_review_events(id) ON DELETE CASCADE,
  ticket_id uuid NOT NULL REFERENCES public.operational_records(id) ON DELETE CASCADE,
  action text NOT NULL CHECK (action IN ('committee_create','committee_assign','committee_status','committee_update')),
  snapshot jsonb NOT NULL,
  channel text NOT NULL DEFAULT 'C0BQPDP5T44',
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','posted','uncertain')),
  slack_ts text,
  attempts integer NOT NULL DEFAULT 0,
  retry_at timestamptz NOT NULL DEFAULT now(),
  claimed_at timestamptz,
  delivered_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((state='posted') = (slack_ts IS NOT NULL))
);
CREATE UNIQUE INDEX ticket_slack_outbox_one_create ON public.ticket_slack_outbox(ticket_id)
  WHERE action='committee_create';
CREATE INDEX ticket_slack_outbox_ready ON public.ticket_slack_outbox(retry_at,id)
  WHERE state IN ('pending','sending','uncertain');
CREATE INDEX ticket_slack_outbox_ticket_order ON public.ticket_slack_outbox(ticket_id,id);
ALTER TABLE public.ticket_slack_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ticket_slack_outbox FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.ticket_slack_outbox TO service_role;

CREATE TABLE public.ticket_slack_roots (
  ticket_id uuid PRIMARY KEY REFERENCES public.operational_records(id) ON DELETE CASCADE,
  create_event_id uuid NOT NULL UNIQUE REFERENCES public.ticket_slack_outbox(event_id) ON DELETE CASCADE,
  channel text NOT NULL,
  root_ts text NOT NULL
);
ALTER TABLE public.ticket_slack_roots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ticket_slack_roots FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.ticket_slack_roots TO service_role;

CREATE FUNCTION public.enqueue_website_ticket_slack_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $function$
DECLARE
  v_ticket public.operational_records%ROWTYPE;
  v_snapshot jsonb;
BEGIN
  IF NEW.action NOT IN ('committee_create','committee_assign','committee_status','committee_update') THEN
    RETURN NEW;
  END IF;
  SELECT * INTO v_ticket FROM public.operational_records WHERE id=NEW.operational_record_id;
  IF NOT FOUND OR v_ticket.record_type <> 'action_item'
     OR v_ticket.review_status <> 'approved' OR NOT v_ticket.is_current
     OR v_ticket.visibility <> 'committee'
     OR v_ticket.metadata->>'origin' <> 'committee_dashboard' THEN
    RETURN NEW;
  END IF;
  IF NEW.action='committee_create' THEN
    IF NOT (v_ticket.created_at >= (SELECT installed_at FROM public.ticket_slack_cutover WHERE singleton)) THEN
      RETURN NEW;
    END IF;
  ELSIF NOT EXISTS (SELECT 1 FROM public.ticket_slack_outbox
                    WHERE ticket_id=v_ticket.id AND action='committee_create') THEN
    RETURN NEW;
  END IF;
  SELECT jsonb_build_object(
    'title',v_ticket.title, 'execution_status',v_ticket.execution_status,
    'due_at',v_ticket.due_at,
    'assignees',coalesce((SELECT jsonb_agg(o.name ORDER BY o.name, o.id)
      FROM public.operational_ticket_assignees a
      JOIN public.officers o ON o.id=a.officer_id
      WHERE a.ticket_id=v_ticket.id),'[]'::jsonb)
  ) INTO v_snapshot;
  INSERT INTO public.ticket_slack_outbox(event_id,ticket_id,action,snapshot)
    VALUES (NEW.id,v_ticket.id,NEW.action,v_snapshot);
  RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.enqueue_website_ticket_slack_event() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER enqueue_website_ticket_slack_event
AFTER INSERT ON public.operational_review_events
FOR EACH ROW EXECUTE FUNCTION public.enqueue_website_ticket_slack_event();

-- create_committee_ticket_from_evidence calls mutate_committee_ticket('create')
-- before changing origin from committee_dashboard to committee_agent_suggestion.
-- Both changes happen in one transaction, so withdraw that transient enqueue
-- before a worker can observe it. Do not erase a previously published root.
CREATE FUNCTION public.discard_suggested_ticket_slack_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $function$
BEGIN
  IF OLD.metadata->>'origin'='committee_dashboard'
     AND NEW.metadata->>'origin'='committee_agent_suggestion' THEN
    DELETE FROM public.ticket_slack_outbox
    WHERE ticket_id=NEW.id AND action='committee_create' AND state='pending'
      AND NOT EXISTS (SELECT 1 FROM public.ticket_slack_roots WHERE ticket_id=NEW.id);
  END IF;
  RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.discard_suggested_ticket_slack_event() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER discard_suggested_ticket_slack_event
AFTER UPDATE OF metadata ON public.operational_records
FOR EACH ROW EXECUTE FUNCTION public.discard_suggested_ticket_slack_event();
"""


def upgrade() -> None:
    op.execute(OUTBOX_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS discard_suggested_ticket_slack_event ON public.operational_records")
    op.execute("DROP FUNCTION IF EXISTS public.discard_suggested_ticket_slack_event()")
    op.execute("DROP TRIGGER IF EXISTS enqueue_website_ticket_slack_event ON public.operational_review_events")
    op.execute("DROP FUNCTION IF EXISTS public.enqueue_website_ticket_slack_event()")
    op.execute("DROP TABLE IF EXISTS public.ticket_slack_roots")
    op.execute("DROP TABLE IF EXISTS public.ticket_slack_outbox")
    op.execute("DROP TABLE IF EXISTS public.ticket_slack_cutover")
