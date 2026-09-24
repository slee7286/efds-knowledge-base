# Authentication email fallback

## Deployment state

`send-auth-email` version 5 is deployed in project `immldithmugfrpojetmm`, and its Auth Hook is active. A controlled password-reset email reached the account owner on 24 September 2026; its fresh link opened the correct **Set your password** page. Resend showed **Delivered** for two other signup recipients who reported no inbox arrival. One fresh setup email was requested for each on 24 September at 06:46 UTC; Supabase recorded new confirmation send times and the hook recorded two Resend acceptances. Their inbox outcomes and completed confirmations remain unverified. Brevo fallback and provider delivery callbacks still need live verification. The preserved Resend SMTP configuration is the rollback path if the hook fails.

Sender: `EFDS <no-reply@imperial-efds.com>`. This address/domain must be authorized in both providers. Disable provider click/link tracking for authentication email.

## Activation (project administrator)

1. In Supabase → Edge Functions → Secrets, add `RESEND_API_KEY` using the sending API key for the existing EFDS Resend domain. SMTP configuration does not automatically expose its key to Edge Functions. Do not paste credentials into chat or commit them.
2. Generate a webhook signing secret locally in Bash using `openssl rand -base64 32`. Prefix the result with `v1,whsec_`. Store the full value as `SEND_EMAIL_HOOK_SECRET` in Edge Function secrets. This value is independent of either email-provider API key.
3. Verify `https://immldithmugfrpojetmm.supabase.co/functions/v1/send-auth-email` returns `configured: true` and an empty `missing` list. This checks presence, **not credential validity or delivery**.
4. Prepare Authentication → Hooks → Send Email as an HTTP hook with URL `https://immldithmugfrpojetmm.supabase.co/functions/v1/send-auth-email` and the same signing secret. Keep Email authentication enabled. Enable the hook only when ready to test immediately. The hook replaces SMTP sending; Supabase does not independently retry SMTP after hook failures.
5. Use a controlled real account to test signup/setup, magic link, and password reset from the EFDS site, opening links in the initiating browser. Confirm inbox delivery, correct destination, sender, and provider acceptance in logs. The controlled password-reset page passed on 24 September; signup and magic-link completion still need real-user checks. Also test secure email change if enabled. Existing browser sessions are unaffected by switching email transport.
6. Validate Brevo with a controlled fallback test before declaring it operational. Local tests simulate Resend quota exhaustion; this has not yet verified Brevo's live sender authorization or inbox delivery. Do not exhaust production quotas or invalidate the production key to test.
7. If any live flow fails, disable the Send Email hook immediately. Supabase will resume the preserved Resend SMTP configuration. Keep credentials in place for diagnosis.

There is no management API/CLI credential available to the current agent session to set secrets or configure Auth Hooks. Those dashboard steps require the project administrator. The agent's Supabase connector can deploy functions and query the database.

## Behavior and boundaries

The function verifies Standard Webhooks signatures and timestamp tolerance before any database or provider access. JWT verification is intentionally disabled because Supabase Auth authenticates this hook with its signature, not a user JWT. Public GET reports configuration presence only; POST cannot send without the signing secret.

All normal action emails (signup, invitation, magic link, recovery, email change, reauthentication) are handled, together with standard security-change notifications. Signup, invitation, magic-link, recovery and email-change emails open the site's scanner-resistant `/auth/confirm` page before a human POST verifies the token. Secure email change preserves Supabase's documented old/new hash mapping. The first successful confirmation may return no session; the site then explains that the other address may need confirmation, while the final confirmation opens the workspace. This behavior passed local tests; a live email-change test with a controlled pair of addresses is still needed.

Resend is primary. An explicit HTTP rejection (including quota/rate-limit rejection) invokes Brevo. Timeouts, 5xx, ambiguous responses, and idempotency conflicts **do not** switch providers, because the primary may already have accepted the email. No retry loop sleeps inside the hook: Supabase HTTP hooks have a five-second execution budget. Each provider attempt is bounded to 1.3 seconds and the work has a 4.2-second budget. Conservative timeouts can return an error even when a provider later delivers; inspect provider records before requesting another link.

The service-only `auth_email_deliveries` table claims a hash of recipient/action/token identity before sending. It contains no email addresses, bodies, links or raw tokens. Concurrent/repeated calls cannot resend an accepted message. An uncertain, failed or interrupted attempt blocks replay of that identity; the user must request a fresh link after checking delivery. A crash between provider acceptance and ledger completion is deliberately treated as uncertain. Provider acceptance does not prove inbox delivery.

## Missing confirmation email after Resend reports Delivered

