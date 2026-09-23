"""Summarize private auth-mail health and prune expired deduplication records."""

from __future__ import annotations

import os
import sys

import psycopg


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Missing DATABASE_URL repository secret", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url, connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM public.auth_email_deliveries "
                    "WHERE created_at < now() - interval '45 days'"
                )
                removed = cursor.rowcount
                cursor.execute("""
                    SELECT
                      count(*) FILTER (WHERE status='accepted'),
                      count(*) FILTER (WHERE status IN ('sending','uncertain','rejected')),
                      count(*) FILTER (WHERE provider='resend' AND status='accepted'),
                      count(*) FILTER (WHERE provider='brevo' AND status='accepted'),
                      count(*) FILTER (WHERE resend_quota_or_rate_limited),
                      count(*) FILTER (WHERE delivery_status IN ('bounced','blocked','complained')),
                      count(*) FILTER (WHERE status='accepted' AND delivery_status='pending'
                        AND created_at < now() - interval '6 hours')
                    FROM public.auth_email_deliveries
                    WHERE created_at >= now() - interval '24 hours'
                """)
                accepted, failed, resend, brevo, quota, bounced, no_event = cursor.fetchone()
    except psycopg.Error:
        print("Auth email health database check failed", file=sys.stderr)
        return 2

    lines = [
        "## EFDS authentication email health",
        "Rolling 24-hour counts from the EFDS Send Email hook only. Provider acceptance is not inbox delivery.",
        f"Accepted: {accepted} (Resend {resend}, Brevo {brevo})",
        f"Failed or uncertain: {failed}; quota/rate signals: {quota}; delivery failures: {bounced}",
        f"No delivery callback after 6 hours: {no_event}; records pruned after 45 days: {removed}",
    ]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    for line in lines[1:]:
        print(line)
    if resend >= 80:
        print("::warning::Resend hook volume is nearing the published free daily allowance; check the provider dashboard.")
    if no_event:
        print("::warning::Accepted messages have no delivery callback after six hours; check webhook configuration and provider records.")
    if failed or quota or bounced:
        print("::error::Authentication email failures need admin review in /admin/integrations and provider dashboards.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
