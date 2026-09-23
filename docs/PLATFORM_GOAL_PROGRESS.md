# EFDS platform goal progress

Updated: 2026-09-23 16:08 UTC. This is the working handoff for the ten-milestone Goal. Do not infer completion from code or green tests alone; verify live state.

## Current audit

- `efds-knowledge-base`: started clean and synchronized with `origin/main` at `58fd645`. Email fallback function and ledger were already deployed. Function health returns `configured: true`. The user reports the hook is active and received a real email; which provider accepted that email and whether all auth flows work remain unverified.
- `efds-site`: fast-forwarded clean local `main` from `956c41d` to `7e863bd`. These newer commits add `/auth/confirm` scanner protection. The prior fallback function bypassed it; function code has now been corrected and deployed as version 3. Its 11 synthetic tests pass. Test a newly generated real link to verify production behavior.
- `efds-agent`: clean, synchronized main `3c30a8f`.
- `efds-recruiting`: active `codex/application-generation` is 31 commits ahead of `origin/main`, with four modified files and two untracked files. Treat these as active work; do not reset or blindly merge. Classify changes and reconcile safely later.
- Outlook scope: the user has limited ingestion to two exact sender addresses in their personal mailbox. These addresses and the personal mailbox identifier are intentionally omitted from this public repository. No Outlook connector was exposed to this session when checked. Do not scan other mail. A suitable delegated connection is required for live ingestion.

## Milestone status and next evidence

| Milestone | State | Evidence / next action |
| --- | --- | --- |
| 1 Auth email fallback | Partial | Function configured and user reports receiving email. Version 3 scanner-safe links deployed. Verify a fresh live link, Auth Hook state, actual provider acceptance and rollback. |
| 2 Email reliability | Partial | Basic deduplication and conservative failover exist. Add quota tracking, bounce/delivery events, admin health, alerts, retention and safe retry. |
| 3 Google login | Partial | Site code has optional provider. Verify project provider config and live account linking/session flows. |
| 4 Membership permissions | Not implemented | Add member/efds-member role model, RLS and resource gates without downgrading existing privileged roles. |
| 5 Account review | Not implemented | Add review state, secure mutation, audit log and admin UI. |
| 6 Slack repost | Partial | Existing read-only archive schedule is live. Bot previously lacked chat:write; recheck. Repost job is not scheduled. |
| 7 Outlook ingestion | Not implemented | Sender scope established privately; live delegated mailbox connection missing. |
| 8 AI draft workflow | Partial | Admin-only suggestion text exists. Need committee-scoped editable drafts and approval. |
| 9 Onboarding/verification | Partial | Existing DOCX and local browser tests; update for new account flow and verify roles live. |
| 10 Repo/OneDrive | Partial | Repo audit above. OneDrive copy and access not yet verified. |

## Active next action

Verify version 3 function health and run a controlled real-account auth email test. Then implement membership and account-review work while provider and Outlook verification are pending.

## Operating record

- The user authorized publishing validated EFDS work to `main` and `origin`.
- There is no active Goal token budget.
- Do not mark any milestone live verified solely from a configured secret or mocked test.
- No external emails or Slack messages were sent by this Goal so far.
