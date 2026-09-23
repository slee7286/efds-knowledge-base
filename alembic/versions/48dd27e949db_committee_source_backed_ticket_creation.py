"""Let committee reviewers create a ticket from permitted, cited evidence."""

from collections.abc import Sequence

from alembic import op


revision: str = "48dd27e949db"
down_revision: str | None = "d8144f029e73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE FUNCTION public.committee_ticket_slack_evidence_v1(result_limit integer DEFAULT 10)
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
      AND caller.access_role IN ('committee','admin')
  ) THEN
    RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized';
  END IF;
  RETURN QUERY
  SELECT r.id, 'slack_message'::text, r.source_record_id,
    r.source_parent_id, r.source_version_id, r.title,
    left(r.content, 1400)::text,
    (1.0 - greatest(0.0, extract(epoch FROM (now()-m.source_posted_at)) / 2592000.0))::real,
    NULL::text, NULL::text, c.name,
    coalesce(nullif(u.real_name,''), nullif(u.display_name,''), m.user_slack_id),
    m.source_posted_at, m.source_edited_at,
    'source_generated'::text, 'committee'::text,
    r.is_current, r.is_stale, m.permalink, m.permalink,
    NULL::text, r.content_hash,
    jsonb_build_object('channel_id',c.id,'slack_ts',m.slack_ts),
    'committee_slack'::text
  FROM public.retrieval_units r
  JOIN public.slack_messages m ON m.id::text=r.source_record_id
  JOIN public.slack_channels c ON c.id=m.channel_id
  JOIN public.slack_channel_sync_settings s ON s.channel_id=c.id
  LEFT JOIN public.slack_users u ON u.id=m.author_user_id
  WHERE r.source_type='slack_message' AND r.is_current AND NOT r.is_stale
    AND NOT r.is_deleted AND NOT m.is_deleted
    AND r.source_version_id=m.content_hash AND r.chunk_index=0
    AND NOT c.is_private AND s.enabled
    AND m.source_posted_at >= now()-interval '30 days'
    AND m.message_text NOT LIKE '[EFDS archive repost:%'
  ORDER BY m.source_posted_at DESC, r.chunk_index ASC
  LIMIT least(greatest(coalesce(result_limit,10),1),12);
END;
$function$;
REVOKE ALL ON FUNCTION public.committee_ticket_slack_evidence_v1(integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.committee_ticket_slack_evidence_v1(integer)
  TO authenticated;
    """)
    op.execute("""
CREATE FUNCTION public.create_committee_ticket_from_evidence(p_patch jsonb, p_unit_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $function$
DECLARE
  v_unit public.retrieval_units%ROWTYPE;
  v_result jsonb;
  v_ticket_id uuid;
  v_title text;
BEGIN
  IF (SELECT auth.uid()) IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.profiles caller
    WHERE caller.auth_user_id = (SELECT auth.uid()) AND caller.active
      AND caller.access_role IN ('committee','admin')
  ) THEN
    RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized';
  END IF;
  IF p_patch IS NULL OR jsonb_typeof(p_patch) <> 'object' THEN
    RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
  END IF;
  v_title := nullif(btrim(p_patch->>'title'), '');
  IF v_title IS NULL OR length(v_title) > 300 THEN
    RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='validation_error';
  END IF;

  -- SECURITY DEFINER bypasses RLS. Mirror the committee retrieval boundary
  -- explicitly. Raw Slack is permitted only from enabled public channels;
  -- private meeting and document units are never permitted.
  SELECT candidate.* INTO v_unit FROM public.retrieval_units AS candidate
  WHERE candidate.id=p_unit_id AND candidate.source_type IN (
    'slack_message',
    'icu_article','knowledge_requirement','knowledge_timing_rule',
    'knowledge_process','knowledge_process_step','knowledge_resource','knowledge_contact',
    'operational_decision','operational_action','operational_commitment',
    'operational_question','operational_status'
  ) AND candidate.is_current AND NOT candidate.is_stale AND NOT candidate.is_deleted
    AND (
      (candidate.source_type <> 'slack_message'
       AND candidate.visibility IN ('committee','member','public') AND candidate.review_status='approved')
      OR (candidate.source_type='slack_message' AND EXISTS (
        SELECT 1 FROM public.slack_messages m
        JOIN public.slack_channels c ON c.id=m.channel_id
        JOIN public.slack_channel_sync_settings s ON s.channel_id=c.id
        WHERE m.id::text=candidate.source_record_id AND NOT m.is_deleted
          AND candidate.source_version_id=m.content_hash
          AND NOT c.is_private AND s.enabled
          AND m.source_posted_at >= now()-interval '30 days'
          AND m.message_text NOT LIKE '[EFDS archive repost:%'
      ))
    );
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='evidence_not_permitted';
  END IF;

  -- Serialize exact-title creations so concurrent suggestions cannot create
  -- duplicate open committee tickets. The underlying mutation rechecks role,
  -- allowed fields, officer IDs and title length and writes the actor audit.
  PERFORM pg_advisory_xact_lock(hashtextextended(lower(v_title), 0));
  IF EXISTS (
    SELECT 1 FROM public.operational_records existing
    WHERE existing.record_type='action_item' AND existing.review_status='approved'
      AND existing.visibility='committee' AND existing.is_current
      AND existing.execution_status NOT IN ('completed','cancelled')
      AND lower(btrim(existing.title))=lower(v_title)
  ) THEN
    RAISE EXCEPTION USING ERRCODE='P0007', MESSAGE='duplicate_ticket';
  END IF;

  v_result := public.mutate_committee_ticket('create', NULL, NULL, p_patch);
  v_ticket_id := (v_result->'record'->>'id')::uuid;
  INSERT INTO public.operational_record_evidence(
    operational_record_id, retrieval_unit_id, source_type, source_record_id,
    source_version_id, evidence_text, evidence_role, metadata
  ) VALUES (
    v_ticket_id, v_unit.id, v_unit.source_type, v_unit.source_record_id,
    v_unit.source_version_id, v_unit.title, 'primary',
    jsonb_build_object('title',v_unit.title,'authority',v_unit.authority)
  );
  UPDATE public.operational_records
  SET metadata=metadata || jsonb_build_object('origin','committee_agent_suggestion')
  WHERE id=v_ticket_id;
  v_result := jsonb_set(v_result, '{record,metadata}',
    (SELECT metadata FROM public.operational_records WHERE id=v_ticket_id));
  RETURN v_result;
END;
$function$;
REVOKE ALL ON FUNCTION public.create_committee_ticket_from_evidence(jsonb,uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_committee_ticket_from_evidence(jsonb,uuid)
  TO authenticated;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION public.create_committee_ticket_from_evidence(jsonb,uuid)")
    op.execute("DROP FUNCTION public.committee_ticket_slack_evidence_v1(integer)")