On 24 September, the Resend sending dashboard showed **Delivered** for two confirmation-email recipients, but both recipients reported no email. This does not establish inbox placement: [Microsoft says a delivered message may be in Junk or quarantine](https://learn.microsoft.com/en-us/exchange/monitoring/trace-an-email-message/message-trace-faq). The sender is `no-reply@imperial-efds.com` and the subject is `Confirm your EFDS account`.

Do not use an email-open pixel as a substitute for confirmation. [Resend open tracking uses a remote image](https://resend.com/blog/open-and-click-tracking), which [Apple Mail may load before a person reads the message](https://www.apple.com/legal/privacy/data/en/mail-privacy-protection/). [Supabase recommends disabling email tracking for authentication templates](https://supabase.com/docs/guides/auth/auth-email-templates) because link rewriting can break verification. Use provider delivery events for transport state and Supabase confirmation/sign-in timestamps for completed user action. Provider event webhooks still need configuring.

1. Each recipient should search **all Outlook folders** for the sender or subject, including Junk and Deleted Items, then check their [Microsoft quarantine page](https://security.microsoft.com/quarantine). A recipient may need to request release under Imperial's policy.
2. If absent, ask Imperial ICT or an Exchange administrator to run [message trace](https://learn.microsoft.com/en-us/exchange/monitoring/trace-an-email-message/trace-an-email-message) for the exact recipient and sending time. The Resend dashboard record can supply its provider message ID and SMTP delivery details. Keep recipient addresses and message IDs out of public issue trackers.
3. Once found or released, use the **newest** confirmation email. Request another confirmation only after the trace is resolved, because a newer link may supersede an older one. If the link then fails, capture only the on-screen error, never the link or password.

The EFDS site does not require admin approval for basic membership. After successful email confirmation, account provisioning grants `member` and dashboard access automatically. Admin review is only for promotion to `efds_member`, `committee` or `admin`.

Structured logs contain provider and outcome only. Query delivery counts in the SQL Editor:

```sql
select provider, status, count(*)
from public.auth_email_deliveries
where created_at > now() - interval '24 hours'
group by provider, status;
```

RLS with no user policies is deliberate: only the service role and database administrators access this table. The existing Supabase security-advisor findings elsewhere in the project were not changed by this migration.

Version 5 records provider message IDs and a separate delivery state without retaining recipients or tokens. A timed-out or 5xx Resend attempt gets one same-provider retry with the *same* idempotency key; it never switches to Brevo after an ambiguous result. Resend documents a 24-hour idempotency window. Brevo receives no automatic retry because its API does not provide the same deduplication guarantee for this hook. Do not describe Brevo as unlimited or guaranteed immediate overflow capacity: it may queue accepted mail after its daily allowance is exhausted.

The admin-only `/admin/integrations` page reports EFDS hook handoffs, failures, quota/rate rejections and signed delivery/bounce callbacks. It distinguishes provider acceptance from delivery. A separate GitHub Actions job runs at 04:43 and 16:43 UTC, writes aggregate results to its run summary, raises a failing check on a recorded failure/bounce/quota rejection, warns about older missing callbacks, and removes ledger rows after 45 days. GitHub notification delivery depends on maintainers' notification settings. Counts exclude emails sent outside this hook, so provider dashboards remain the source for actual quota usage.

## Activate provider delivery callbacks

The `auth-email-events` function is deployed with JWT verification disabled because it performs its own provider authentication. It will return 503 until the two webhook secrets are configured. The current agent session cannot read or write the provider accounts or Supabase Edge Function secrets, so this step requires an EFDS project administrator. Never send the secrets in chat or commit them.

1. In [Resend Webhooks](https://resend.com/webhooks), create an HTTPS webhook at `https://immldithmugfrpojetmm.supabase.co/functions/v1/auth-email-events/resend` for `email.delivered`, `email.delivery_delayed`, `email.bounced`, `email.complained`, `email.failed`, and `email.suppressed`. Copy its `whsec_` signing secret into a password manager.
2. In [Brevo transactional webhooks](https://developers.brevo.com/docs/how-to-use-webhooks), create a **non-batched** webhook at `https://immldithmugfrpojetmm.supabase.co/functions/v1/auth-email-events/brevo` for Delivered, Deferred, Soft Bounce, Hard Bounce, Blocked, Spam, Invalid Email and Error. Use [bearer-token authentication](https://developers.brevo.com/docs/secured-webhooks). Generate a token locally with `openssl rand -hex 32`, then enter it in Brevo and a password manager. Disable open/click tracking for authentication mail.
3. From `/home/siheon/projects/efds-knowledge-base` in a Linux/WSL terminal, run `python3 scripts/configure_auth_email_events.py`. It prompts invisibly for a Supabase personal access token, the Resend **webhook signing** secret, and the Brevo bearer token. It stores only the latter two as Edge Function secrets and verifies both endpoints reject unsigned requests. It does not send mail.
4. Request a fresh controlled EFDS authentication email. Confirm a provider `delivered` callback appears under `/admin/integrations`, and verify the link opens the expected sign-in or recovery page. To test Brevo, use a controlled provider-side rejection test; do not exhaust the production quota or disable Resend globally. Until this is done, Brevo acceptance and live delivery event handling are unverified.

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
