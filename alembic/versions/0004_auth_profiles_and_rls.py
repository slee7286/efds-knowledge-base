"""Add EFDS application profiles, external access exceptions, and RLS.

Revision ID: 0004_auth_profiles_and_rls
Revises: 0003_icu_knowledge_layer
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_auth_profiles_and_rls"
down_revision: str | None = "0003_icu_knowledge_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

jsonb = postgresql.JSONB(astext_type=sa.Text())
uuid = postgresql.UUID(as_uuid=True)

MEMBER_TYPES = "'imperial', 'external', 'alumni', 'departmental_representative', 'other'"
ACCESS_ROLES = "'viewer', 'member', 'committee', 'admin'"

COMMITTEE_TABLES = (
    "officers",
    "documents",
    "meetings",
    "meeting_attendees",
    "decisions",
    "action_items",
    "knowledge_articles",
    "knowledge_topics",
    "knowledge_roles",
    "knowledge_article_topics",
    "knowledge_article_roles",
    "knowledge_requirements",
    "knowledge_requirement_roles",
    "knowledge_timing_rules",
    "knowledge_processes",
    "knowledge_process_steps",
    "knowledge_process_roles",
    "knowledge_process_resources",
    "knowledge_resources",
    "knowledge_contacts",
    "knowledge_article_relationships",
)
ADMIN_TABLES = (
    "knowledge_article_changes",
    "knowledge_extraction_runs",
    "ingestion_runs",
    "slack_channels",
    "slack_messages",
)
RLS_TABLES = ("profiles", "auth_access_exceptions", *COMMITTEE_TABLES, *ADMIN_TABLES)


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def _create_role_policy(table: str, required_role: str) -> None:
    policy_name = f"{table}_select_{required_role}"
    op.execute(
        f"""
        CREATE POLICY {policy_name}
        ON public.{table}
        FOR SELECT TO authenticated
        USING (public.has_efds_role('{required_role}'))
        """
    )


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("auth_user_id", uuid, nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("full_name", sa.Text()),
        sa.Column("member_type", sa.Text(), nullable=False, server_default=sa.text("'imperial'")),
        sa.Column("access_role", sa.Text(), nullable=False, server_default=sa.text("'member'")),
        sa.Column("officer_id", uuid, sa.ForeignKey("officers.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("email = lower(email)", name="ck_profiles_email_lowercase"),
        sa.CheckConstraint(f"member_type IN ({MEMBER_TYPES})", name="ck_profiles_member_type"),
        sa.CheckConstraint(f"access_role IN ({ACCESS_ROLES})", name="ck_profiles_access_role"),
    )
    op.create_index("ix_profiles_auth_user_id", "profiles", ["auth_user_id"])
    op.create_index("ix_profiles_access_role", "profiles", ["access_role"])
    op.create_index("ix_profiles_officer_id", "profiles", ["officer_id"])

    op.create_table(
        "auth_access_exceptions",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("access_role", sa.Text(), nullable=False),
        sa.Column("member_type", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_profile_id", uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("email = lower(email)", name="ck_auth_access_exceptions_email_lowercase"),
        sa.CheckConstraint(f"access_role IN ({ACCESS_ROLES})", name="ck_auth_access_exceptions_access_role"),
        sa.CheckConstraint(
            f"member_type IS NULL OR member_type IN ({MEMBER_TYPES})",
            name="ck_auth_access_exceptions_member_type",
        ),
    )
    op.create_index(
        "ix_auth_access_exceptions_active_expiry",
        "auth_access_exceptions",
        ["active", "expires_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.current_efds_access_role()
        RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT p.access_role
          FROM public.profiles AS p
          WHERE p.auth_user_id = auth.uid() AND p.active
          LIMIT 1
        $$;

        CREATE OR REPLACE FUNCTION public.has_efds_role(required_role text)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT CASE public.current_efds_access_role()
            WHEN 'admin' THEN required_role IN ('viewer', 'member', 'committee', 'admin')
            WHEN 'committee' THEN required_role IN ('viewer', 'member', 'committee')
            WHEN 'member' THEN required_role IN ('viewer', 'member')
            WHEN 'viewer' THEN required_role = 'viewer'
            ELSE false
          END
        $$;

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

        CREATE OR REPLACE FUNCTION public.current_efds_external_access_role()
        RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT e.access_role
          FROM public.auth_access_exceptions AS e
          WHERE e.email = lower(trim(coalesce(auth.jwt() ->> 'email', '')))
            AND e.active
            AND (e.expires_at IS NULL OR e.expires_at > now())
          LIMIT 1
        $$;

        CREATE OR REPLACE FUNCTION public.protect_efds_profile_fields()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
          -- A trusted backend connection has no Supabase auth.uid().
          IF auth.uid() IS NULL OR public.has_efds_role('admin') THEN
            RETURN NEW;
          END IF;
          IF OLD.auth_user_id IS DISTINCT FROM NEW.auth_user_id
             OR (OLD.email IS DISTINCT FROM NEW.email
                 AND NEW.email <> lower(coalesce(auth.jwt() ->> 'email', '')))
             OR (OLD.member_type IS DISTINCT FROM NEW.member_type
                 AND NOT (
                   NEW.member_type = 'imperial'
                   AND split_part(NEW.email, '@', 2) IN ('ic.ac.uk', 'imperial.ac.uk')
                   AND NEW.email = lower(coalesce(auth.jwt() ->> 'email', ''))
                 ))
             OR OLD.access_role IS DISTINCT FROM NEW.access_role
             OR OLD.officer_id IS DISTINCT FROM NEW.officer_id
             OR OLD.active IS DISTINCT FROM NEW.active
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'profile authorization fields are managed by EFDS administrators';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER protect_efds_profile_fields
        BEFORE UPDATE ON public.profiles
        FOR EACH ROW EXECUTE FUNCTION public.protect_efds_profile_fields();
        """
    )

    op.execute("ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.auth_access_exceptions ENABLE ROW LEVEL SECURITY")
    for table in COMMITTEE_TABLES + ADMIN_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY profiles_select_self_or_admin
        ON public.profiles FOR SELECT TO authenticated
        USING (auth_user_id = auth.uid() OR public.has_efds_role('admin'));

        CREATE POLICY profiles_insert_self
        ON public.profiles FOR INSERT TO authenticated
        WITH CHECK (
          auth_user_id = auth.uid()
          AND email = lower(coalesce(auth.jwt() ->> 'email', ''))
          AND (
            (member_type = 'imperial' AND access_role = 'member')
            OR (
              access_role IN ('viewer', 'member', 'committee')
              AND access_role = public.current_efds_external_access_role()
            )
          )
        );

        CREATE POLICY profiles_update_self_or_admin
        ON public.profiles FOR UPDATE TO authenticated
        USING (auth_user_id = auth.uid() OR public.has_efds_role('admin'))
        WITH CHECK (auth_user_id = auth.uid() OR public.has_efds_role('admin'));

        CREATE POLICY profiles_delete_admin
        ON public.profiles FOR DELETE TO authenticated
        USING (public.has_efds_role('admin'));

        CREATE POLICY auth_access_exceptions_admin
        ON public.auth_access_exceptions FOR ALL TO authenticated
        USING (public.has_efds_role('admin'))
        WITH CHECK (public.has_efds_role('admin'));
        """
    )
    for table in COMMITTEE_TABLES:
        _create_role_policy(table, "committee")
    for table in ADMIN_TABLES:
        _create_role_policy(table, "admin")

    for table in RLS_TABLES:
        if table == "profiles":
            op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO authenticated")
        elif table == "auth_access_exceptions":
            op.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON public.auth_access_exceptions TO authenticated"
            )
        else:
            op.execute(f"GRANT SELECT ON public.{table} TO authenticated")
        op.execute(f"REVOKE ALL ON public.{table} FROM anon")

    op.execute("REVOKE ALL ON FUNCTION public.is_external_email_eligible(text) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.is_external_email_eligible(text) TO anon, authenticated"
    )
    op.execute("REVOKE ALL ON FUNCTION public.current_efds_external_access_role() FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.current_efds_external_access_role() TO authenticated"
    )
    op.execute("REVOKE ALL ON FUNCTION public.current_efds_access_role() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.current_efds_access_role() TO authenticated")
    op.execute("REVOKE ALL ON FUNCTION public.has_efds_role(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.has_efds_role(text) TO authenticated")


def downgrade() -> None:
    for table in COMMITTEE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_select_committee ON public.{table}")
    for table in ADMIN_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_select_admin ON public.{table}")
    op.execute("DROP POLICY IF EXISTS profiles_select_self_or_admin ON public.profiles")
    op.execute("DROP POLICY IF EXISTS profiles_insert_self ON public.profiles")
    op.execute("DROP POLICY IF EXISTS profiles_update_self_or_admin ON public.profiles")
    op.execute("DROP POLICY IF EXISTS profiles_delete_admin ON public.profiles")
    op.execute("DROP POLICY IF EXISTS auth_access_exceptions_admin ON public.auth_access_exceptions")
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS protect_efds_profile_fields ON public.profiles")
    op.execute("DROP FUNCTION IF EXISTS public.protect_efds_profile_fields()")
    op.execute("DROP FUNCTION IF EXISTS public.current_efds_external_access_role()")
    op.execute("DROP FUNCTION IF EXISTS public.is_external_email_eligible(text)")
    op.execute("DROP FUNCTION IF EXISTS public.has_efds_role(text)")
    op.execute("DROP FUNCTION IF EXISTS public.current_efds_access_role()")
    op.drop_table("auth_access_exceptions")
    op.drop_index("ix_profiles_officer_id", table_name="profiles")
    op.drop_index("ix_profiles_access_role", table_name="profiles")
    op.drop_index("ix_profiles_auth_user_id", table_name="profiles")
    op.drop_table("profiles")
