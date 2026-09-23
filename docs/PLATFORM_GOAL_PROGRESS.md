# EFDS platform goal progress

Updated: 2026-09-23. This is the working handoff for the ten-milestone Goal. Do not infer completion from code or green tests alone; verify live state.

## Current audit

- `efds-knowledge-base`: started clean and synchronized with `origin/main` at `58fd645`. Email fallback function and ledger were already deployed. Function health returns `configured: true`. The user reports the hook is active and received a real email; which provider accepted that email and whether all auth flows work remain unverified.
- `efds-site`: fast-forwarded clean local `main` from `956c41d` to `7e863bd`. These newer commits add `/auth/confirm` scanner protection. The prior fallback function bypassed it; function code has now been corrected and deployed as version 3. Its 11 synthetic tests pass. Test a newly generated real link to verify production behavior.
- `efds-agent`: started clean at `3c30a8f`. The role-to-scope mapping now treats a basic member as public and `efds_member` as member. The full 36-test suite passes when local sockets are permitted; publication and deployed-service verification remain.
- `efds-recruiting`: active `codex/application-generation` is 31 commits ahead of `origin/main`, with four modified files and two untracked files. Treat these as active work; do not reset or blindly merge. Classify changes and reconcile safely later.
- Outlook scope: the user has limited ingestion to two exact sender addresses in their personal mailbox. These addresses and the personal mailbox identifier are intentionally omitted from this public repository. No Outlook connector was exposed to this session when checked. Do not scan other mail. A suitable delegated connection is required for live ingestion.

## Milestone status and next evidence

| Milestone | State | Evidence / next action |
| --- | --- | --- |
| 1 Auth email fallback | Partial | User activated the hook and received a real email. Delivery ledger shows one Resend acceptance. Version 3 scanner-safe links deployed; verify a newly generated real link and a controlled Brevo failover. |
| 2 Email reliability | Partial | Basic deduplication and conservative failover exist. Add quota tracking, bounce/delivery events, admin health, alerts, retention and safe retry. |
| 3 Google login | Partial | Site code has optional provider. Verify project provider config and live account linking/session flows. |
| 4 Membership permissions | Implemented; live E2E pending | Live migration `c79bf18165ae` adds `efds_member`; basic profiles remain pending and only public knowledge is visible. The site and agent role mapping are updated. A rolled-back live SQL test verified that a basic member cannot obtain verified or committee access. Publish both apps, then test with two real accounts. |
| 5 Account review | Implemented; live E2E pending | Admin-only `review_efds_account` checks actor, version and role transitions, and writes an audit event. Rolled-back live tests verified a grant, audit entry, stale-version rejection, owner-only ordinary edits and a member's own claim submission. Site panel and membership request form are implemented; publish the site and test as a real admin/member. |
| 6 Slack repost | Partial | Existing read-only archive schedule is live. Bot previously lacked chat:write; recheck. Repost job is not scheduled. |
| 7 Outlook ingestion | Not implemented | Sender scope established privately; live delegated mailbox connection missing. |
| 8 AI draft workflow | Partial | Admin-only suggestion text exists. Need committee-scoped editable drafts and approval. |
| 9 Onboarding/verification | Partial | The committee Markdown and DOCX handoff now describe member → EFDS member → committee → roster link. Local browser tests cover the account review page at desktop, tablet and mobile sizes; real account onboarding remains. |
| 10 Repo/OneDrive | Partial | Repo audit above. OneDrive copy and access not yet verified. |

## Active next action

Publish the membership changes after final checks, verify the deployed site and agent, and then implement email reliability, Slack reposting and reviewed AI drafts while waiting for Outlook delegated access and a safe auth test identity.

## Operating record

- The user authorized publishing validated EFDS work to `main` and `origin`.
- There is no active Goal token budget.
- Do not mark any milestone live verified solely from a configured secret or mocked test.
- No external emails or Slack messages were sent by this Goal so far.
