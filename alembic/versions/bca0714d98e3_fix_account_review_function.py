"""Resolve ambiguous profile columns in the reviewed account RPC."""

from collections.abc import Sequence

from alembic import op

revision: str = "bca0714d98e3"
down_revision: str | None = "6ac6ff68d6db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE OR REPLACE FUNCTION public.review_efds_account(
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

""")


def downgrade() -> None:
    raise RuntimeError("Account review function corrections require a reviewed forward migration")
