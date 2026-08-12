"""Add transactional review mutations and optimistic concurrency.

Revision ID: 0006_transactional_knowledge_review
Revises: 0005_knowledge_review_publication

The RPC is intentionally the only canonical write path for review and
publication mutations from the website.  It resolves the actor from the
Supabase JWT, locks the derived row, validates its version and writes the
derived row plus exactly one audit event in the same PostgreSQL transaction.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_transactional_knowledge_review"
down_revision: str | None = "0005_knowledge_review_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DERIVED_TABLES = (
    "knowledge_requirements",
    "knowledge_timing_rules",
    "knowledge_processes",
    "knowledge_process_steps",
    "knowledge_resources",
    "knowledge_contacts",
)


def upgrade() -> None:
    for table in DERIVED_TABLES:
        op.add_column(
            table,
            sa.Column("review_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        )
        op.create_check_constraint(
            f"ck_{table}_review_version_positive",
            table,
            "review_version > 0",
        )

    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.review_knowledge_transaction(
  p_knowledge_type text,
  p_knowledge_record_id uuid,
  p_action text,
  p_expected_version integer,
  p_patch jsonb DEFAULT '{}'::jsonb,
  p_reason_code text DEFAULT NULL,
  p_reason_note text DEFAULT NULL,
  p_visibility text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
DECLARE
  v_table text;
  v_profile_id uuid;
  v_officer_id uuid;
  v_active boolean;
  v_access_role text;
  v_before jsonb;
  v_after jsonb;
  v_status text;
  v_next_status text;
  v_visibility text;
  v_stale boolean;
  v_version integer;
  v_process_id uuid;
  v_step_number integer;
  v_target_step_number integer;
  v_event_id uuid;
  v_changes jsonb := '{}'::jsonb;
  v_key text;
  v_old jsonb;
  v_new jsonb;
  v_roles_before jsonb;
  v_roles_after jsonb;
  v_original jsonb := '{}'::jsonb;
  v_resource_id uuid;
  v_new_step_id uuid;
  v_audit_type text;
  v_audit_record_id uuid;
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized';
  END IF;

  SELECT p.id, p.officer_id, p.active, p.access_role
    INTO v_profile_id, v_officer_id, v_active, v_access_role
  FROM public.profiles AS p
  WHERE p.auth_user_id = auth.uid()
  LIMIT 1;

  IF v_profile_id IS NULL OR NOT v_active THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'inactive_profile';
  END IF;
  IF v_access_role <> 'admin' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized';
  END IF;
  IF p_expected_version IS NULL OR p_expected_version < 1 THEN
    RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
  END IF;
  IF p_patch IS NULL OR jsonb_typeof(p_patch) <> 'object' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
  END IF;
  FOR v_key IN SELECT jsonb_object_keys(p_patch) LOOP
    IF v_key IN (
      'source_article_id', 'source_url', 'source_content_hash', 'source_updated_at',
      'evidence_text', 'evidence_start', 'evidence_end', 'extraction_run_id',
      'extracted_at', 'extraction_method', 'confidence', 'fingerprint', 'metadata',
      'review_status', 'reviewed_by_officer_id', 'reviewed_by_profile_id',
      'reviewed_at', 'is_stale', 'published_at', 'published_by_profile_id',
      'visibility', 'review_version'
    ) THEN
      RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
    END IF;
  END LOOP;

  v_table := CASE p_knowledge_type
    WHEN 'requirement' THEN 'knowledge_requirements'
    WHEN 'timing_rule' THEN 'knowledge_timing_rules'
    WHEN 'process' THEN 'knowledge_processes'
    WHEN 'process_step' THEN 'knowledge_process_steps'
    WHEN 'resource' THEN 'knowledge_resources'
    WHEN 'contact' THEN 'knowledge_contacts'
    ELSE NULL
  END;
  IF v_table IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
  END IF;

  EXECUTE format(
    'SELECT to_jsonb(t), t.review_status, t.visibility, t.is_stale, t.review_version
       FROM public.%I AS t WHERE t.id = $1 FOR UPDATE', v_table
  ) INTO v_before, v_status, v_visibility, v_stale, v_version
  USING p_knowledge_record_id;
  IF v_before IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = 'P0003', MESSAGE = 'record_not_found';
  END IF;
  IF v_version <> p_expected_version THEN
    RAISE EXCEPTION USING ERRCODE = 'P0006', MESSAGE = 'concurrency_conflict';
  END IF;
  v_audit_type := p_knowledge_type;
  v_audit_record_id := p_knowledge_record_id;

  IF p_action IN ('approve', 'edit_approve', 'reject', 'needs_review', 'defer', 'supersede') THEN
    IF p_action = 'reject' AND coalesce(nullif(trim(p_reason_code), ''), nullif(trim(p_reason_note), '')) IS NULL THEN
      RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
    END IF;
    IF p_action IN ('approve', 'edit_approve') THEN
      v_next_status := 'approved';
    ELSIF p_action = 'reject' THEN
      v_next_status := 'rejected';
    ELSIF p_action IN ('needs_review', 'defer') THEN
      v_next_status := 'needs_review';
    ELSE
      v_next_status := 'superseded';
    END IF;

    EXECUTE format(
      'UPDATE public.%I
          SET review_status = $1,
              reviewed_by_profile_id = $2,
              reviewed_by_officer_id = $3,
              reviewed_at = now(),
              is_stale = CASE WHEN $4 THEN false ELSE is_stale END,
              review_version = review_version + 1
        WHERE id = $5', v_table
    ) USING v_next_status, v_profile_id, v_officer_id, (p_action IN ('approve', 'edit_approve')), p_knowledge_record_id;

    IF p_action = 'edit_approve' THEN
      IF jsonb_object_length(p_patch) = 0 THEN
        RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
      END IF;
      FOR v_key IN SELECT jsonb_object_keys(p_patch) LOOP
        IF v_key <> 'role_ids' AND v_key <> 'resource_id' AND v_before ? v_key THEN
          v_original := v_original || jsonb_build_object(v_key, v_before -> v_key);
        END IF;
      END LOOP;
      IF jsonb_object_length(v_original) > 0 AND NOT (coalesce(v_before->'metadata', '{}'::jsonb) ? 'original_extraction') THEN
        EXECUTE format('UPDATE public.%I SET metadata = coalesce(metadata, ''{}''::jsonb) || jsonb_build_object(''original_extraction'', jsonb_build_object(''captured_at'', now(), ''values'', $1)) WHERE id=$2', v_table)
          USING v_original, p_knowledge_record_id;
      END IF;
      IF p_knowledge_type = 'requirement' THEN
        IF p_patch ? 'requirement_text' AND nullif(trim(p_patch->>'requirement_text'), '') IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
        END IF;
        EXECUTE 'UPDATE public.knowledge_requirements SET
          requirement_text = CASE WHEN $1 ? ''requirement_text'' THEN trim($1->>''requirement_text'') ELSE requirement_text END,
          requirement_type = CASE WHEN $1 ? ''requirement_type'' THEN trim($1->>''requirement_type'') ELSE requirement_type END,
          applies_to = CASE WHEN $1 ? ''applies_to'' THEN nullif(trim($1->>''applies_to''), '''') ELSE applies_to END,
          mandatory = CASE WHEN $1 ? ''mandatory'' THEN ($1->>''mandatory'')::boolean ELSE mandatory END,
          topic_id = CASE WHEN $1 ? ''topic_id'' THEN nullif($1->>''topic_id'', '''')::uuid ELSE topic_id END
          WHERE id = $2' USING p_patch, p_knowledge_record_id;
        IF p_patch ? 'role_ids' THEN
          SELECT coalesce(jsonb_agg(role_id ORDER BY role_id), '[]'::jsonb) INTO v_roles_before
          FROM public.knowledge_requirement_roles WHERE requirement_id = p_knowledge_record_id;
          DELETE FROM public.knowledge_requirement_roles WHERE requirement_id = p_knowledge_record_id;
          INSERT INTO public.knowledge_requirement_roles(requirement_id, role_id)
          SELECT p_knowledge_record_id, value::uuid FROM jsonb_array_elements_text(p_patch->'role_ids')
          ON CONFLICT DO NOTHING;
          SELECT coalesce(jsonb_agg(role_id ORDER BY role_id), '[]'::jsonb) INTO v_roles_after
          FROM public.knowledge_requirement_roles WHERE requirement_id = p_knowledge_record_id;
          v_changes := v_changes || jsonb_build_object('roles', jsonb_build_object('before', v_roles_before, 'after', v_roles_after));
        END IF;
      ELSIF p_knowledge_type = 'timing_rule' THEN
        IF p_patch ? 'description' AND nullif(trim(p_patch->>'description'), '') IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
        END IF;
        EXECUTE 'UPDATE public.knowledge_timing_rules SET
          description = CASE WHEN $1 ? ''description'' THEN trim($1->>''description'') ELSE description END,
          deadline_type = CASE WHEN $1 ? ''deadline_type'' THEN trim($1->>''deadline_type'') ELSE deadline_type END,
          absolute_date = CASE WHEN $1 ? ''absolute_date'' THEN nullif($1->>''absolute_date'', '''')::date ELSE absolute_date END,
          notice_period_value = CASE WHEN $1 ? ''notice_period_value'' THEN nullif($1->>''notice_period_value'', '''')::integer ELSE notice_period_value END,
          notice_period_unit = CASE WHEN $1 ? ''notice_period_unit'' THEN nullif(trim($1->>''notice_period_unit''), '''') ELSE notice_period_unit END,
          working_days = CASE WHEN $1 ? ''working_days'' THEN ($1->>''working_days'')::boolean ELSE working_days END,
          recurrence_rule = CASE WHEN $1 ? ''recurrence_rule'' THEN nullif(trim($1->>''recurrence_rule''), '''') ELSE recurrence_rule END,
          relative_to_event_type = CASE WHEN $1 ? ''relative_to_event_type'' THEN nullif(trim($1->>''relative_to_event_type''), '''') ELSE relative_to_event_type END,
          topic_id = CASE WHEN $1 ? ''topic_id'' THEN nullif($1->>''topic_id'', '''')::uuid ELSE topic_id END
          WHERE id = $2' USING p_patch, p_knowledge_record_id;
      ELSIF p_knowledge_type = 'process' THEN
        IF p_patch ? 'name' AND nullif(trim(p_patch->>'name'), '') IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
        END IF;
        EXECUTE 'UPDATE public.knowledge_processes SET
          name = CASE WHEN $1 ? ''name'' THEN trim($1->>''name'') ELSE name END,
          description = CASE WHEN $1 ? ''description'' THEN nullif(trim($1->>''description''), '''') ELSE description END,
          topic_id = CASE WHEN $1 ? ''topic_id'' THEN nullif($1->>''topic_id'', '''')::uuid ELSE topic_id END
          WHERE id = $2' USING p_patch, p_knowledge_record_id;
        IF p_patch ? 'role_ids' THEN
          SELECT coalesce(jsonb_agg(role_id ORDER BY role_id), '[]'::jsonb) INTO v_roles_before
          FROM public.knowledge_process_roles WHERE process_id = p_knowledge_record_id;
          DELETE FROM public.knowledge_process_roles WHERE process_id = p_knowledge_record_id;
          INSERT INTO public.knowledge_process_roles(process_id, role_id)
          SELECT p_knowledge_record_id, value::uuid FROM jsonb_array_elements_text(p_patch->'role_ids')
          ON CONFLICT DO NOTHING;
          SELECT coalesce(jsonb_agg(role_id ORDER BY role_id), '[]'::jsonb) INTO v_roles_after
          FROM public.knowledge_process_roles WHERE process_id = p_knowledge_record_id;
          v_changes := v_changes || jsonb_build_object('roles', jsonb_build_object('before', v_roles_before, 'after', v_roles_after));
        END IF;
      ELSIF p_knowledge_type = 'process_step' THEN
        IF p_patch ? 'instruction' AND nullif(trim(p_patch->>'instruction'), '') IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
        END IF;
        SELECT process_id, step_number INTO v_process_id, v_step_number
        FROM public.knowledge_process_steps WHERE id = p_knowledge_record_id;
        IF p_patch ? 'step_number' THEN
          v_target_step_number := (p_patch->>'step_number')::integer;
          IF v_target_step_number < 1 THEN
            RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
          END IF;
          UPDATE public.knowledge_process_steps SET step_number = -step_number WHERE id = p_knowledge_record_id;
          UPDATE public.knowledge_process_steps SET step_number = v_step_number
          WHERE process_id = v_process_id AND step_number = v_target_step_number;
        END IF;
        EXECUTE 'UPDATE public.knowledge_process_steps SET
          title = CASE WHEN $1 ? ''title'' THEN nullif(trim($1->>''title''), '''') ELSE title END,
          instruction = CASE WHEN $1 ? ''instruction'' THEN trim($1->>''instruction'') ELSE instruction END,
          condition = CASE WHEN $1 ? ''condition'' THEN nullif(trim($1->>''condition''), '''') ELSE condition END,
          step_number = CASE WHEN $1 ? ''step_number'' THEN ($1->>''step_number'')::integer ELSE step_number END
          WHERE id = $2' USING p_patch, p_knowledge_record_id;
      ELSIF p_knowledge_type = 'resource' THEN
        EXECUTE 'UPDATE public.knowledge_resources SET
          name = CASE WHEN $1 ? ''name'' THEN trim($1->>''name'') ELSE name END,
          resource_type = CASE WHEN $1 ? ''resource_type'' THEN trim($1->>''resource_type'') ELSE resource_type END,
          url = CASE WHEN $1 ? ''url'' THEN nullif(trim($1->>''url''), '''') ELSE url END,
          description = CASE WHEN $1 ? ''description'' THEN nullif(trim($1->>''description''), '''') ELSE description END,
          topic_id = CASE WHEN $1 ? ''topic_id'' THEN nullif($1->>''topic_id'', '''')::uuid ELSE topic_id END
          WHERE id = $2' USING p_patch, p_knowledge_record_id;
      ELSIF p_knowledge_type = 'contact' THEN
        EXECUTE 'UPDATE public.knowledge_contacts SET
          name = CASE WHEN $1 ? ''name'' THEN nullif(trim($1->>''name''), '''') ELSE name END,
          organisation = CASE WHEN $1 ? ''organisation'' THEN nullif(trim($1->>''organisation''), '''') ELSE organisation END,
          email = CASE WHEN $1 ? ''email'' THEN nullif(trim($1->>''email''), '''') ELSE email END,
          url = CASE WHEN $1 ? ''url'' THEN nullif(trim($1->>''url''), '''') ELSE url END,
          description = CASE WHEN $1 ? ''description'' THEN nullif(trim($1->>''description''), '''') ELSE description END
          WHERE id = $2' USING p_patch, p_knowledge_record_id;
      END IF;
    END IF;

  ELSIF p_action IN ('publish', 'unpublish') THEN
    IF v_status <> 'approved' OR (p_action = 'publish' AND v_stale) THEN
      RAISE EXCEPTION USING ERRCODE = 'P0005', MESSAGE = 'stale_record';
    END IF;
    IF p_action = 'publish' AND p_visibility NOT IN ('internal', 'committee', 'member', 'public') THEN
      RAISE EXCEPTION USING ERRCODE = 'P0007', MESSAGE = 'invalid_visibility';
    END IF;
    v_visibility := CASE WHEN p_action = 'unpublish' THEN 'internal' ELSE p_visibility END;
    EXECUTE format('UPDATE public.%I SET visibility=$1, published_at=CASE WHEN $1=''internal'' THEN NULL ELSE now() END, published_by_profile_id=CASE WHEN $1=''internal'' THEN NULL ELSE $2 END, review_version=review_version+1 WHERE id=$3', v_table)
      USING v_visibility, v_profile_id, p_knowledge_record_id;
    v_changes := jsonb_build_object('visibility', jsonb_build_object('before', v_before->'visibility', 'after', to_jsonb(v_visibility)));
  ELSIF p_action = 'add_process_step' AND p_knowledge_type = 'process' THEN
    IF nullif(trim(p_patch->>'instruction'), '') IS NULL THEN
      RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
    END IF;
    v_new_step_id := gen_random_uuid();
    INSERT INTO public.knowledge_process_steps(
      id, process_id, step_number, title, instruction, condition,
      source_article_id, source_url, source_content_hash, source_updated_at,
      evidence_text, extraction_run_id, extracted_at, extraction_method,
      confidence, review_status, reviewed_by_officer_id, reviewed_by_profile_id,
      reviewed_at, is_stale, fingerprint, visibility, metadata
    ) VALUES (
      v_new_step_id, p_knowledge_record_id,
      coalesce((SELECT max(step_number) + 1 FROM public.knowledge_process_steps WHERE process_id = p_knowledge_record_id), 1),
      nullif(trim(p_patch->>'title'), ''), trim(p_patch->>'instruction'),
      nullif(trim(p_patch->>'condition'), ''),
      (v_before->>'source_article_id')::uuid, v_before->>'source_url', v_before->>'source_content_hash',
      nullif(v_before->>'source_updated_at', '')::timestamptz,
      coalesce(nullif(trim(v_before->>'evidence_text'), ''), 'Reviewer-added interpretation; see the source process evidence.'),
      NULL, now(), 'reviewer_edit', NULL, 'approved', v_officer_id, v_profile_id,
      now(), coalesce((v_before->>'is_stale')::boolean, false),
      md5(v_new_step_id::text || trim(p_patch->>'instruction')),
      coalesce(v_before->>'visibility', 'internal'),
      coalesce(v_before->'metadata', '{}'::jsonb) || jsonb_build_object('reviewer_added', true)
    );
    UPDATE public.knowledge_processes SET review_version = review_version + 1 WHERE id = p_knowledge_record_id;
    v_table := 'knowledge_process_steps';
    v_audit_type := 'process_step';
    v_audit_record_id := v_new_step_id;
    v_status := NULL;
    v_next_status := 'approved';
    v_changes := jsonb_build_object('reviewer_added', true, 'process_id', p_knowledge_record_id);
  ELSIF p_action = 'remove_process_step' AND p_knowledge_type = 'process_step' THEN
    EXECUTE 'UPDATE public.knowledge_process_steps SET review_status=''superseded'', reviewed_by_profile_id=$1, reviewed_at=now(), review_version=review_version+1 WHERE id=$2'
      USING v_profile_id, p_knowledge_record_id;
    v_next_status := 'superseded';
    v_changes := jsonb_build_object('removed_from_current_interpretation', jsonb_build_object('before', false, 'after', true));
  ELSIF p_action = 'link_resource' AND p_knowledge_type = 'process' THEN
    IF NOT (p_patch ? 'resource_id') THEN
      RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
    END IF;
    v_resource_id := (p_patch->>'resource_id')::uuid;
    INSERT INTO public.knowledge_process_resources(process_id, resource_id) VALUES (p_knowledge_record_id, v_resource_id) ON CONFLICT DO NOTHING;
    UPDATE public.knowledge_processes SET review_version=review_version+1 WHERE id=p_knowledge_record_id;
    v_changes := jsonb_build_object('resource_id', jsonb_build_object('before', NULL, 'after', to_jsonb(v_resource_id)));
  ELSIF p_action = 'unlink_resource' AND p_knowledge_type = 'process' THEN
    IF NOT (p_patch ? 'resource_id') THEN
      RAISE EXCEPTION USING ERRCODE = 'P0008', MESSAGE = 'validation_error';
    END IF;
    v_resource_id := (p_patch->>'resource_id')::uuid;
    DELETE FROM public.knowledge_process_resources WHERE process_id=p_knowledge_record_id AND resource_id=v_resource_id;
    UPDATE public.knowledge_processes SET review_version=review_version+1 WHERE id=p_knowledge_record_id;
    v_changes := jsonb_build_object('resource_id', jsonb_build_object('before', to_jsonb(v_resource_id), 'after', NULL));
  ELSE
    RAISE EXCEPTION USING ERRCODE = 'P0004', MESSAGE = 'invalid_transition';
  END IF;

  EXECUTE format('SELECT to_jsonb(t) FROM public.%I AS t WHERE t.id=$1', v_table)
    INTO v_after USING p_knowledge_record_id;
  IF v_after IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = 'P0003', MESSAGE = 'record_not_found';
  END IF;
  IF v_next_status IS NULL THEN v_next_status := v_after->>'review_status'; END IF;
  IF v_changes = '{}'::jsonb THEN
    v_changes := jsonb_build_object('status', jsonb_build_object('before', v_status, 'after', v_after->>'review_status'));
  ELSE
    v_changes := jsonb_build_object('status', jsonb_build_object('before', v_status, 'after', v_after->>'review_status')) || v_changes;
  END IF;
  IF p_action = 'approve' AND v_stale THEN
    v_changes := v_changes || jsonb_build_object('kept_source_version', true);
  END IF;
  IF p_action = 'edit_approve' THEN
    FOR v_key IN SELECT jsonb_object_keys(p_patch) LOOP
      IF v_key <> 'role_ids' THEN
        v_old := v_before -> v_key;
        v_new := v_after -> v_key;
        IF v_old IS DISTINCT FROM v_new THEN
          v_changes := v_changes || jsonb_build_object(v_key, jsonb_build_object('before', v_old, 'after', v_new));
        END IF;
      END IF;
    END LOOP;
  END IF;
  IF p_reason_code IS NOT NULL THEN
    v_changes := v_changes || jsonb_build_object('reason_code', p_reason_code);
  END IF;

  INSERT INTO public.knowledge_review_events(
    knowledge_type, knowledge_record_id, action, previous_status, new_status,
    reviewer_profile_id, reason, changes
  ) VALUES (
    v_audit_type, v_audit_record_id,
    CASE WHEN p_action = 'defer' THEN 'needs_review' WHEN p_action = 'publish' AND v_visibility = 'internal' THEN 'unpublish' ELSE p_action END,
    v_status, v_after->>'review_status', v_profile_id,
    nullif(coalesce(p_reason_note, p_reason_code), ''), v_changes
  ) RETURNING id INTO v_event_id;

  RETURN jsonb_build_object('record', v_after, 'event_id', v_event_id, 'review_version', (v_after->>'review_version')::integer);
END;
$function$;

REVOKE ALL ON FUNCTION public.review_knowledge_transaction(text, uuid, text, integer, jsonb, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.review_knowledge_transaction(text, uuid, text, integer, jsonb, text, text, text) TO authenticated;
"""
    )


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.review_knowledge_transaction(text, uuid, text, integer, jsonb, text, text, text) FROM authenticated"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.review_knowledge_transaction(text, uuid, text, integer, jsonb, text, text, text)"
    )
    for table in reversed(DERIVED_TABLES):
        op.drop_constraint(f"ck_{table}_review_version_positive", table, type_="check")
        op.drop_column(table, "review_version")
