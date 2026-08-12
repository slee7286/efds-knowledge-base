"""Allow explicitly approved email identities to use the magic-link path."""

from __future__ import annotations

from alembic import op


revision: str = "0009_auth_exception_magic_links"
down_revision: str | None = "0008_filesystem_institutional_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.is_external_email_eligible(candidate_email text)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM public.auth_access_exceptions AS e
            WHERE e.email = lower(trim(candidate_email))
              AND e.active
              AND (e.expires_at IS NULL OR e.expires_at > now())
          )
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.is_external_email_eligible(candidate_email text)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM public.auth_access_exceptions AS e
            WHERE e.email = lower(trim(candidate_email))
              AND split_part(e.email, '@', 2) NOT IN ('ic.ac.uk', 'imperial.ac.uk')
              AND e.active
              AND (e.expires_at IS NULL OR e.expires_at > now())
          )
        $$;
        """
    )
