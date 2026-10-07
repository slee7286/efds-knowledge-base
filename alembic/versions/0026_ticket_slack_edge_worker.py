"""Wake the same website-ticket outbox after commit; retain hourly publisher."""
from collections.abc import Sequence

from alembic import op

revision: str = "0026_ticket_slack_edge_worker"
down_revision: str | None = "0025_website_ticket_slack_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
-- pg_net queues HTTP until commit. A failed wake-up is not a failed ticket save;
-- the independent minute job and existing hourly Python worker can recover.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM vault.secrets WHERE name='efds_ticket_slack_worker') THEN
    PERFORM vault.create_secret(encode(extensions.gen_random_bytes(32),'hex'),
                                'efds_ticket_slack_worker');
  END IF;
END $$;

CREATE FUNCTION public.verify_ticket_slack_worker(p_token text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  SELECT p_token ~ '^[0-9a-f]{64}$' AND EXISTS (
    SELECT 1 FROM vault.decrypted_secrets
    WHERE name='efds_ticket_slack_worker' AND decrypted_secret=p_token
  )
$$;
REVOKE ALL ON FUNCTION public.verify_ticket_slack_worker(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.verify_ticket_slack_worker(text) TO service_role;

-- Mirror OutboxStore.claim: transaction commits on RPC return, before any Slack POST.
CREATE FUNCTION public.claim_ticket_slack_event()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_event jsonb;
BEGIN
  WITH candidate AS (
    SELECT o.id,o.state AS previous_state,r.root_ts,r.create_event_id
    FROM public.ticket_slack_outbox o
    LEFT JOIN public.ticket_slack_roots r ON r.ticket_id=o.ticket_id
    WHERE o.retry_at<=now()
      AND (o.state IN ('pending','uncertain') OR
           (o.state='sending' AND o.claimed_at<now()-interval '2 hours'))
      AND NOT EXISTS (
        SELECT 1 FROM public.ticket_slack_outbox earlier
        WHERE earlier.ticket_id=o.ticket_id AND earlier.id < o.id
          AND earlier.state <> 'posted'
      )
      AND (o.action='committee_create' OR r.root_ts IS NOT NULL)
    ORDER BY o.id FOR UPDATE OF o SKIP LOCKED LIMIT 1
  ), updated AS (
    UPDATE public.ticket_slack_outbox o
    SET state=CASE WHEN candidate.previous_state='uncertain' THEN 'uncertain' ELSE 'sending' END,
        claimed_at=now(),retry_at=now()+interval '15 minutes',attempts=o.attempts+1
    FROM candidate WHERE o.id=candidate.id
    RETURNING o.event_id,o.ticket_id,o.action,o.snapshot,o.channel,
              candidate.root_ts,candidate.create_event_id,
              candidate.previous_state <> 'pending' AS reconcile_only
  )
  SELECT to_jsonb(updated) INTO v_event FROM updated;
  RETURN v_event;
END $$;
REVOKE ALL ON FUNCTION public.claim_ticket_slack_event() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_ticket_slack_event() TO service_role;

CREATE FUNCTION public.finish_ticket_slack_event(p_event_id uuid,p_ts text,p_root_ts text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_event public.ticket_slack_outbox%ROWTYPE;
DECLARE v_root public.ticket_slack_roots%ROWTYPE;
BEGIN
  IF p_ts !~ '^[0-9]+[.][0-9]+$' OR p_root_ts !~ '^[0-9]+[.][0-9]+$' THEN
    RAISE EXCEPTION 'invalid Slack timestamp';
  END IF;
  SELECT * INTO v_event FROM public.ticket_slack_outbox
    WHERE event_id=p_event_id AND state IN ('sending','uncertain') FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'delivery claim changed'; END IF;
  IF v_event.action='committee_create' THEN
    IF p_ts <> p_root_ts THEN RAISE EXCEPTION 'root timestamp mismatch'; END IF;
    INSERT INTO public.ticket_slack_roots(ticket_id,create_event_id,channel,root_ts)
      VALUES (v_event.ticket_id,p_event_id,v_event.channel,p_root_ts)
      ON CONFLICT (ticket_id) DO NOTHING;
  END IF;
  SELECT * INTO v_root FROM public.ticket_slack_roots WHERE ticket_id=v_event.ticket_id;
  IF NOT FOUND OR v_root.channel<>v_event.channel OR v_root.root_ts<>p_root_ts
     OR (v_event.action='committee_create' AND v_root.create_event_id<>p_event_id) THEN
    RAISE EXCEPTION 'root mismatch';
  END IF;
  UPDATE public.ticket_slack_outbox SET state='posted',slack_ts=p_ts,delivered_at=now()
    WHERE event_id=p_event_id AND state IN ('sending','uncertain');
  IF NOT FOUND THEN RAISE EXCEPTION 'delivery claim changed'; END IF;
END $$;
REVOKE ALL ON FUNCTION public.finish_ticket_slack_event(uuid,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_ticket_slack_event(uuid,text,text) TO service_role;

CREATE FUNCTION public.defer_ticket_slack_event(p_event_id uuid,p_uncertain boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF p_uncertain IS NULL THEN RAISE EXCEPTION 'invalid outcome'; END IF;
  UPDATE public.ticket_slack_outbox
  SET state=CASE WHEN p_uncertain THEN 'uncertain' ELSE 'pending' END,
      retry_at=now()+CASE WHEN p_uncertain THEN interval '15 minutes'
        ELSE (least(3600,30*power(2,least(attempts,7))) * interval '1 second') END
  WHERE event_id=p_event_id AND state IN ('sending','uncertain')
    AND (p_uncertain OR state='sending');
  IF NOT FOUND THEN RAISE EXCEPTION 'delivery claim changed'; END IF;
END $$;
REVOKE ALL ON FUNCTION public.defer_ticket_slack_event(uuid,boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.defer_ticket_slack_event(uuid,boolean) TO service_role;

CREATE FUNCTION public.wake_ticket_slack_worker()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  PERFORM net.http_post(
    url := 'https://immldithmugfrpojetmm.supabase.co/functions/v1/send-ticket-slack',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'X-EFDS-Worker-Token',
      (SELECT decrypted_secret FROM vault.decrypted_secrets
       WHERE name='efds_ticket_slack_worker')
    ), body := '{}'::jsonb, timeout_milliseconds := 10000
  );
  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.wake_ticket_slack_worker() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER wake_ticket_slack_worker
AFTER INSERT ON public.ticket_slack_outbox
FOR EACH ROW EXECUTE FUNCTION public.wake_ticket_slack_worker();

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='efds-ticket-slack') THEN
    PERFORM cron.schedule('efds-ticket-slack','* * * * *',
      $job$ SELECT net.http_post(
        url := 'https://immldithmugfrpojetmm.supabase.co/functions/v1/send-ticket-slack',
        headers := jsonb_build_object(
          'Content-Type','application/json',
          'X-EFDS-Worker-Token',
          (SELECT decrypted_secret FROM vault.decrypted_secrets
           WHERE name='efds_ticket_slack_worker')
        ), body := '{}'::jsonb, timeout_milliseconds := 10000
      ); $job$);
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    raise RuntimeError("Review ticket Slack delivery state before removing its worker")
