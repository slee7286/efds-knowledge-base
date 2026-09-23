"""Remove the inherited PUBLIC grant from the profile-protection trigger."""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_restrict_profile_trigger_execution"
down_revision: str | None = "0018_restrict_anonymous_rpc_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.protect_efds_profile_fields() FROM PUBLIC")


def downgrade() -> None:
    op.execute("GRANT EXECUTE ON FUNCTION public.protect_efds_profile_fields() TO PUBLIC")
