"""Include the website ticket description in Slack root previews."""
from collections.abc import Sequence

from alembic import op

revision: str = "0027_ticket_slack_description"
down_revision: str | None = "0026_ticket_slack_edge_worker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
CREATE OR REPLACE FUNCTION public.enqueue_website_ticket_slack_event()
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
    'title',v_ticket.title,
    'description',v_ticket.description,
    'execution_status',v_ticket.execution_status,
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

-- Refresh queued payloads; already-posted roots update on the next ticket change.
UPDATE public.ticket_slack_outbox o
SET snapshot = o.snapshot || jsonb_build_object('description', r.description)
FROM public.operational_records r
WHERE r.id=o.ticket_id AND o.state <> 'posted';
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    raise RuntimeError("Description snapshots are forward-only; keep the enqueue trigger compatible")
