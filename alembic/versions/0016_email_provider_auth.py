"""Allow verified Imperial email identities to request EFDS email sign-in."""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_email_provider_auth"
down_revision: str | None = "0015_committee_slack_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This is an eligibility check for sending an email, not an access grant.
    # The callback still requires a confirmed Supabase identity and the site's
    # profile authorization; outside Imperial, an active exception is required.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.is_external_email_eligible(candidate_email text)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT coalesce(
            lower(btrim(candidate_email)) ~ '^[^@[:space:]]+@(ic\\.ac\\.uk|imperial\\.ac\\.uk)$',
            false
          ) OR EXISTS (
            SELECT 1
            FROM public.auth_access_exceptions AS e
            WHERE e.email = lower(btrim(candidate_email))
              AND e.active
              AND (e.expires_at IS NULL OR e.expires_at > now())
          )
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.is_external_email_eligible(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.is_external_email_eligible(text) TO anon, authenticated")


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION public.is_external_email_eligible(candidate_email text)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM public.auth_access_exceptions AS e
            WHERE e.email = lower(btrim(candidate_email))
              AND e.active
              AND (e.expires_at IS NULL OR e.expires_at > now())
          )
        $$;
    """)
