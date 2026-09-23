"""Give committee tickets multiple assignees and audited committee mutations."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017_committee_tickets"
down_revision: str | None = "0016_email_provider_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "operational_ticket_assignees",
        sa.Column("ticket_id", uuid, sa.ForeignKey("operational_records.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("officer_id", uuid, sa.ForeignKey("officers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_by_profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_operational_ticket_assignees_officer", "operational_ticket_assignees", ["officer_id"])
    op.create_index("ix_operational_ticket_assignees_actor", "operational_ticket_assignees", ["assigned_by_profile_id"])
    op.create_index(
        "uq_operational_records_slack_ticket_source", "operational_records",
        [sa.text("(metadata ->> 'slack_message_id')")], unique=True,
        postgresql_where=sa.text("record_type = 'action_item' AND metadata ? 'slack_message_id'"),
    )
    op.execute("ALTER TABLE public.operational_ticket_assignees ENABLE ROW LEVEL SECURITY")
    op.execute("""
      DROP POLICY operational_records_select_committee ON public.operational_records;
      CREATE POLICY operational_records_select_committee ON public.operational_records
      FOR SELECT TO authenticated USING (
        review_status='approved' AND is_current
        AND visibility IN ('committee','member','public')
        AND (SELECT public.has_efds_role('committee'))
      );
      CREATE POLICY operational_ticket_assignees_select ON public.operational_ticket_assignees
      FOR SELECT TO authenticated USING (
        EXISTS (SELECT 1 FROM public.operational_records r WHERE r.id=ticket_id AND r.record_type='action_item')
      );
      CREATE POLICY operational_review_events_select_committee ON public.operational_review_events
      FOR SELECT TO authenticated USING (
        EXISTS (
          SELECT 1 FROM public.operational_records r
          WHERE r.id=operational_record_id AND r.record_type='action_item'
            AND r.visibility='committee' AND r.review_status='approved' AND r.is_current
            AND (SELECT public.has_efds_role('committee'))
        )
      );
      GRANT SELECT ON public.operational_ticket_assignees TO authenticated;
      REVOKE ALL ON public.operational_ticket_assignees FROM anon;
    """)
    op.execute("""
