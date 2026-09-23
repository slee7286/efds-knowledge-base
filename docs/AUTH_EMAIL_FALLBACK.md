# Authentication email fallback

## Deployment state

`send-auth-email` is deployed in project `immldithmugfrpojetmm`. Deployment does **not** enable the Auth Hook. Keep existing Resend SMTP working until configuration and real-account tests are complete.

The initial deployed health check confirmed `BREVO_API_KEY` exists. It reported missing `RESEND_API_KEY` and `SEND_EMAIL_HOOK_SECRET`. No real email has been sent by this implementation during preparation.

Sender: `EFDS <no-reply@imperial-efds.com>`. This address/domain must be authorized in both providers. Disable provider click/link tracking for authentication email.

## Activation (project administrator)

1. In Supabase → Edge Functions → Secrets, add `RESEND_API_KEY` using the sending API key for the existing EFDS Resend domain. SMTP configuration does not automatically expose its key to Edge Functions. Do not paste credentials into chat or commit them.
2. Generate a webhook signing secret locally in Bash using `openssl rand -base64 32`. Prefix the result with `v1,whsec_`. Store the full value as `SEND_EMAIL_HOOK_SECRET` in Edge Function secrets. This value is independent of either email-provider API key.
3. Verify `https://immldithmugfrpojetmm.supabase.co/functions/v1/send-auth-email` returns `configured: true` and an empty `missing` list. This checks presence, **not credential validity or delivery**.
4. Prepare Authentication → Hooks → Send Email as an HTTP hook with URL `https://immldithmugfrpojetmm.supabase.co/functions/v1/send-auth-email` and the same signing secret. Keep Email authentication enabled. Enable the hook only when ready to test immediately. The hook replaces SMTP sending; Supabase does not independently retry SMTP after hook failures.
5. Use a controlled real account to test signup/setup, magic link, and password reset from the EFDS site, opening links in the initiating browser. Confirm inbox delivery, correct destination, sender, and provider acceptance in logs. Also test secure email change if enabled. Existing browser sessions are unaffected by switching email transport.
6. Validate Brevo with a controlled fallback test before declaring it operational. Local tests simulate Resend quota exhaustion; this has not yet verified Brevo's live sender authorization or inbox delivery. Do not exhaust production quotas or invalidate the production key to test.
7. If any live flow fails, disable the Send Email hook immediately. Supabase will resume the preserved Resend SMTP configuration. Keep credentials in place for diagnosis.

There is no management API/CLI credential available to the current agent session to set secrets or configure Auth Hooks. Those dashboard steps require the project administrator. The agent's Supabase connector can deploy functions and query the database.

## Behavior and boundaries

The function verifies Standard Webhooks signatures and timestamp tolerance before any database or provider access. JWT verification is intentionally disabled because Supabase Auth authenticates this hook with its signature, not a user JWT. Public GET reports configuration presence only; POST cannot send without the signing secret.

All normal action emails (signup, invitation, magic link, recovery, email change, reauthentication) are handled, together with standard security-change notifications. Links use the fixed EFDS origin and the existing PKCE callback/recovery routes. Secure email change follows Supabase's documented old/new hash mapping.

Resend is primary. An explicit HTTP rejection (including quota/rate-limit rejection) invokes Brevo. Timeouts, 5xx, ambiguous responses, and idempotency conflicts **do not** switch providers, because the primary may already have accepted the email. No retry loop sleeps inside the hook: Supabase HTTP hooks have a five-second execution budget. Each provider attempt is bounded to 1.3 seconds and the work has a 4.2-second budget. Conservative timeouts can return an error even when a provider later delivers; inspect provider records before requesting another link.

The service-only `auth_email_deliveries` table claims a hash of recipient/action/token identity before sending. It contains no email addresses, bodies, links or raw tokens. Concurrent/repeated calls cannot resend an accepted message. An uncertain, failed or interrupted attempt blocks replay of that identity; the user must request a fresh link after checking delivery. A crash between provider acceptance and ledger completion is deliberately treated as uncertain. Provider acceptance does not prove inbox delivery.

Structured logs contain provider and outcome only. Query delivery counts in the SQL Editor:

```sql
select provider, status, count(*)
from public.auth_email_deliveries
where created_at > now() - interval '24 hours'
group by provider, status;
```

RLS with no user policies is deliberate: only the service role and database administrators access this table. The existing Supabase security-advisor findings elsewhere in the project were not changed by this migration.

Still planned separately: provider quota alerts, delivery/bounce webhooks, dashboard monitoring, bounded recovery of failed attempts, and automated retention cleanup. Brevo may queue accepted mail after its own daily allowance is exhausted; this implementation does not yet detect that queue. It must not be described as unlimited or guaranteed immediate overflow capacity.

## Local checks

From `/home/siheon/projects/efds-knowledge-base`:

```bash
npx --yes deno check --config supabase/functions/send-auth-email/deno.json supabase/functions/send-auth-email/index.ts
npx --yes deno test --allow-env --config supabase/functions/send-auth-email/deno.json supabase/functions/send-auth-email/core_test.ts
.venv/bin/python -m pytest -q
curl -fsS https://immldithmugfrpojetmm.supabase.co/functions/v1/send-auth-email
```

The Deno lockfile pins the webhook verifier and transitive packages. The tests use synthetic credentials and mocked provider requests; they send no emails.

References: [Supabase Send Email Hook](https://supabase.com/docs/guides/auth/auth-hooks/send-email-hook), [hook execution limits](https://supabase.com/docs/guides/auth/auth-hooks), [Brevo transactional API](https://developers.brevo.com/reference/send-transac-email), [Resend errors](https://resend.com/docs/api-reference/errors).

## Bash setup helper

A project owner/admin with a [Supabase personal access token](https://supabase.com/dashboard/account/tokens) can run the following from this repository. Python 3 is the only local dependency. The script prompts invisibly, never writes credentials to disk, generates the shared signing secret, and preserves SMTP settings.

```bash
python3 scripts/configure_auth_email_fallback.py --configure
python3 scripts/configure_auth_email_fallback.py --status
# Enable when ready to test sign-in/reset immediately:
python3 scripts/configure_auth_email_fallback.py --activate
# Roll back if needed:
python3 scripts/configure_auth_email_fallback.py --disable
```

Configuration leaves the hook disabled; activation is a separate command. The helper refuses to overwrite another hook or rotate the signing secret of an enabled hook. Status verifies configuration only, not inbox delivery. A 403 requires an owner/admin to grant the necessary Auth configuration and Edge Function secret permissions; do not bypass it.
