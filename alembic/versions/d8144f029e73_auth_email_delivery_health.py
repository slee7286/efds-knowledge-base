"""Track provider outcomes without retaining email addresses or auth links."""

from collections.abc import Sequence

from alembic import op

revision: str = "d8144f029e73"
down_revision: str | None = "c79bf18165ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE public.auth_email_deliveries
  ADD COLUMN provider_message_id text CHECK (length(provider_message_id) BETWEEN 1 AND 256),
  ADD COLUMN delivery_status text NOT NULL DEFAULT 'pending'
    CHECK (delivery_status IN ('pending','delivered','deferred','bounced','complained','blocked')),
  ADD COLUMN delivery_event_at timestamptz,
  ADD COLUMN resend_rejected boolean NOT NULL DEFAULT false,
  ADD COLUMN resend_quota_or_rate_limited boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX auth_email_deliveries_provider_message_idx
  ON public.auth_email_deliveries(provider, provider_message_id)
  WHERE provider_message_id IS NOT NULL;
COMMENT ON COLUMN public.auth_email_deliveries.delivery_status IS
  'pending means no authenticated provider delivery event, not that mail is undelivered.';

CREATE FUNCTION public.record_auth_email_event(
  p_provider text, p_message_id text, p_status text, p_occurred_at timestamptz
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF p_provider NOT IN ('resend','brevo')
     OR p_message_id IS NULL OR length(p_message_id) NOT BETWEEN 1 AND 256
     OR p_status NOT IN ('delivered','deferred','bounced','complained','blocked')
     OR p_occurred_at IS NULL
     OR p_occurred_at > now() + interval '1 day'
     OR p_occurred_at < now() - interval '60 days' THEN
    RAISE EXCEPTION 'Invalid delivery event';
  END IF;
  UPDATE public.auth_email_deliveries
  SET delivery_status=p_status, delivery_event_at=p_occurred_at,
      updated_at=now()
  WHERE provider=p_provider AND provider_message_id=p_message_id
    AND status='accepted'
    AND (delivery_event_at IS NULL OR p_occurred_at > delivery_event_at
         OR (p_occurred_at = delivery_event_at AND p_status IN ('bounced','complained','blocked')));
  RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION public.record_auth_email_event(text,text,text,timestamptz)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_auth_email_event(text,text,text,timestamptz)
  TO service_role;

CREATE FUNCTION public.auth_email_health() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE result jsonb;
BEGIN
  IF NOT public.has_efds_role('admin') THEN
    RAISE EXCEPTION 'Admin role required' USING ERRCODE='42501';
  END IF;
  SELECT jsonb_build_object(
    'accepted24h', count(*) FILTER (WHERE created_at >= now()-interval '24 hours' AND status='accepted'),
    'resend24h', count(*) FILTER (WHERE created_at >= now()-interval '24 hours' AND status='accepted' AND provider='resend'),
    'brevo24h', count(*) FILTER (WHERE created_at >= now()-interval '24 hours' AND status='accepted' AND provider='brevo'),
    'failed24h', count(*) FILTER (WHERE created_at >= now()-interval '24 hours' AND status IN ('rejected','uncertain','sending')),
    'quotaSignals24h', count(*) FILTER (WHERE created_at >= now()-interval '24 hours' AND resend_quota_or_rate_limited),
    'delivered7d', count(*) FILTER (WHERE created_at >= now()-interval '7 days' AND delivery_status='delivered'),
    'bounced7d', count(*) FILTER (WHERE created_at >= now()-interval '7 days' AND delivery_status IN ('bounced','complained','blocked')),
    'awaitingEvent7d', count(*) FILTER (WHERE created_at >= now()-interval '7 days' AND status='accepted' AND delivery_status='pending'),
    'lastAttemptAt', max(created_at),
    'lastDeliveryEventAt', max(delivery_event_at)
  ) INTO result FROM public.auth_email_deliveries
  WHERE created_at >= now()-interval '7 days';
  RETURN result;
END;
$$;
REVOKE ALL ON FUNCTION public.auth_email_health() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.auth_email_health() TO authenticated;
    """)


def downgrade() -> None:
    op.execute("""
DROP FUNCTION public.auth_email_health();
DROP FUNCTION public.record_auth_email_event(text,text,text,timestamptz);
DROP INDEX public.auth_email_deliveries_provider_message_idx;
ALTER TABLE public.auth_email_deliveries
  DROP COLUMN provider_message_id, DROP COLUMN delivery_status,
  DROP COLUMN delivery_event_at, DROP COLUMN resend_rejected,
  DROP COLUMN resend_quota_or_rate_limited;
    """)