CREATE OR REPLACE FUNCTION public.mutate_committee_ticket(
  p_action text,
  p_ticket_id uuid DEFAULT NULL,
  p_expected_version integer DEFAULT NULL,
  p_patch jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
DECLARE
  v_actor public.profiles%ROWTYPE;
  v_ticket public.operational_records%ROWTYPE;
  v_before jsonb;
  v_after jsonb;
  v_ids uuid[] := ARRAY[]::uuid[];
  v_status text;
BEGIN
  SELECT * INTO v_actor FROM public.profiles
  WHERE auth_user_id=auth.uid() AND active AND access_role IN ('committee','admin') LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized'; END IF;
  IF p_patch IS NULL OR jsonb_typeof(p_patch) <> 'object' THEN
    RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
  END IF;

  IF p_action='create' THEN
    IF p_ticket_id IS NOT NULL OR p_expected_version IS NOT NULL OR
       EXISTS (SELECT 1 FROM jsonb_object_keys(p_patch) AS fields(key) WHERE key NOT IN ('title','description','workstream','priority','due_at','assignee_ids')) OR
       nullif(btrim(p_patch->>'title'),'') IS NULL OR length(p_patch->>'title') > 300 OR
       length(coalesce(p_patch->>'description','')) > 5000 OR
       length(coalesce(p_patch->>'workstream','')) > 100 OR
       coalesce(p_patch->>'priority','medium') NOT IN ('low','medium','high','urgent') THEN
      RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
    END IF;
    INSERT INTO public.operational_records(
      record_type,title,description,workstream,priority,due_at,execution_status,
      review_status,visibility,created_by_profile_id,reviewed_by_profile_id,reviewed_at,metadata
    ) VALUES (
      'action_item',btrim(p_patch->>'title'),nullif(btrim(p_patch->>'description'),''),
      nullif(btrim(p_patch->>'workstream'),''),coalesce(p_patch->>'priority','medium'),
      nullif(p_patch->>'due_at','')::timestamptz,'open',
      'approved','committee',v_actor.id,v_actor.id,now(),
      jsonb_build_object('origin','committee_dashboard')
    ) RETURNING * INTO v_ticket;
    p_ticket_id := v_ticket.id;
    v_before := NULL;
  ELSE
    SELECT * INTO v_ticket FROM public.operational_records WHERE id=p_ticket_id FOR UPDATE;
    IF NOT FOUND OR v_ticket.record_type <> 'action_item' THEN
      RAISE EXCEPTION USING ERRCODE='P0003', MESSAGE='ticket_not_found';
    END IF;
    IF v_ticket.review_status <> 'approved' OR NOT v_ticket.is_current OR
       (v_actor.access_role='committee' AND v_ticket.visibility <> 'committee') THEN
      RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized';
    END IF;
    IF p_expected_version IS NULL OR p_expected_version <> v_ticket.review_version THEN
      RAISE EXCEPTION USING ERRCODE='P0006', MESSAGE='concurrency_conflict';
    END IF;
    v_before := to_jsonb(v_ticket);
    IF p_action='update' THEN
      IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_patch) AS fields(key) WHERE key NOT IN ('title','description','workstream','priority','due_at')) OR
         (p_patch ? 'title' AND (nullif(btrim(p_patch->>'title'),'') IS NULL OR length(p_patch->>'title') > 300)) OR
         length(coalesce(p_patch->>'description','')) > 5000 OR
         length(coalesce(p_patch->>'workstream','')) > 100 OR
         (p_patch ? 'priority' AND p_patch->>'priority' NOT IN ('low','medium','high','urgent')) THEN
        RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
      END IF;
      UPDATE public.operational_records SET
        title=CASE WHEN p_patch ? 'title' THEN btrim(p_patch->>'title') ELSE title END,
        description=CASE WHEN p_patch ? 'description' THEN nullif(btrim(p_patch->>'description'),'') ELSE description END,
        workstream=CASE WHEN p_patch ? 'workstream' THEN nullif(btrim(p_patch->>'workstream'),'') ELSE workstream END,
        priority=CASE WHEN p_patch ? 'priority' THEN p_patch->>'priority' ELSE priority END,
        due_at=CASE WHEN p_patch ? 'due_at' THEN nullif(p_patch->>'due_at','')::timestamptz ELSE due_at END,
        review_version=review_version+1,updated_at=now()
      WHERE id=p_ticket_id;
    ELSIF p_action='status' THEN
      IF (SELECT count(*) FROM jsonb_object_keys(p_patch)) <> 1 OR NOT (p_patch ? 'execution_status') THEN
        RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
      END IF;
      v_status := p_patch->>'execution_status';
      IF v_status NOT IN ('open','in_progress','blocked','completed','cancelled') THEN
        RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
      END IF;
      UPDATE public.operational_records SET
        execution_status=v_status,
        completed_at=CASE WHEN v_status='completed' THEN now() ELSE NULL END,
        review_version=review_version+1,updated_at=now()
      WHERE id=p_ticket_id;
    ELSIF p_action <> 'assign' THEN
      RAISE EXCEPTION USING ERRCODE='P0004', MESSAGE='invalid_transition';
    END IF;
  END IF;

  IF p_action IN ('create','assign') THEN
    IF p_action='assign' AND ((SELECT count(*) FROM jsonb_object_keys(p_patch)) <> 1 OR NOT (p_patch ? 'assignee_ids')) THEN
      RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
    END IF;
    IF p_patch ? 'assignee_ids' THEN
      IF jsonb_typeof(p_patch->'assignee_ids') <> 'array' OR jsonb_array_length(p_patch->'assignee_ids') > 20 THEN
        RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
      END IF;
      SELECT coalesce(array_agg(DISTINCT item::uuid),ARRAY[]::uuid[]) INTO v_ids
      FROM jsonb_array_elements_text(p_patch->'assignee_ids') AS x(item);
      IF (SELECT count(*) FROM public.officers WHERE id=ANY(v_ids) AND active) <> cardinality(v_ids) THEN
        RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
      END IF;
    END IF;
    DELETE FROM public.operational_ticket_assignees WHERE ticket_id=p_ticket_id;
    INSERT INTO public.operational_ticket_assignees(ticket_id,officer_id,assigned_by_profile_id)
    SELECT p_ticket_id, unnest(v_ids), v_actor.id;
    UPDATE public.operational_records SET
      owner_officer_id=v_ids[1], owner_text=NULL,
      review_version=CASE WHEN p_action='assign' THEN review_version+1 ELSE review_version END,
      updated_at=now()
    WHERE id=p_ticket_id;
  END IF;

  SELECT * INTO v_ticket FROM public.operational_records WHERE id=p_ticket_id;
  v_after := to_jsonb(v_ticket);
  INSERT INTO public.operational_review_events(
    operational_record_id,action,previous_review_status,new_review_status,reviewer_profile_id,changes
  ) VALUES (
    p_ticket_id,'committee_'||p_action,v_before->>'review_status',v_after->>'review_status',v_actor.id,
    CASE WHEN p_action='create' THEN jsonb_build_object('title',v_after->>'title','assignee_ids',to_jsonb(v_ids))
         WHEN p_action='assign' THEN jsonb_build_object('assignee_ids',to_jsonb(v_ids))
         ELSE p_patch END
  );
  RETURN jsonb_build_object('record',v_after,'review_version',v_ticket.review_version);
END;
$function$;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.mutate_committee_ticket(text,uuid,integer,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_committee_ticket(text,uuid,integer,jsonb) TO authenticated")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.mutate_committee_ticket(text,uuid,integer,jsonb) FROM authenticated")
    op.execute("DROP FUNCTION public.mutate_committee_ticket(text,uuid,integer,jsonb)")
    op.execute("DROP POLICY operational_review_events_select_committee ON public.operational_review_events")
    op.execute("DROP POLICY operational_ticket_assignees_select ON public.operational_ticket_assignees")
    op.execute("DROP POLICY operational_records_select_committee ON public.operational_records")
    op.execute("""
      CREATE POLICY operational_records_select_committee ON public.operational_records
      FOR SELECT TO authenticated USING (review_status='approved' AND is_current AND public.has_efds_role('committee'))
    """)
    op.drop_index("uq_operational_records_slack_ticket_source", table_name="operational_records")
    op.drop_index("ix_operational_ticket_assignees_actor", table_name="operational_ticket_assignees")
    op.drop_index("ix_operational_ticket_assignees_officer", table_name="operational_ticket_assignees")
    op.drop_table("operational_ticket_assignees")
