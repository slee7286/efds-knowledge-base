"""Optional, audited progress for each assignee on a committee ticket.

Revision ID: 82afe16026f6
Revises: c07748e4a2fe
Create Date: 2026-09-24 22:53:32.111005
"""
from collections.abc import Sequence

from alembic import op


revision: str = "82afe16026f6"
down_revision: str | None = "c07748e4a2fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROGRESS_SQL = """
CREATE FUNCTION public.set_committee_ticket_individual_progress(
  p_ticket_id uuid,
  p_expected_version integer,
  p_enabled boolean DEFAULT NULL,
  p_officer_id uuid DEFAULT NULL,
  p_status text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
  v_actor public.profiles%ROWTYPE;
  v_ticket public.operational_records%ROWTYPE;
  v_progress jsonb;
  v_statuses jsonb;
  v_previous_status text;
  v_count integer;
  v_changes jsonb;
  v_action text;
BEGIN
  SELECT * INTO v_actor FROM public.profiles
    WHERE auth_user_id=auth.uid() AND active AND access_role IN ('committee','admin')
    LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized'; END IF;
  SELECT * INTO v_ticket FROM public.operational_records WHERE id=p_ticket_id FOR UPDATE;
  IF NOT FOUND OR v_ticket.record_type<>'action_item' OR v_ticket.review_status<>'approved'
     OR NOT v_ticket.is_current OR v_ticket.visibility<>'committee' THEN
    RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='ticket_unavailable';
  END IF;
  IF p_expected_version IS NULL OR p_expected_version<>v_ticket.review_version THEN
    RAISE EXCEPTION USING ERRCODE='P0006', MESSAGE='concurrency_conflict';
  END IF;
  SELECT count(*) INTO v_count FROM public.operational_ticket_assignees WHERE ticket_id=p_ticket_id;
  v_progress:=CASE WHEN jsonb_typeof(v_ticket.metadata->'individual_progress')='object'
    THEN v_ticket.metadata->'individual_progress' ELSE '{}'::jsonb END;

  IF p_enabled IS NOT NULL THEN
    IF p_officer_id IS NOT NULL OR p_status IS NOT NULL OR (p_enabled AND v_count<2) THEN
      RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='individual_progress_requires_two_assignees';
    END IF;
    IF coalesce((v_progress->>'enabled')::boolean,false)=p_enabled THEN
      RETURN jsonb_build_object('review_version',v_ticket.review_version,'unchanged',true);
    END IF;
    IF p_enabled THEN
      SELECT coalesce(jsonb_object_agg(a.officer_id::text,
        coalesce(v_progress #>> ARRAY['statuses',a.officer_id::text],'open')), '{}'::jsonb)
        INTO v_statuses FROM public.operational_ticket_assignees a WHERE a.ticket_id=p_ticket_id;
    ELSE
      v_statuses:=coalesce(v_progress->'statuses','{}'::jsonb);
    END IF;
    v_progress:=jsonb_build_object('enabled',p_enabled,'statuses',v_statuses);
    v_action:='committee_progress_mode';
    v_changes:=jsonb_build_object('enabled',p_enabled);
  ELSE
    IF p_officer_id IS NULL OR p_status NOT IN ('open','in_progress','blocked','completed')
       OR v_count<2 OR coalesce((v_progress->>'enabled')::boolean,false) IS NOT TRUE
       OR NOT EXISTS (SELECT 1 FROM public.operational_ticket_assignees
                      WHERE ticket_id=p_ticket_id AND officer_id=p_officer_id) THEN
      RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='invalid_individual_progress';
    END IF;
    v_previous_status:=coalesce(v_progress #>> ARRAY['statuses',p_officer_id::text],'open');
    IF v_previous_status=p_status THEN
      RETURN jsonb_build_object('review_version',v_ticket.review_version,'unchanged',true);
    END IF;
    v_progress:=jsonb_set(v_progress,ARRAY['statuses',p_officer_id::text],to_jsonb(p_status),true);
    v_action:='committee_assignee_progress';
    v_changes:=jsonb_build_object('officer_id',p_officer_id,'previous_status',v_previous_status,'status',p_status);
  END IF;

  UPDATE public.operational_records SET
    metadata=jsonb_set(coalesce(metadata,'{}'::jsonb),'{individual_progress}',v_progress,true),
    review_version=review_version+1,updated_at=now()
    WHERE id=p_ticket_id RETURNING * INTO v_ticket;
  INSERT INTO public.operational_review_events(
    operational_record_id,action,previous_review_status,new_review_status,reviewer_profile_id,changes
  ) VALUES (p_ticket_id,v_action,'approved','approved',v_actor.id,v_changes);
  RETURN jsonb_build_object('review_version',v_ticket.review_version,'unchanged',false);
END;
$function$;
REVOKE ALL ON FUNCTION public.set_committee_ticket_individual_progress(uuid,integer,boolean,uuid,text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.set_committee_ticket_individual_progress(uuid,integer,boolean,uuid,text) TO authenticated;
"""


HISTORY_SQL = """
CREATE OR REPLACE FUNCTION public.committee_ticket_history(p_ticket_id uuid DEFAULT NULL)
RETURNS TABLE(event_id uuid, ticket_id uuid, action text, actor_name text, changes jsonb, occurred_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $function$
BEGIN
  IF auth.uid() IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.profiles caller
    WHERE caller.auth_user_id=auth.uid() AND caller.active
      AND caller.access_role IN ('committee','admin')
  ) THEN RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized'; END IF;

  RETURN QUERY
  SELECT e.id, e.operational_record_id, e.action,
    coalesce(nullif(btrim(actor.full_name),''),nullif(btrim(o.name),''),
      nullif(split_part(actor.email,'@',1),''),'Committee member'),
    e.changes,e.created_at
  FROM public.operational_review_events e
  JOIN public.operational_records ticket ON ticket.id=e.operational_record_id
  JOIN public.profiles actor ON actor.id=e.reviewer_profile_id
  LEFT JOIN public.officers o ON o.id=actor.officer_id
  WHERE ticket.record_type='action_item' AND ticket.review_status='approved'
    AND ticket.is_current AND ticket.visibility='committee'
    AND e.action IN ('committee_create','committee_update','committee_assign',
      'committee_status','committee_reminder','committee_progress_mode','committee_assignee_progress')
    AND (p_ticket_id IS NULL OR ticket.id=p_ticket_id)
  ORDER BY e.created_at DESC LIMIT 2000;
END;
$function$;
REVOKE ALL ON FUNCTION public.committee_ticket_history(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.committee_ticket_history(uuid) TO authenticated;
"""


def upgrade() -> None:
    op.execute(PROGRESS_SQL)
    op.execute(HISTORY_SQL)


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.set_committee_ticket_individual_progress(uuid,integer,boolean,uuid,text) FROM authenticated")
    op.execute("DROP FUNCTION public.set_committee_ticket_individual_progress(uuid,integer,boolean,uuid,text)")
    op.execute(HISTORY_SQL.replace(",\n      'committee_status','committee_reminder','committee_progress_mode','committee_assignee_progress'", ",\n      'committee_status','committee_reminder'"))
