"""Add reviewed operational records, evidence, audit, RLS, and mutations."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0012_operational_truth"
down_revision: str | None = "0011_meetily_meeting_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
jsonb = postgresql.JSONB()


def upgrade() -> None:
    op.create_table(
        "operational_records",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("record_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("priority", sa.Text()),
        sa.Column("owner_profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("owner_officer_id", uuid, sa.ForeignKey("officers.id", ondelete="SET NULL")),
        sa.Column("owner_text", sa.Text()),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("due_text", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("workstream", sa.Text()),
        sa.Column("execution_status", sa.Text()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("visibility", sa.Text(), nullable=False, server_default=sa.text("'internal'")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("reviewed_by_profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by_id", uuid, sa.ForeignKey("operational_records.id", ondelete="SET NULL")),
        sa.Column("review_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("record_type IN ('decision', 'action_item', 'commitment', 'open_question', 'status_update')", name="ck_operational_records_type"),
        sa.CheckConstraint("review_status IN ('proposed', 'approved', 'rejected', 'needs_review', 'superseded')", name="ck_operational_records_review_status"),
        sa.CheckConstraint("execution_status IS NULL OR execution_status IN ('open', 'in_progress', 'blocked', 'completed', 'cancelled', 'answered', 'resolved', 'closed')", name="ck_operational_records_execution_status"),
        sa.CheckConstraint("visibility IN ('internal', 'committee', 'member', 'public')", name="ck_operational_records_visibility"),
    )
    for name, columns in {
        "ix_operational_records_type_review": ["record_type", "review_status", "is_current"],
        "ix_operational_records_execution": ["execution_status", "due_at"],
        "ix_operational_records_owner": ["owner_profile_id", "owner_officer_id"],
        "ix_operational_records_workstream": ["workstream"],
        "ix_operational_records_visibility": ["visibility", "review_status", "is_current"],
    }.items():
        op.create_index(name, "operational_records", columns)

    op.create_table(
        "operational_record_evidence",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operational_record_id", uuid, sa.ForeignKey("operational_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retrieval_unit_id", uuid, sa.ForeignKey("retrieval_units.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("source_version_id", sa.Text()),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("start_offset", sa.Integer()),
        sa.Column("end_offset", sa.Integer()),
        sa.Column("evidence_role", sa.Text(), nullable=False, server_default=sa.text("'supporting'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("operational_record_id", "retrieval_unit_id", "evidence_role", name="uq_operational_record_evidence_unit_role"),
        sa.CheckConstraint("evidence_role IN ('primary', 'supporting', 'contradicting', 'context')", name="ck_operational_record_evidence_role"),
    )
    op.create_index("ix_operational_record_evidence_record", "operational_record_evidence", ["operational_record_id"])
    op.create_index("ix_operational_record_evidence_unit", "operational_record_evidence", ["retrieval_unit_id"])

    op.create_table(
        "operational_review_events",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operational_record_id", uuid, sa.ForeignKey("operational_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("previous_review_status", sa.Text()),
        sa.Column("new_review_status", sa.Text()),
        sa.Column("reviewer_profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("changes", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_operational_review_events_record", "operational_review_events", ["operational_record_id", "created_at"])
    op.create_index("ix_operational_review_events_reviewer", "operational_review_events", ["reviewer_profile_id", "created_at"])

    # Preserve the two original operational concepts without making them the
    # extensibility boundary for the new five-type reviewed model.
    op.execute("""
      INSERT INTO public.operational_records(record_type, title, occurred_at, review_status, visibility, metadata)
      SELECT 'decision', decision_text, decided_at,
             CASE WHEN approved THEN 'approved' ELSE 'proposed' END,
             'internal', jsonb_build_object('legacy_table', 'decisions', 'legacy_id', id)
      FROM public.decisions
    """)
    op.execute("""
      INSERT INTO public.operational_records(record_type, title, description, owner_officer_id, due_text, execution_status, review_status, visibility, metadata)
      SELECT 'action_item', title, description, owner_id,
             CASE WHEN due_date IS NULL THEN NULL ELSE due_date::text END,
             CASE status WHEN 'not_started' THEN 'open' WHEN 'done' THEN 'completed' ELSE status END,
             'proposed', 'internal', jsonb_build_object('legacy_table', 'action_items', 'legacy_id', id, 'legacy_priority', priority)
      FROM public.action_items
    """)

    for table in ("operational_records", "operational_record_evidence", "operational_review_events"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute("""
      CREATE POLICY operational_records_select_admin ON public.operational_records
      FOR SELECT TO authenticated USING (public.has_efds_role('admin'));
      CREATE POLICY operational_records_select_committee ON public.operational_records
      FOR SELECT TO authenticated USING (review_status = 'approved' AND is_current AND public.has_efds_role('committee'));
      CREATE POLICY operational_records_select_member ON public.operational_records
      FOR SELECT TO authenticated USING (review_status = 'approved' AND is_current AND visibility IN ('member', 'public') AND public.has_efds_role('member'));
      CREATE POLICY operational_record_evidence_select_visible ON public.operational_record_evidence
      FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM public.operational_records r WHERE r.id = operational_record_id));
      CREATE POLICY operational_review_events_select_admin ON public.operational_review_events
      FOR SELECT TO authenticated USING (public.has_efds_role('admin'));
    """)
    op.execute("GRANT SELECT ON public.operational_records, public.operational_record_evidence, public.operational_review_events TO authenticated")
    op.execute("REVOKE ALL ON public.operational_records, public.operational_record_evidence, public.operational_review_events FROM anon")

    op.execute(r"""
