# EFDS platform goal progress

Updated: 2026-09-23. This is the working handoff for the ten-milestone Goal. Do not infer completion from code or green tests alone; verify live state.

## Current audit

- `efds-knowledge-base`: `main` at `f27db55` was pushed. The live Alembic head is `d8144f029e73`. The auth email hook is version 4 and the provider-event endpoint is version 1. The user reports a received real email; the ledger has two Resend acceptances. Brevo fallback and delivery webhooks remain unverified live. The Slack repost scheduler and reaction-date fix passed local validation; their first scheduled production run remains pending.
- `efds-site`: `main` at `8ec3a1c` was pushed and its Vercel deployment check succeeded; a subsequent documentation commit `c9d7905` was pushed. It includes scanner-resistant `/auth/confirm`, account review, membership gating, the onboarding DOCX, an admin email-health panel and an admin AI proposal flow. The latter passed lint, 102 tests, production build and mobile/tablet/desktop browser review; a rolled-back live SQL test verified the proposal/review/publish transition and attached evidence. Test a newly generated real auth link to verify production behavior.
- `efds-agent`: `main` at `ea6a14b` was pushed. The role-to-scope mapping treats a basic member as public and `efds_member` as member. The full 36-test suite passes when local sockets are permitted; deployed-service verification remains.
- `efds-recruiting`: active `codex/application-generation` is 31 commits ahead of `origin/main`, with four modified files and two untracked files. Treat these as active work; do not reset or blindly merge. Classify changes and reconcile safely later.
- Outlook scope: the user has limited ingestion to two exact sender addresses in their personal mailbox. These addresses and the personal mailbox identifier are intentionally omitted from this public repository. No Outlook connector was exposed to this session when checked. Do not scan other mail. A suitable delegated connection is required for live ingestion.

## Milestone status and next evidence

| Milestone | State | Evidence / next action |
| --- | --- | --- |
| 1 Auth email fallback | Partial | User activated the hook and received a real email. Delivery ledger shows two Resend acceptances. Version 4 scanner-safe links deployed; verify a newly generated real link and a controlled Brevo failover. |
| 2 Email reliability | Implemented; provider setup pending | Live migration adds private delivery states and admin-only aggregate health. Version 4 hook records message IDs and makes one Resend idempotent retry. New signed/bearer provider-event function deployed. Admin health panel passed local browser checks; twice-daily GitHub health/retention job is defined but has not run in production. Provider webhook registration and live bounce/delivery tests require dashboard credentials; see `AUTH_EMAIL_FALLBACK.md`. |
| 3 Google login | Blocked on provider credentials | A live public Supabase Auth settings check on 2026-09-23 returned `google: false` and `email: true`. The site integration is present and the project-specific guide is `efds-site/docs/GOOGLE_LOGIN_SETUP.md`. Create the Google OAuth web client, enter its client ID/secret in Supabase and enable the provider; then verify live account linking, callbacks and sessions. |
| 4 Membership permissions | Implemented; live E2E pending | Live migration adds `efds_member`; basic profiles remain pending and only public knowledge is visible. The site and agent role mapping are pushed. A rolled-back live SQL test verified that a basic member cannot obtain verified or committee access. Test with two real accounts. |
| 5 Account review | Implemented; live E2E pending | Admin-only `review_efds_account` checks actor, version and role transitions, and writes an audit event. Rolled-back live tests verified a grant, audit entry, stale-version rejection, owner-only ordinary edits and a member's own claim submission. Site panel and membership request form are published; test as a real admin/member. |
| 6 Slack repost | Code validated; posting blocked by Slack scope | Fresh `#actions-tickets` sync completed. The current bot token lacks `chat:write`; a dry run found at least ten eligible historic tickets and the posting command safely skipped. The twice-daily workflow imports new tickets and plans at most five reminders after each successful sync, excludes completed/cancelled dashboard tickets, checks current and archived reaction status and requires a fresh archive. Reaction refresh now preserves the first-observed date used in the committee activity log. Add the scope and reinstall the Slack app; see `SLACK_TICKET_REPOST.md`. |
| 7 Outlook ingestion | Not implemented | Sender scope established privately; live delegated mailbox connection missing. |
| 8 AI draft workflow | Partial | The admin-only agent suggestion now supports a cited, editable proposal through the existing audited operational review flow. A rolled-back live SQL test verified proposed → approved → committee publication with linked evidence, and the site deployment for commit `8ec3a1c` succeeded. The Goal explicitly also requires committee members to generate suggestions within their source permissions, plus supported workstream/owner fields. Canonical retrieval currently keeps raw Slack and meeting units `internal` and admin-only; do not pass admin-only evidence to a committee draft. Committee-scoped retrieval and review UX still need implementation. Outlook remains unavailable. |
| 9 Onboarding/verification | Partial | The committee Markdown and DOCX handoff now describe member → EFDS member → committee → roster link. Local browser tests cover the account review page at desktop, tablet and mobile sizes; real account onboarding remains. |
| 10 Repo/OneDrive | Partial | Repo audit above. OneDrive copy and access not yet verified. |

## Active next action

Implement committee-scoped, source-permission-safe ticket suggestions and finish Outlook ingestion preparation. Verify the first scheduled Slack and auth-email-health workflow runs when they occur. Provider webhook registration, Google OAuth client credentials and a safe auth test identity remain external dependencies.

## Operating record

- The user authorized publishing validated EFDS work to `main` and `origin`.
- There is no active Goal token budget.
- Do not mark any milestone live verified solely from a configured secret or mocked test.
- No external emails or Slack messages were sent by this Goal so far.
