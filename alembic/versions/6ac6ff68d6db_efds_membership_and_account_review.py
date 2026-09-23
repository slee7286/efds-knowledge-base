"""Separate basic accounts from verified EFDS membership and audit admin grants."""

from collections.abc import Sequence

from alembic import op

revision: str = "6ac6ff68d6db"
down_revision: str | None = "9d75b634920d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PUBLISHED = (
    "knowledge_contacts", "knowledge_process_steps", "knowledge_processes",
    "knowledge_requirements", "knowledge_resources", "knowledge_timing_rules",
)


def upgrade() -> None:
    op.execute("""
ALTER TABLE public.profiles DROP CONSTRAINT ck_profiles_access_role;
ALTER TABLE public.profiles ADD CONSTRAINT ck_profiles_access_role
  CHECK (access_role IN ('viewer','member','efds_member','committee','admin'));
ALTER TABLE public.auth_access_exceptions DROP CONSTRAINT ck_auth_access_exceptions_access_role;
ALTER TABLE public.auth_access_exceptions ADD CONSTRAINT ck_auth_access_exceptions_access_role
  CHECK (access_role IN ('viewer','member','efds_member','committee','admin'));
ALTER TABLE public.profiles
  ADD COLUMN efds_verification_status text NOT NULL DEFAULT 'pending'
    CHECK (efds_verification_status IN ('pending','approved','declined')),
  ADD COLUMN efds_verification_claim text
    CHECK (length(efds_verification_claim) <= 500),
  ADD COLUMN efds_verified_by_profile_id uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  ADD COLUMN efds_verified_at timestamptz,
  ADD COLUMN access_version integer NOT NULL DEFAULT 1
    CHECK (access_version > 0);
-- Preserve all existing privileged roles. Existing ordinary accounts require
-- review; their university email domain is not proof of society membership.
UPDATE public.profiles SET efds_verification_status='approved', efds_verified_at=now()
WHERE access_role IN ('committee','admin');
CREATE UNIQUE INDEX profiles_active_officer_identity
ON public.profiles(officer_id) WHERE officer_id IS NOT NULL AND active;

CREATE TABLE public.account_access_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_profile_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
  actor_profile_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
  action text NOT NULL CHECK (action IN ('verify','decline','promote_committee','promote_admin','demote_member','link_officer')),
  previous_role text NOT NULL,
  new_role text NOT NULL,
  previous_verification_status text NOT NULL,
  new_verification_status text NOT NULL,
  previous_officer_id uuid,
  new_officer_id uuid,
  reason text CHECK (length(reason) <= 500),
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_access_events_target_time ON public.account_access_events(target_profile_id, occurred_at DESC);
ALTER TABLE public.account_access_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.account_access_events FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.account_access_events TO authenticated;
CREATE POLICY account_access_events_admin_read ON public.account_access_events
  FOR SELECT TO authenticated USING ((SELECT public.has_efds_role('admin')));

CREATE OR REPLACE FUNCTION public.has_efds_role(required_role text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT CASE public.current_efds_access_role()
    WHEN 'admin' THEN required_role IN ('viewer','member','efds_member','committee','admin')
    WHEN 'committee' THEN required_role IN ('viewer','member','efds_member','committee')
    WHEN 'efds_member' THEN required_role IN ('viewer','member','efds_member')
    WHEN 'member' THEN required_role IN ('viewer','member')
    WHEN 'viewer' THEN required_role = 'viewer'
    ELSE false
  END
$$;

-- A new account always enters as a basic member, even when an external-email
-- exception grants eligibility. Existing privileged profiles stay unchanged.
DROP POLICY profiles_insert_self ON public.profiles;
CREATE POLICY profiles_insert_self ON public.profiles FOR INSERT TO authenticated
WITH CHECK (
  auth_user_id=auth.uid()
  AND email=lower(coalesce(auth.jwt()->>'email',''))
  AND access_role='member'
  AND efds_verification_status='pending'
  AND officer_id IS NULL
  AND efds_verified_by_profile_id IS NULL
  AND efds_verified_at IS NULL
  AND access_version=1
  AND (
    (member_type='imperial' AND split_part(email,'@',2) IN ('ic.ac.uk','imperial.ac.uk'))
    OR (member_type='external' AND public.current_efds_external_access_role() IS NOT NULL)
  )
);

-- Raw profile writes may change contact/display details only. All role,
-- verification and roster mutations must go through the audited RPC.
REVOKE UPDATE, DELETE ON public.profiles FROM authenticated;
GRANT UPDATE(email, full_name, avatar_path, member_type, last_login_at, efds_verification_claim)
  ON public.profiles TO authenticated;
DROP POLICY profiles_delete_admin ON public.profiles;

CREATE OR REPLACE FUNCTION public.protect_efds_profile_fields()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  IF auth.uid() IS NULL THEN RETURN NEW; END IF;
  IF OLD.auth_user_id IS DISTINCT FROM NEW.auth_user_id
     OR (OLD.email IS DISTINCT FROM NEW.email
         AND NEW.email <> lower(coalesce(auth.jwt()->>'email','')))
     OR (OLD.member_type IS DISTINCT FROM NEW.member_type
         AND NOT (NEW.member_type='imperial'
                  AND split_part(NEW.email,'@',2) IN ('ic.ac.uk','imperial.ac.uk')
                  AND NEW.email=lower(coalesce(auth.jwt()->>'email',''))))
     OR OLD.access_role IS DISTINCT FROM NEW.access_role
     OR OLD.officer_id IS DISTINCT FROM NEW.officer_id
     OR OLD.active IS DISTINCT FROM NEW.active
     OR OLD.efds_verification_status IS DISTINCT FROM NEW.efds_verification_status
     OR OLD.efds_verified_by_profile_id IS DISTINCT FROM NEW.efds_verified_by_profile_id
     OR OLD.efds_verified_at IS DISTINCT FROM NEW.efds_verified_at
     OR OLD.access_version IS DISTINCT FROM NEW.access_version
     OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
    -- The reviewed SECURITY DEFINER RPC is the only authenticated writer of
    -- these fields. Column grants also block direct REST updates.
    IF current_setting('efds.reviewed_account_update', true) IS DISTINCT FROM 'on'
       THEN RAISE EXCEPTION 'profile authorization fields require reviewed admin action';
    END IF;
  END IF;
  IF OLD.efds_verification_claim IS DISTINCT FROM NEW.efds_verification_claim
     AND OLD.efds_verification_status='approved'
     AND current_setting('efds.reviewed_account_update', true) IS DISTINCT FROM 'on'
     THEN RAISE EXCEPTION 'approved verification details cannot be changed without admin review';
  END IF;
  RETURN NEW;
END;
$$;
    """)
    # Prevent future 'member' content from leaking to every signed-in account.
    for table in PUBLISHED:
        op.execute(f"""
DROP POLICY {table}_select_published_member ON public.{table};
CREATE POLICY {table}_select_published_member ON public.{table}
FOR SELECT TO authenticated USING (
  review_status='approved' AND NOT is_stale
  AND (visibility='public' OR
       (visibility='member' AND (SELECT public.has_efds_role('efds_member'))))
);
        """)
    op.execute("""
DROP POLICY operational_records_select_member ON public.operational_records;
CREATE POLICY operational_records_select_member ON public.operational_records
FOR SELECT TO authenticated USING (
  review_status='approved' AND is_current AND (SELECT public.has_efds_role('member'))
  AND (visibility='public' OR
       (visibility='member' AND (SELECT public.has_efds_role('efds_member'))))
);
DROP POLICY retrieval_units_member_select ON public.retrieval_units;
CREATE POLICY retrieval_units_member_select ON public.retrieval_units
FOR SELECT TO authenticated USING (
  (SELECT public.has_efds_role('member'))
  AND source_type NOT IN ('document','slack_message')
  AND (visibility='public' OR
       (visibility='member' AND (SELECT public.has_efds_role('efds_member'))))
  AND review_status='approved' AND is_current AND NOT is_stale AND NOT is_deleted
);
    """)
    op.execute("""
CREATE FUNCTION public.review_efds_account(
  p_target_profile_id uuid,
  p_expected_version integer,
  p_action text,
  p_reason text DEFAULT NULL,
  p_officer_id uuid DEFAULT NULL
) RETURNS TABLE(profile_id uuid, access_role text, verification_status text, officer_id uuid, access_version integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
#variable_conflict use_column
DECLARE
  v_actor public.profiles%ROWTYPE;
  v_target public.profiles%ROWTYPE;
  v_new_role text;
  v_new_status text;
  v_new_officer uuid;
  v_verify_by uuid;
  v_verify_at timestamptz;
BEGIN
  SELECT * INTO v_actor FROM public.profiles
    WHERE auth_user_id=auth.uid() AND active AND access_role='admin';
  IF NOT FOUND THEN RAISE EXCEPTION 'admin required' USING ERRCODE='42501'; END IF;
  IF p_action NOT IN ('verify','decline','promote_committee','promote_admin','demote_member','link_officer')
    THEN RAISE EXCEPTION 'unsupported review action'; END IF;
  IF p_reason IS NOT NULL AND length(p_reason)>500
    THEN RAISE EXCEPTION 'reason is too long'; END IF;
  SELECT * INTO v_target FROM public.profiles WHERE id=p_target_profile_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'profile not found'; END IF;
  IF NOT v_target.active THEN RAISE EXCEPTION 'inactive profile'; END IF;
  IF v_target.access_version<>p_expected_version THEN
    RAISE EXCEPTION 'profile changed; refresh before reviewing' USING ERRCODE='40001';
  END IF;
  IF v_target.id=v_actor.id AND p_action IN ('verify','decline','promote_committee','promote_admin','demote_member')
    THEN RAISE EXCEPTION 'cannot review your own access'; END IF;
  v_new_role:=v_target.access_role;
  v_new_status:=v_target.efds_verification_status;
  v_new_officer:=v_target.officer_id;
  v_verify_by:=v_target.efds_verified_by_profile_id;
  v_verify_at:=v_target.efds_verified_at;
  CASE p_action
    WHEN 'verify' THEN
      IF v_target.access_role NOT IN ('viewer','member') THEN RAISE EXCEPTION 'account already elevated'; END IF;
      v_new_role:='efds_member'; v_new_status:='approved';
      v_verify_by:=v_actor.id; v_verify_at:=now();
    WHEN 'decline' THEN
      IF v_target.access_role NOT IN ('viewer','member') THEN RAISE EXCEPTION 'cannot decline elevated account'; END IF;
      IF nullif(btrim(coalesce(p_reason,'')),'') IS NULL THEN RAISE EXCEPTION 'decline reason required'; END IF;
      v_new_status:='declined'; v_verify_by:=v_actor.id; v_verify_at:=now();
    WHEN 'promote_committee' THEN
      IF v_target.efds_verification_status<>'approved' OR v_target.access_role NOT IN ('efds_member','member')
        THEN RAISE EXCEPTION 'verify EFDS membership first'; END IF;
      v_new_role:='committee';
    WHEN 'promote_admin' THEN
      IF v_target.efds_verification_status<>'approved' OR v_target.access_role NOT IN ('efds_member','committee')
        THEN RAISE EXCEPTION 'verify EFDS membership first'; END IF;
      IF nullif(btrim(coalesce(p_reason,'')),'') IS NULL THEN RAISE EXCEPTION 'admin promotion reason required'; END IF;
      v_new_role:='admin';
    WHEN 'demote_member' THEN
      IF v_target.access_role NOT IN ('committee','admin') THEN RAISE EXCEPTION 'account is not elevated'; END IF;
      IF v_target.access_role='admin' AND
         (SELECT count(*) FROM public.profiles WHERE access_role='admin' AND active)<=1
        THEN RAISE EXCEPTION 'cannot remove the last active admin'; END IF;
      v_new_role:=CASE WHEN v_target.efds_verification_status='approved' THEN 'efds_member' ELSE 'member' END;
      v_new_officer:=NULL;
    WHEN 'link_officer' THEN
      IF v_target.access_role NOT IN ('committee','admin') THEN RAISE EXCEPTION 'committee role required'; END IF;
      IF p_officer_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM public.officers WHERE id=p_officer_id AND active)
          THEN RAISE EXCEPTION 'active officer not found'; END IF;
        IF EXISTS (SELECT 1 FROM public.profiles WHERE officer_id=p_officer_id AND active AND id<>v_target.id)
          THEN RAISE EXCEPTION 'officer already linked'; END IF;
      END IF;
      v_new_officer:=p_officer_id;
  END CASE;
  PERFORM set_config('efds.reviewed_account_update','on',true);
  UPDATE public.profiles SET access_role=v_new_role,
    efds_verification_status=v_new_status, officer_id=v_new_officer,
    efds_verified_by_profile_id=v_verify_by, efds_verified_at=v_verify_at,
    access_version=access_version+1, updated_at=now()
    WHERE id=v_target.id;
  INSERT INTO public.account_access_events(
    target_profile_id,actor_profile_id,action,previous_role,new_role,
    previous_verification_status,new_verification_status,
    previous_officer_id,new_officer_id,reason
  ) VALUES (
    v_target.id,v_actor.id,p_action,v_target.access_role,v_new_role,
    v_target.efds_verification_status,v_new_status,
    v_target.officer_id,v_new_officer,p_reason
  );
  RETURN QUERY SELECT v_target.id,v_new_role,v_new_status,v_new_officer,v_target.access_version+1;
END;
$$;
REVOKE ALL ON FUNCTION public.review_efds_account(uuid,integer,text,text,uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.review_efds_account(uuid,integer,text,text,uuid) TO authenticated;
    """)


def downgrade() -> None:
    # Downgrading while EFDS-member accounts exist would destroy access state.
    raise RuntimeError("This membership migration requires a reviewed forward migration to reverse")