CREATE OR REPLACE FUNCTION public.mutate_operational_record(
  p_action text,
  p_record_id uuid DEFAULT NULL,
  p_expected_version integer DEFAULT NULL,
  p_patch jsonb DEFAULT '{}'::jsonb,
  p_reason text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
DECLARE
  v_profile_id uuid;
  v_record public.operational_records%ROWTYPE;
  v_before jsonb;
  v_after jsonb;
  v_event_id uuid;
  v_new_status text;
  v_new_execution text;
  v_visibility text;
  v_evidence public.retrieval_units%ROWTYPE;
  v_evidence_id uuid;
  v_key text;
  v_changes jsonb := '{}'::jsonb;
BEGIN
  SELECT id INTO v_profile_id FROM public.profiles
  WHERE auth_user_id = auth.uid() AND active AND access_role = 'admin' LIMIT 1;
  IF v_profile_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized'; END IF;

  IF p_action = 'create' THEN
    IF p_record_id IS NOT NULL OR nullif(trim(p_patch->>'record_type'), '') IS NULL OR
       p_patch->>'record_type' NOT IN ('decision','action_item','commitment','open_question','status_update') OR
       nullif(trim(p_patch->>'title'), '') IS NULL THEN
      RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
    END IF;
    v_new_execution := CASE WHEN p_patch->>'record_type' = 'decision' THEN NULL ELSE coalesce(nullif(p_patch->>'execution_status',''), 'open') END;
    INSERT INTO public.operational_records(record_type, title, description, priority, owner_profile_id, owner_officer_id, owner_text, due_at, due_text, occurred_at, workstream, execution_status, review_status, visibility, created_by_profile_id, metadata)
    VALUES (p_patch->>'record_type', trim(p_patch->>'title'), nullif(trim(p_patch->>'description'),''), nullif(trim(p_patch->>'priority'),''), nullif(p_patch->>'owner_profile_id','')::uuid, nullif(p_patch->>'owner_officer_id','')::uuid, nullif(trim(p_patch->>'owner_text'),''), nullif(p_patch->>'due_at','')::timestamptz, nullif(trim(p_patch->>'due_text'),''), nullif(p_patch->>'occurred_at','')::timestamptz, nullif(trim(p_patch->>'workstream'),''), v_new_execution, 'proposed', 'internal', v_profile_id, coalesce(p_patch->'metadata','{}'::jsonb))
    RETURNING * INTO v_record;
    IF nullif(p_patch->>'retrieval_unit_id','') IS NOT NULL THEN
      SELECT * INTO v_evidence FROM public.retrieval_units WHERE id=(p_patch->>'retrieval_unit_id')::uuid;
      IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0003', MESSAGE='evidence_not_found'; END IF;
      INSERT INTO public.operational_record_evidence(operational_record_id, retrieval_unit_id, source_type, source_record_id, source_version_id, evidence_text, start_offset, end_offset, evidence_role, metadata)
      VALUES (v_record.id, v_evidence.id, v_evidence.source_type, v_evidence.source_record_id, v_evidence.source_version_id, nullif(p_patch->>'evidence_text',''), nullif(p_patch->>'start_offset','')::integer, nullif(p_patch->>'end_offset','')::integer, coalesce(nullif(p_patch->>'evidence_role',''),'supporting'), jsonb_build_object('title',v_evidence.title));
    END IF;
    v_before := NULL;
    v_after := to_jsonb(v_record);
  ELSE
    IF p_record_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0003', MESSAGE='record_not_found'; END IF;
    SELECT * INTO v_record FROM public.operational_records WHERE id = p_record_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0003', MESSAGE='record_not_found'; END IF;
    IF p_expected_version IS NULL OR p_expected_version <> v_record.review_version THEN RAISE EXCEPTION USING ERRCODE='P0006', MESSAGE='concurrency_conflict'; END IF;
    v_before := to_jsonb(v_record);

    IF p_action IN ('edit', 'edit_approve') THEN
      IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_patch) AS k WHERE k NOT IN ('title','description','priority','owner_profile_id','owner_officer_id','owner_text','due_at','due_text','occurred_at','workstream','execution_status','metadata')) THEN
        RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
      END IF;
      IF p_patch ? 'title' AND nullif(trim(p_patch->>'title'),'') IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error'; END IF;
      UPDATE public.operational_records SET
        title = CASE WHEN p_patch ? 'title' THEN trim(p_patch->>'title') ELSE title END,
        description = CASE WHEN p_patch ? 'description' THEN nullif(trim(p_patch->>'description'),'') ELSE description END,
        priority = CASE WHEN p_patch ? 'priority' THEN nullif(trim(p_patch->>'priority'),'') ELSE priority END,
        owner_profile_id = CASE WHEN p_patch ? 'owner_profile_id' THEN nullif(p_patch->>'owner_profile_id','')::uuid ELSE owner_profile_id END,
        owner_officer_id = CASE WHEN p_patch ? 'owner_officer_id' THEN nullif(p_patch->>'owner_officer_id','')::uuid ELSE owner_officer_id END,
        owner_text = CASE WHEN p_patch ? 'owner_text' THEN nullif(trim(p_patch->>'owner_text'),'') ELSE owner_text END,
        due_at = CASE WHEN p_patch ? 'due_at' THEN nullif(p_patch->>'due_at','')::timestamptz ELSE due_at END,
        due_text = CASE WHEN p_patch ? 'due_text' THEN nullif(trim(p_patch->>'due_text'),'') ELSE due_text END,
        occurred_at = CASE WHEN p_patch ? 'occurred_at' THEN nullif(p_patch->>'occurred_at','')::timestamptz ELSE occurred_at END,
        workstream = CASE WHEN p_patch ? 'workstream' THEN nullif(trim(p_patch->>'workstream'),'') ELSE workstream END,
        execution_status = CASE WHEN p_patch ? 'execution_status' THEN nullif(p_patch->>'execution_status','') ELSE execution_status END,
        metadata = CASE WHEN p_patch ? 'metadata' THEN p_patch->'metadata' ELSE metadata END,
        review_status = CASE WHEN p_action = 'edit_approve' THEN 'approved' ELSE review_status END,
        reviewed_by_profile_id = CASE WHEN p_action = 'edit_approve' THEN v_profile_id ELSE reviewed_by_profile_id END,
        reviewed_at = CASE WHEN p_action = 'edit_approve' THEN now() ELSE reviewed_at END,
        review_version = review_version + 1, updated_at = now()
      WHERE id = p_record_id;
    ELSIF p_action IN ('approve','reject','defer','publish','unpublish','assign_owner','change_due_date','change_execution_status','complete','reopen','resolve','supersede','attach_evidence','detach_evidence') THEN
      IF p_action = 'approve' THEN
        IF v_record.review_status NOT IN ('proposed','needs_review') OR NOT v_record.is_current THEN RAISE EXCEPTION USING ERRCODE='P0004', MESSAGE='invalid_transition'; END IF;
        UPDATE public.operational_records SET review_status='approved', reviewed_by_profile_id=v_profile_id, reviewed_at=now(), review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'reject' THEN
        IF nullif(trim(p_reason),'') IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error'; END IF;
        UPDATE public.operational_records SET review_status='rejected', reviewed_by_profile_id=v_profile_id, reviewed_at=now(), review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'defer' THEN
        UPDATE public.operational_records SET review_status='needs_review', reviewed_by_profile_id=v_profile_id, reviewed_at=now(), review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action IN ('publish','unpublish') THEN
        IF v_record.review_status <> 'approved' OR NOT v_record.is_current THEN RAISE EXCEPTION USING ERRCODE='P0005', MESSAGE='only approved current records can be published'; END IF;
        v_visibility := CASE WHEN p_action='unpublish' THEN 'internal' ELSE p_patch->>'visibility' END;
        IF v_visibility IS NULL OR v_visibility NOT IN ('internal','committee','member','public') THEN RAISE EXCEPTION USING ERRCODE='P0007', MESSAGE='invalid_visibility'; END IF;
        UPDATE public.operational_records SET visibility=v_visibility, review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'assign_owner' THEN
        UPDATE public.operational_records SET owner_profile_id=nullif(p_patch->>'owner_profile_id','')::uuid, owner_officer_id=nullif(p_patch->>'owner_officer_id','')::uuid, owner_text=nullif(trim(p_patch->>'owner_text'),'') , review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'change_due_date' THEN
        UPDATE public.operational_records SET due_at=nullif(p_patch->>'due_at','')::timestamptz, due_text=nullif(trim(p_patch->>'due_text'),'') , review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action IN ('change_execution_status','complete','reopen','resolve') THEN
        v_new_execution := CASE p_action WHEN 'complete' THEN 'completed' WHEN 'reopen' THEN 'open' WHEN 'resolve' THEN 'resolved' ELSE p_patch->>'execution_status' END;
        IF v_new_execution IS NULL OR v_new_execution NOT IN ('open','in_progress','blocked','completed','cancelled','answered','resolved','closed') THEN RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error'; END IF;
        UPDATE public.operational_records SET execution_status=v_new_execution, completed_at=CASE WHEN v_new_execution='completed' THEN now() ELSE completed_at END, resolved_at=CASE WHEN v_new_execution IN ('resolved','closed') THEN now() ELSE resolved_at END, review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'supersede' THEN
        IF v_record.record_type <> 'decision' OR nullif(p_patch->>'superseded_by_id','') IS NULL OR (p_patch->>'superseded_by_id')::uuid = p_record_id OR NOT EXISTS (SELECT 1 FROM public.operational_records WHERE id=(p_patch->>'superseded_by_id')::uuid AND record_type='decision' AND is_current) THEN RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error'; END IF;
        UPDATE public.operational_records SET review_status='superseded', is_current=false, superseded_by_id=(p_patch->>'superseded_by_id')::uuid, reviewed_by_profile_id=v_profile_id, reviewed_at=now(), review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'attach_evidence' THEN
        SELECT * INTO v_evidence FROM public.retrieval_units WHERE id=(p_patch->>'retrieval_unit_id')::uuid;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0003', MESSAGE='evidence_not_found'; END IF;
        INSERT INTO public.operational_record_evidence(operational_record_id, retrieval_unit_id, source_type, source_record_id, source_version_id, evidence_text, start_offset, end_offset, evidence_role, metadata)
        VALUES (p_record_id, v_evidence.id, v_evidence.source_type, v_evidence.source_record_id, v_evidence.source_version_id, nullif(p_patch->>'evidence_text',''), nullif(p_patch->>'start_offset','')::integer, nullif(p_patch->>'end_offset','')::integer, coalesce(nullif(p_patch->>'evidence_role',''),'supporting'), jsonb_build_object('title',v_evidence.title));
        UPDATE public.operational_records SET review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      ELSIF p_action = 'detach_evidence' THEN
        DELETE FROM public.operational_record_evidence WHERE id=(p_patch->>'evidence_id')::uuid AND operational_record_id=p_record_id;
        UPDATE public.operational_records SET review_version=review_version+1, updated_at=now() WHERE id=p_record_id;
      END IF;
    ELSE
      RAISE EXCEPTION USING ERRCODE='P0004', MESSAGE='invalid_transition';
    END IF;
    SELECT * INTO v_record FROM public.operational_records WHERE id=p_record_id;
    v_after := to_jsonb(v_record);
  END IF;

  IF v_before IS NOT NULL THEN
    FOR v_key IN SELECT jsonb_object_keys(coalesce(p_patch,'{}'::jsonb)) LOOP
      IF (v_before -> v_key) IS DISTINCT FROM (v_after -> v_key) THEN v_changes := v_changes || jsonb_build_object(v_key, jsonb_build_object('before', v_before->v_key, 'after', v_after->v_key)); END IF;
    END LOOP;
  ELSE
    v_changes := jsonb_build_object('record_type', v_after->>'record_type', 'title', v_after->>'title');
  END IF;
  IF p_action IN ('approve','reject','defer','edit_approve') OR v_before IS NULL THEN
    v_changes := v_changes || jsonb_build_object('review_status', jsonb_build_object('before', v_before->>'review_status', 'after', v_after->>'review_status'));
  END IF;
  INSERT INTO public.operational_review_events(operational_record_id, action, previous_review_status, new_review_status, reviewer_profile_id, reason, changes)
  VALUES (v_record.id, CASE WHEN p_action='defer' THEN 'needs_review' ELSE p_action END, v_before->>'review_status', v_after->>'review_status', v_profile_id, nullif(trim(p_reason),''), v_changes)
  RETURNING id INTO v_event_id;
  RETURN jsonb_build_object('record', v_after, 'event_id', v_event_id, 'review_version', v_after->>'review_version');
