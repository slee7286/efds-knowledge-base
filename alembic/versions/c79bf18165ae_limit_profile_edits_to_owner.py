"""Keep ordinary profile edits owner-scoped; admin access changes use the audit RPC."""

from collections.abc import Sequence

from alembic import op

revision: str = "c79bf18165ae"
down_revision: str | None = "bca0714d98e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
DROP POLICY profiles_update_self_or_admin ON public.profiles;
CREATE POLICY profiles_update_self_or_admin ON public.profiles
FOR UPDATE TO authenticated
USING (auth_user_id = auth.uid())
WITH CHECK (auth_user_id = auth.uid());
    """)


def downgrade() -> None:
    raise RuntimeError("Profile edit policy requires a reviewed forward migration to reverse")
