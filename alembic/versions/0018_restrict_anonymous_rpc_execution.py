"""Remove Supabase's direct anon grants from privileged application RPCs.

Supabase's function default privileges can grant EXECUTE directly to anon.
Revoking PUBLIC alone therefore does not remove anonymous execution.
The email eligibility RPC remains intentionally callable before login.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_restrict_anonymous_rpc_execution"
down_revision: str | None = "0017_committee_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIVILEGED_FUNCTIONS = (
    "public.current_efds_access_role()",
    "public.current_efds_external_access_role()",
    "public.has_efds_role(text)",
    "public.mutate_committee_ticket(text,uuid,integer,jsonb)",
    "public.mutate_operational_record(text,uuid,integer,jsonb,text)",
    "public.review_knowledge_transaction(text,uuid,text,integer,jsonb,text,text,text)",
    "public.protect_efds_profile_fields()",
)


def upgrade() -> None:
    for function in PRIVILEGED_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM anon")
    op.execute("REVOKE EXECUTE ON FUNCTION public.protect_efds_profile_fields() FROM authenticated")


def downgrade() -> None:
    for function in PRIVILEGED_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO anon")
    op.execute("GRANT EXECUTE ON FUNCTION public.protect_efds_profile_fields() TO authenticated")
