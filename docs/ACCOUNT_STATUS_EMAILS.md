# Account access change emails

Every substantive update to `profiles.access_role`, `efds_verification_status`,
`officer_id` or `active` writes one row to the private
`account_status_notices` outbox in the same transaction. This includes EFDS
EFDS student verification or a non-EFDS-student decision, committee/admin promotion and demotion,
officer roster links, and account activation changes. Ordinary profile edits
do not send an access email. The trigger also covers trusted maintenance writes
outside the admin review RPC; such writes get a new `access_version` if needed.

`send-account-status-email` is deployed in Supabase project
`immldithmugfrpojetmm`. A `pg_cron` job named `efds-account-status-email`
invokes it each minute through `pg_net`. The job retrieves a random worker
token from Supabase Vault; the Edge Function verifies it through a service-only
RPC before claiming at most five pending notices atomically. No provider key is
stored on the website. Resend is primary and Brevo is used only after an
explicit Resend rejection. Ambiguous provider outcomes are never replayed
automatically, to avoid duplicate mail. The affected account holder receives
a plain-language email with their new status and a workspace/help link. The
admin account page reports pending, provider-accepted, and attention-needed
counts. Provider acceptance is not proof of inbox delivery or reading.

## Live verification on 24 September 2026

The Alembic revisions `6e9d1c2a4b70`, `277acbd83911`, and
`972c30a34f67` were applied
to the hosted database. A rollback-only test changed a pending test profile
to `declined` within a subtransaction and verified exactly one outbox row and
version increment; the transaction rolled back, so it sent no email and left
the account unchanged. At 10:29 UTC the first scheduled job succeeded and
`pg_net` recorded HTTP 200 with `{ "claimed": 0, "accepted": 0, "attention": 0 }`.
Local Deno checks and four focused unit tests passed. A real status-change
email has **not** yet been observed in a recipient inbox; verify that on the
next legitimate admin decision.

## Check delivery

Run these read-only queries in the Supabase SQL Editor for the EFDS project:

```sql
select state, provider, count(*)
from public.account_status_notices
group by state, provider
order by state, provider;

select created_at, state, provider, attempted_at, finished_at
from public.account_status_notices
order by created_at desc limit 20;

select d.start_time, d.status, d.return_message
from cron.job_run_details d
where d.jobid = (select jobid from cron.job
                 where jobname = 'efds-account-status-email')
order by d.start_time desc limit 10;

select created, status_code, content
from net._http_response
order by created desc limit 10;
```

The outbox contains recipient addresses and must remain service-only; never
expose it directly to public or member clients. `accepted` means an email
provider returned a message ID. `rejected`, `uncertain`, or `sending` for more
than two minutes require investigation. Check provider records before any
manual replay. Do not send a second copy from Brevo after an ambiguous Resend
attempt. Provider delivery/bounce webhooks and inbox verification are covered
in [AUTH_EMAIL_FALLBACK.md](AUTH_EMAIL_FALLBACK.md).

The GitHub `Authentication email health` workflow also runs twice daily. It
fails when an account notice remains pending for more than five minutes, is
stuck sending for more than two minutes, or has a rejected/uncertain outcome.
It prunes accepted notice rows after 45 days; the separate account-access
audit remains available.

## Local checks

From `/home/siheon/projects/efds-knowledge-base`:

```bash
deno check --config supabase/functions/send-account-status-email/deno.json supabase/functions/send-account-status-email/index.ts
deno test --allow-env --config supabase/functions/send-account-status-email/deno.json supabase/functions/send-account-status-email/core_test.ts
.venv/bin/python -m pytest -q
```

The tests use synthetic addresses and send no email.
