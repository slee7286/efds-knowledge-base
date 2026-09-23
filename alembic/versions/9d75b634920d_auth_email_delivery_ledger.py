"""auth email delivery ledger

Revision ID: 9d75b634920d
Revises: 0021_profile_photos
Create Date: 2026-09-23 16:37:18.730859
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9d75b634920d'
down_revision: Union[str, None] = '0021_profile_photos'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE public.auth_email_deliveries (
  delivery_key text PRIMARY KEY CHECK (delivery_key ~ '^[a-f0-9]{64}$'),
  status text NOT NULL CHECK (status IN ('sending','accepted','rejected','uncertain')),
  provider text CHECK (provider IN ('resend','brevo')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_email_deliveries_created_idx ON public.auth_email_deliveries(created_at);
ALTER TABLE public.auth_email_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.auth_email_deliveries FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.auth_email_deliveries TO service_role;
COMMENT ON TABLE public.auth_email_deliveries IS 'Service-only auth email deduplication. No recipients, message bodies or tokens are stored. accepted means provider acceptance, not inbox delivery.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE public.auth_email_deliveries")

