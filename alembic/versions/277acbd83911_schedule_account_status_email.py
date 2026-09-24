"""Send queued account status emails through the private Edge worker each minute."""

from collections.abc import Sequence

from alembic import op

revision: str = "277acbd83911"
down_revision: str | None = "6e9d1c2a4b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_cron;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM cron.job WHERE jobname = 'efds-account-status-email'
  ) THEN
    PERFORM cron.schedule(
      'efds-account-status-email', '* * * * *',
      $job$
      SELECT net.http_post(
        url := 'https://immldithmugfrpojetmm.supabase.co/functions/v1/send-account-status-email',
        headers := jsonb_build_object(
          'Content-Type', 'application/json',
          'X-EFDS-Worker-Token',
          (SELECT decrypted_secret FROM vault.decrypted_secrets
           WHERE name = 'efds_account_notice_worker')
        ),
        body := '{}'::jsonb,
        timeout_milliseconds := 10000
      );
      $job$
    );
  END IF;
END;
$$;
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    raise RuntimeError("Scheduled account notices require a reviewed forward migration")
