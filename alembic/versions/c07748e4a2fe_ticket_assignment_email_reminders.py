"""ticket_assignment_email_reminders

Revision ID: c07748e4a2fe
Revises: 948e359c2344
Create Date: 2026-09-24 16:44:11.970779
"""
from collections.abc import Sequence

from alembic import op


revision: str = "c07748e4a2fe"
down_revision: str | None = "948e359c2344"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL = """
CREATE TABLE public.ticket_reminder_jobs (
  ticket_id uuid PRIMARY KEY REFERENCES public.operational_records(id) ON DELETE CASCADE,
  initial_version integer NOT NULL,
  initial_updated_at timestamptz NOT NULL,
  due_at timestamptz NOT NULL,
  state text NOT NULL DEFAULT 'pending'
    CHECK (state IN ('waiting_assignment','pending','queued','skipped','no_recipients')),
  processed_at timestamptz
);
CREATE INDEX ticket_reminder_jobs_due_idx ON public.ticket_reminder_jobs(due_at)
  WHERE state='pending';
ALTER TABLE public.ticket_reminder_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ticket_reminder_jobs FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.ticket_reminder_jobs TO service_role;

CREATE TABLE public.ticket_reminder_emails (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL,
  ticket_id uuid NOT NULL REFERENCES public.operational_records(id) ON DELETE CASCADE,
  profile_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  recipient_email text NOT NULL CHECK (length(recipient_email) BETWEEN 3 AND 254),
  ticket_title text NOT NULL CHECK (length(ticket_title) BETWEEN 1 AND 300),
  source text NOT NULL CHECK (source IN ('automatic','manual')),
  state text NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','sending','accepted','rejected','uncertain','cancelled')),
  provider text CHECK (provider IN ('resend','brevo')),
  provider_message_id text CHECK (length(provider_message_id) <= 256),
  attempts integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  attempted_at timestamptz,
  finished_at timestamptz,
  UNIQUE (request_id, profile_id)
);
CREATE INDEX ticket_reminder_emails_pending_idx
  ON public.ticket_reminder_emails(created_at,id) WHERE state='pending';
CREATE INDEX ticket_reminder_emails_manual_recent_idx
  ON public.ticket_reminder_emails(ticket_id,created_at DESC) WHERE source='manual';
CREATE INDEX ticket_reminder_emails_profile_idx
  ON public.ticket_reminder_emails(profile_id);
ALTER TABLE public.ticket_reminder_emails ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ticket_reminder_emails FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.ticket_reminder_emails TO service_role;
COMMENT ON TABLE public.ticket_reminder_emails IS
  'Private transactional reminder outbox. Accepted means provider accepted, not inbox delivery. Uncertain mail is never replayed automatically.';

CREATE FUNCTION public.schedule_new_ticket_reminder()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE v_ticket public.operational_records%ROWTYPE;
BEGIN
  IF NEW.action NOT IN ('committee_create','committee_assign') THEN RETURN NEW; END IF;
  SELECT * INTO v_ticket FROM public.operational_records WHERE id=NEW.operational_record_id;
  IF NOT FOUND OR v_ticket.record_type <> 'action_item'
     OR v_ticket.metadata->>'origin' <> 'committee_dashboard' THEN
    RETURN NEW;
  END IF;
  IF NEW.action='committee_create' THEN
    INSERT INTO public.ticket_reminder_jobs
      (ticket_id,initial_version,initial_updated_at,due_at,state)
    VALUES (v_ticket.id,v_ticket.review_version,v_ticket.updated_at,
            v_ticket.created_at + interval '2 minutes',
            CASE WHEN EXISTS (SELECT 1 FROM public.operational_ticket_assignees
                              WHERE ticket_id=v_ticket.id)
                 THEN 'pending' ELSE 'waiting_assignment' END)
    ON CONFLICT (ticket_id) DO NOTHING;
  ELSE
    UPDATE public.ticket_reminder_jobs
       SET initial_version=v_ticket.review_version,
           initial_updated_at=v_ticket.updated_at,
           due_at=NEW.created_at + interval '2 minutes',
           state='pending'
     WHERE ticket_id=v_ticket.id AND state='waiting_assignment'
       AND EXISTS (SELECT 1 FROM public.operational_ticket_assignees
                   WHERE ticket_id=v_ticket.id);
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.schedule_new_ticket_reminder() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER schedule_new_ticket_reminder
AFTER INSERT ON public.operational_review_events
FOR EACH ROW EXECUTE FUNCTION public.schedule_new_ticket_reminder();

CREATE FUNCTION public.request_ticket_reminder(
  p_ticket_id uuid, p_expected_version integer
) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
  v_actor public.profiles%ROWTYPE;
  v_ticket public.operational_records%ROWTYPE;
  v_request uuid := gen_random_uuid();
  v_count integer;
BEGIN
  SELECT * INTO v_actor FROM public.profiles
    WHERE auth_user_id=auth.uid() AND active AND access_role IN ('committee','admin') LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='unauthorized'; END IF;
  SELECT * INTO v_ticket FROM public.operational_records WHERE id=p_ticket_id FOR UPDATE;
  IF NOT FOUND OR v_ticket.record_type <> 'action_item'
     OR v_ticket.review_status <> 'approved' OR NOT v_ticket.is_current
     OR v_ticket.visibility <> 'committee' THEN
    RAISE EXCEPTION USING ERRCODE='P0003', MESSAGE='ticket_not_found';
  END IF;
  IF p_expected_version IS NULL OR p_expected_version <> v_ticket.review_version THEN
    RAISE EXCEPTION USING ERRCODE='P0006', MESSAGE='concurrency_conflict';
  END IF;
  IF EXISTS (SELECT 1 FROM public.ticket_reminder_emails
             WHERE ticket_id=p_ticket_id AND source='manual'
               AND created_at >= now()-interval '1 minute') THEN
    RAISE EXCEPTION USING ERRCODE='P0007', MESSAGE='reminder_recently_requested';
  END IF;
  INSERT INTO public.ticket_reminder_emails
    (request_id,ticket_id,profile_id,recipient_email,ticket_title,source)
  SELECT v_request,p_ticket_id,p.id,p.email,v_ticket.title,'manual'
  FROM public.operational_ticket_assignees a
  JOIN public.officers o ON o.id=a.officer_id AND o.active
  JOIN public.profiles p ON p.officer_id=a.officer_id
    AND p.active AND p.access_role IN ('committee','admin')
  WHERE a.ticket_id=p_ticket_id;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  IF v_count=0 THEN
    RAISE EXCEPTION USING ERRCODE='P0008', MESSAGE='no_email_recipients';
  END IF;
  UPDATE public.ticket_reminder_jobs SET state='skipped',processed_at=now()
    WHERE ticket_id=p_ticket_id AND state IN ('pending','waiting_assignment');
  UPDATE public.ticket_reminder_emails SET state='cancelled',finished_at=now()
    WHERE ticket_id=p_ticket_id AND source='automatic' AND state='pending';
  INSERT INTO public.operational_review_events
    (operational_record_id,action,previous_review_status,new_review_status,
     reviewer_profile_id,changes)
  VALUES (p_ticket_id,'committee_reminder',v_ticket.review_status,v_ticket.review_status,
          v_actor.id,jsonb_build_object('recipient_count',v_count));
  RETURN v_count;
END;
$$;
REVOKE ALL ON FUNCTION public.request_ticket_reminder(uuid,integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_ticket_reminder(uuid,integer) TO authenticated;
"""