END;
$function$;
""")
    op.execute("REVOKE ALL ON FUNCTION public.mutate_operational_record(text, uuid, integer, jsonb, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_operational_record(text, uuid, integer, jsonb, text) TO authenticated")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.mutate_operational_record(text, uuid, integer, jsonb, text) FROM authenticated")
    op.execute("DROP FUNCTION IF EXISTS public.mutate_operational_record(text, uuid, integer, jsonb, text)")
    for policy in ("operational_review_events_select_admin", "operational_record_evidence_select_visible", "operational_records_select_member", "operational_records_select_committee", "operational_records_select_admin"):
        table = "operational_review_events" if policy.startswith("operational_review") else "operational_record_evidence" if policy.startswith("operational_record_evidence") else "operational_records"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON public.{table}")
    for table in ("operational_review_events", "operational_record_evidence", "operational_records"):
        op.execute(f"REVOKE ALL ON public.{table} FROM authenticated")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_operational_review_events_reviewer", table_name="operational_review_events")
    op.drop_index("ix_operational_review_events_record", table_name="operational_review_events")
    op.drop_table("operational_review_events")
    op.drop_index("ix_operational_record_evidence_unit", table_name="operational_record_evidence")
    op.drop_index("ix_operational_record_evidence_record", table_name="operational_record_evidence")
    op.drop_table("operational_record_evidence")
    for name in ("ix_operational_records_visibility", "ix_operational_records_workstream", "ix_operational_records_owner", "ix_operational_records_execution", "ix_operational_records_type_review"):
        op.drop_index(name, table_name="operational_records")
    op.drop_table("operational_records")
