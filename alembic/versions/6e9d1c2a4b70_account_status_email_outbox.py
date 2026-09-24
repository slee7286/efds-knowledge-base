"""Queue an email atomically with every substantive account access change.

Only the service-role mail worker can claim or finish notices. Provider
acceptance is recorded separately from a user's inbox delivery.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6e9d1c2a4b70"
down_revision: str | None = "0f2a6357bea2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
CREATE TABLE public.account_status_notices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
  access_version integer NOT NULL,
  recipient_email text NOT NULL CHECK (length(recipient_email) BETWEEN 3 AND 254),
  previous_role text NOT NULL,
  new_role text NOT NULL,
  previous_verification_status text NOT NULL,
  new_verification_status text NOT NULL,
  previous_officer_id uuid,
  new_officer_id uuid,
  previous_active boolean NOT NULL,
  new_active boolean NOT NULL,
  state text NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','sending','accepted','rejected','uncertain')),
  provider text CHECK (provider IN ('resend','brevo')),
  provider_message_id text CHECK (length(provider_message_id) <= 256),
  attempts integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  attempted_at timestamptz,
  finished_at timestamptz,
  UNIQUE (profile_id, access_version)
);
CREATE INDEX account_status_notices_pending_idx
  ON public.account_status_notices (created_at, id) WHERE state = 'pending';
CREATE INDEX account_status_notices_attention_idx
  ON public.account_status_notices (created_at DESC)
  WHERE state IN ('sending','rejected','uncertain');
ALTER TABLE public.account_status_notices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.account_status_notices FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.account_status_notices TO service_role;
COMMENT ON TABLE public.account_status_notices IS
  'Private transactional status-email outbox. accepted means provider accepted, not inbox delivered. Uncertain attempts require investigation before replay.';

CREATE FUNCTION public.bump_account_status_version()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF OLD.access_role IS DISTINCT FROM NEW.access_role
     OR OLD.efds_verification_status IS DISTINCT FROM NEW.efds_verification_status
     OR OLD.officer_id IS DISTINCT FROM NEW.officer_id
     OR OLD.active IS DISTINCT FROM NEW.active THEN
    IF NEW.access_version < OLD.access_version THEN
      RAISE EXCEPTION 'access version cannot move backwards';
    END IF;
    IF NEW.access_version = OLD.access_version THEN
      NEW.access_version := OLD.access_version + 1;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.bump_account_status_version() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER bump_account_status_version BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.bump_account_status_version();

CREATE FUNCTION public.queue_account_status_notice()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF OLD.access_role IS DISTINCT FROM NEW.access_role
     OR OLD.efds_verification_status IS DISTINCT FROM NEW.efds_verification_status
     OR OLD.officer_id IS DISTINCT FROM NEW.officer_id
     OR OLD.active IS DISTINCT FROM NEW.active THEN
    INSERT INTO public.account_status_notices (
      profile_id, access_version, recipient_email,
      previous_role, new_role,
      previous_verification_status, new_verification_status,
      previous_officer_id, new_officer_id,
      previous_active, new_active
    ) VALUES (
      NEW.id, NEW.access_version, NEW.email,
      OLD.access_role, NEW.access_role,
      OLD.efds_verification_status, NEW.efds_verification_status,
      OLD.officer_id, NEW.officer_id,
      OLD.active, NEW.active
    );
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.queue_account_status_notice() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER profiles_queue_account_status_notice
AFTER UPDATE ON public.profiles FOR EACH ROW
EXECUTE FUNCTION public.queue_account_status_notice();

CREATE FUNCTION public.claim_account_status_notices(p_limit integer DEFAULT 10)
RETURNS SETOF public.account_status_notices LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 20 THEN
    RAISE EXCEPTION 'invalid batch size';
  END IF;
  RETURN QUERY
    WITH claimed AS (
      SELECT id FROM public.account_status_notices
      WHERE state = 'pending' ORDER BY created_at, id
      FOR UPDATE SKIP LOCKED LIMIT p_limit
    )
    UPDATE public.account_status_notices n
       SET state = 'sending', attempts = attempts + 1, attempted_at = now()
      FROM claimed WHERE n.id = claimed.id
    RETURNING n.*;
END;
$$;
REVOKE ALL ON FUNCTION public.claim_account_status_notices(integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_account_status_notices(integer) TO service_role;

CREATE FUNCTION public.finish_account_status_notice(
  p_id uuid, p_state text, p_provider text, p_message_id text DEFAULT NULL
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF p_state NOT IN ('accepted','rejected','uncertain')
     OR p_provider NOT IN ('resend','brevo')
     OR length(coalesce(p_message_id,'')) > 256 THEN
    RAISE EXCEPTION 'invalid delivery result';
  END IF;
  UPDATE public.account_status_notices
     SET state = p_state, provider = p_provider,
         provider_message_id = p_message_id, finished_at = now()
   WHERE id = p_id AND state = 'sending';
  IF NOT FOUND THEN RAISE EXCEPTION 'notice is not sending'; END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.finish_account_status_notice(uuid,text,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_account_status_notice(uuid,text,text,text) TO service_role;

CREATE FUNCTION public.verify_account_notice_worker(p_token text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
  SELECT length(p_token) = 64 AND EXISTS (
    SELECT 1 FROM vault.decrypted_secrets
    WHERE name = 'efds_account_notice_worker' AND decrypted_secret = p_token
  )
$$;
REVOKE ALL ON FUNCTION public.verify_account_notice_worker(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.verify_account_notice_worker(text) TO service_role;

CREATE FUNCTION public.account_status_notice_health()
RETURNS TABLE(pending bigint, sending bigint, accepted_7d bigint,
              needs_attention bigint, oldest_pending_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT public.has_efds_role('admin') THEN
    RAISE EXCEPTION 'admin required' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
    SELECT count(*) FILTER (WHERE n.state = 'pending'),
           count(*) FILTER (WHERE n.state = 'sending'
             AND n.attempted_at >= now() - interval '2 minutes'),
           count(*) FILTER (WHERE n.state = 'accepted'
             AND n.finished_at >= now() - interval '7 days'),
           count(*) FILTER (WHERE n.state IN ('rejected','uncertain')
             OR (n.state = 'sending'
                 AND n.attempted_at < now() - interval '2 minutes')),
           min(n.created_at) FILTER (WHERE n.state = 'pending')
    FROM public.account_status_notices n;
END;
$$;
REVOKE ALL ON FUNCTION public.account_status_notice_health() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.account_status_notice_health() TO authenticated;

CREATE FUNCTION public.account_status_notice_state(
  p_profile_id uuid, p_access_version integer
) RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE v_state text;
BEGIN
  IF NOT public.has_efds_role('admin') THEN
    RAISE EXCEPTION 'admin required' USING ERRCODE = '42501';
  END IF;
  SELECT n.state INTO v_state FROM public.account_status_notices n
  WHERE n.profile_id = p_profile_id AND n.access_version = p_access_version;
  RETURN v_state;
END;
$$;
REVOKE ALL ON FUNCTION public.account_status_notice_state(uuid,integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.account_status_notice_state(uuid,integer) TO authenticated;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM vault.secrets WHERE name = 'efds_account_notice_worker') THEN
    PERFORM vault.create_secret(encode(extensions.gen_random_bytes(32), 'hex'), 'efds_account_notice_worker');
  END IF;
END;
$$;
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    raise RuntimeError("Account notification history requires a reviewed forward migration")
