"""Check delayed ticket reminders and the scheduler without replaying email."""

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
                cursor.execute("""
                    SELECT
                      count(*) FILTER (WHERE state='pending'
                        AND created_at < now()-interval '5 minutes'),
                      count(*) FILTER (WHERE state='sending'
                        AND attempted_at < now()-interval '2 minutes'),
                      count(*) FILTER (WHERE state IN ('rejected','uncertain')),
                      count(*) FILTER (WHERE state='accepted'
                        AND finished_at >= now()-interval '24 hours')
                    FROM public.ticket_reminder_emails
                """)
                stale, stuck, failed, accepted = cursor.fetchone()
                cursor.execute("""
                    SELECT
                      count(*) FILTER (WHERE state='pending'
                        AND due_at < now()-interval '5 minutes'),
                      count(*) FILTER (WHERE state='no_recipients'
                        AND processed_at >= now()-interval '24 hours')
                    FROM public.ticket_reminder_jobs
                """)
                overdue_jobs, no_recipients = cursor.fetchone()
                cursor.execute("""
                    SELECT max(d.start_time)
                    FROM cron.job_run_details d
                    WHERE d.jobid=(SELECT jobid FROM cron.job
                                   WHERE jobname='efds-ticket-reminders')
                      AND d.status='succeeded'
                """)
                (last_cron_success,) = cursor.fetchone()
                cursor.execute("SELECT now()")
                (current_time,) = cursor.fetchone()
                cursor.execute("""
                    DELETE FROM public.ticket_reminder_emails
                    WHERE state IN ('accepted','cancelled')
                      AND created_at < now()-interval '45 days'
                """)
                pruned = cursor.rowcount
    except psycopg.Error:
        print("Ticket reminder health database check failed", file=sys.stderr)
        return 2

    cron_stale = not last_cron_success or (current_time - last_cron_success).total_seconds() > 300
    lines = [
        "## EFDS ticket reminder health",
        f"Accepted in 24 hours: {accepted}; pending over 5 minutes: {stale}; stuck sending: {stuck}; rejected/uncertain: {failed}",
        f"Due jobs overdue 5 minutes: {overdue_jobs}; jobs with no linked recipients in 24 hours: {no_recipients}",
        f"Last successful scheduler run: {last_cron_success or 'none'}; old accepted/cancelled emails pruned: {pruned}",
        "Provider acceptance is not inbox delivery. Investigate uncertain sends before any replay.",
    ]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    for line in lines[1:]:
        print(line)
    if no_recipients:
        print("::warning::Some assigned officer roles had no active linked email account; link accounts and use the manual reminder button.")
    if cron_stale or stale or stuck or failed or overdue_jobs:
        print("::error::Ticket reminder delivery needs investigation; check the outbox, cron job and provider records.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