WORKER_SQL = """
CREATE FUNCTION public.prepare_due_ticket_reminders(p_limit integer DEFAULT 20)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
  v_job public.ticket_reminder_jobs%ROWTYPE;
  v_ticket public.operational_records%ROWTYPE;
  v_request uuid;
  v_recipients integer;
  v_processed integer := 0;
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'invalid batch size';
  END IF;
  FOR v_job IN
    SELECT * FROM public.ticket_reminder_jobs
    WHERE state='pending' AND due_at <= now()
    ORDER BY due_at,ticket_id FOR UPDATE SKIP LOCKED LIMIT p_limit
  LOOP
    SELECT * INTO v_ticket FROM public.operational_records WHERE id=v_job.ticket_id;
    IF NOT FOUND OR v_ticket.record_type <> 'action_item'
       OR v_ticket.review_status <> 'approved' OR NOT v_ticket.is_current
       OR v_ticket.visibility <> 'committee'
       OR v_ticket.execution_status IN ('completed','cancelled')
       OR v_ticket.review_version <> v_job.initial_version
       OR v_ticket.updated_at IS DISTINCT FROM v_job.initial_updated_at THEN
      UPDATE public.ticket_reminder_jobs SET state='skipped',processed_at=now()
        WHERE ticket_id=v_job.ticket_id;
    ELSE
      v_request := gen_random_uuid();
      INSERT INTO public.ticket_reminder_emails
        (request_id,ticket_id,profile_id,recipient_email,ticket_title,source)
      SELECT v_request,v_ticket.id,p.id,p.email,v_ticket.title,'automatic'
      FROM public.operational_ticket_assignees a
      JOIN public.officers o ON o.id=a.officer_id AND o.active
      JOIN public.profiles p ON p.officer_id=a.officer_id
        AND p.active AND p.access_role IN ('committee','admin')
      WHERE a.ticket_id=v_ticket.id;
      GET DIAGNOSTICS v_recipients = ROW_COUNT;
      UPDATE public.ticket_reminder_jobs
         SET state=CASE WHEN v_recipients>0 THEN 'queued' ELSE 'no_recipients' END,
             processed_at=now()
       WHERE ticket_id=v_job.ticket_id;
    END IF;
    v_processed := v_processed+1;
  END LOOP;
  RETURN v_processed;
END;
$$;
REVOKE ALL ON FUNCTION public.prepare_due_ticket_reminders(integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.prepare_due_ticket_reminders(integer) TO service_role;

CREATE FUNCTION public.claim_ticket_reminder_emails(p_limit integer DEFAULT 10)
RETURNS SETOF public.ticket_reminder_emails LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 20 THEN
    RAISE EXCEPTION 'invalid batch size';
  END IF;
  UPDATE public.ticket_reminder_emails m SET state='cancelled',finished_at=now()
  WHERE m.state='pending' AND (
    NOT EXISTS (
      SELECT 1 FROM public.profiles p
      JOIN public.operational_ticket_assignees a
        ON a.officer_id=p.officer_id AND a.ticket_id=m.ticket_id
      JOIN public.officers o ON o.id=a.officer_id AND o.active
      WHERE p.id=m.profile_id AND p.active
        AND p.access_role IN ('committee','admin')
        AND p.email=m.recipient_email
    )
    OR NOT EXISTS (
      SELECT 1 FROM public.operational_records r
      WHERE r.id=m.ticket_id AND r.is_current AND r.review_status='approved'
        AND r.visibility='committee'
        AND (m.source='manual'
             OR r.execution_status NOT IN ('completed','cancelled'))
    )
  );
  RETURN QUERY
    WITH claimed AS (
      SELECT m.id FROM public.ticket_reminder_emails m
      JOIN public.profiles p ON p.id=m.profile_id AND p.active
        AND p.access_role IN ('committee','admin') AND p.email=m.recipient_email
      JOIN public.operational_records r ON r.id=m.ticket_id
        AND r.is_current AND r.review_status='approved' AND r.visibility='committee'
      WHERE m.state='pending'
      ORDER BY m.created_at,m.id FOR UPDATE OF m SKIP LOCKED LIMIT p_limit
    )
    UPDATE public.ticket_reminder_emails m
       SET state='sending',attempts=attempts+1,attempted_at=now()
      FROM claimed WHERE m.id=claimed.id
    RETURNING m.*;
END;
$$;
REVOKE ALL ON FUNCTION public.claim_ticket_reminder_emails(integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_ticket_reminder_emails(integer) TO service_role;

CREATE FUNCTION public.finish_ticket_reminder_email(
  p_id uuid,p_state text,p_provider text,p_message_id text DEFAULT NULL
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF p_state NOT IN ('accepted','rejected','uncertain')
     OR p_provider NOT IN ('resend','brevo')
     OR length(coalesce(p_message_id,''))>256 THEN
    RAISE EXCEPTION 'invalid delivery result';
  END IF;
  UPDATE public.ticket_reminder_emails
     SET state=p_state,provider=p_provider,
         provider_message_id=p_message_id,finished_at=now()
   WHERE id=p_id AND state='sending';
  IF NOT FOUND THEN RAISE EXCEPTION 'reminder is not sending'; END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.finish_ticket_reminder_email(uuid,text,text,text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_ticket_reminder_email(uuid,text,text,text)
  TO service_role;

CREATE FUNCTION public.verify_ticket_reminder_worker(p_token text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
  SELECT length(p_token)=64 AND EXISTS (
    SELECT 1 FROM vault.decrypted_secrets
    WHERE name='efds_ticket_reminder_worker' AND decrypted_secret=p_token
  )
$$;
REVOKE ALL ON FUNCTION public.verify_ticket_reminder_worker(text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.verify_ticket_reminder_worker(text) TO service_role;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM vault.secrets
                 WHERE name='efds_ticket_reminder_worker') THEN
    PERFORM vault.create_secret(encode(extensions.gen_random_bytes(32),'hex'),
                                'efds_ticket_reminder_worker');
  END IF;
END;
$$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='efds-ticket-reminders') THEN
    PERFORM cron.schedule(
      'efds-ticket-reminders','* * * * *',
      $job$
      SELECT net.http_post(
        url := 'https://immldithmugfrpojetmm.supabase.co/functions/v1/send-ticket-reminders',
        headers := jsonb_build_object(
          'Content-Type','application/json',
          'X-EFDS-Worker-Token',
          (SELECT decrypted_secret FROM vault.decrypted_secrets
           WHERE name='efds_ticket_reminder_worker')
        ),
        body := '{}'::jsonb,
        timeout_milliseconds := 10000
      );
      $job$
    );
  END IF;
END;
$$;
"""


HISTORY_SQL = """
CREATE OR REPLACE FUNCTION public.committee_ticket_history(p_ticket_id uuid DEFAULT NULL)
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
    AND e.action IN ('committee_create', 'committee_update', 'committee_assign', 'committee_status', 'committee_reminder')
    AND (p_ticket_id IS NULL OR ticket.id = p_ticket_id)
  ORDER BY e.created_at DESC
  LIMIT 2000;
END;
$function$;
REVOKE ALL ON FUNCTION public.committee_ticket_history(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.committee_ticket_history(uuid) TO authenticated;
"""


def upgrade() -> None:
    op.execute(SQL)
    op.execute(WORKER_SQL)
    op.execute(HISTORY_SQL)


def downgrade() -> None:
    raise RuntimeError("Ticket reminder delivery history requires a reviewed forward migration")
