# EFDS Slack Institutional Memory V1

## Boundary

Slack messages are canonical source records. The backend owns the token, Slack
Web API calls, normalization, checkpoints and PostgreSQL writes. The website
only reads the private archive through Supabase/RLS and never calls Slack.

```text
Slack Web API → Python sync_slack.py → ingestion_runs + canonical Slack tables
                                      ↓
                              admin-only website archive
```

No LLM, summarization, decision extraction, embeddings or RAG is part of this
milestone.

## Token and effective scopes

Set the token only in the backend `.env`:

```text
SLACK_BOT_TOKEN=xoxb-...
```

The implementation uses the intended read-only scopes as follows:

| Scope | API use |
| --- | --- |
| `channels:read` | Discover public channels |
| `channels:history` | Read public channel history |
| `groups:read` | Discover accessible private channels |
| `groups:history` | Read accessible private channel history |
| `users:read` | Preserve Slack user IDs/display names without email |
| `reactions:read` | Preserve individual reaction names and user IDs |
| `files:read` | Read file metadata present on messages |
| `team:read` | Resolve workspace identity with `team.info` |

The client also calls `auth.test`, `conversations.replies` and
`chat.getPermalink`; these are read operations and do not require write scopes
in this design. File binaries are intentionally never downloaded. No
`users:read.email`, DM, group-DM, write, or admin scope is used.

## Discovery and allowlisting

```powershell
python scripts/sync_slack.py --check
python scripts/sync_slack.py --list-channels
```

Newly discovered channels get `enabled = false` in
`slack_channel_sync_settings`. Enable only selected EFDS public/private
committee channels:

```powershell
python scripts/configure_slack_channel.py C0123456789 --enable
```

The sync command refuses a disabled explicit channel and never archives DMs or
group DMs because discovery requests only `public_channel,private_channel`.

## Backfill and incremental sync

```powershell
python scripts/sync_slack.py --all-enabled --dry-run
python scripts/sync_slack.py --all-enabled --full
python scripts/sync_slack.py --all-enabled
```

Full sync pages all history exposed to the bot. Incremental sync uses the
stored newest message timestamp plus a seven-day reconciliation lookback. It
revisits roots with replies and fetches their replies, which catches new
replies and recent edits/reactions. The lookback is bounded and cannot recover
older changes Slack no longer exposes; use `--full` for an intentional
reconciliation.

The sync is idempotent on `(workspace, channel, slack_message_ts)`. Re-running
unchanged messages increments skipped counters, while edits create history
rows. A missing message from a later page is never treated as deleted. Only an
explicit Slack deletion payload creates a deletion tombstone.

## Stored source model

- `slack_workspaces` stores Slack team identity.
- `slack_channels` stores metadata; `slack_channel_sync_settings` owns the
  explicit archival allowlist and checkpoint.
- `slack_users` stores IDs and names only; no email is required.
- `slack_messages` stores current canonical message state, thread timestamp,
  parent UUID, hashes, source timestamps, raw event and permalink.
- `slack_message_changes` preserves created/edited/deleted/restored history.
- `slack_reactions` stores each reaction/user pair.
- `slack_message_links` extracts URLs deterministically without fetching them.
- `slack_files` stores metadata only; private file URLs are not downloaded or
  exposed outside the admin RLS boundary.

Threads remain separate message rows. Root/reply rendering is a query concern,
not a mutation of canonical text.

## Privacy and RLS

All Slack tables are inaccessible to `anon`, members and committee users.
Administrators can read the archive. Backend ingestion uses the direct
PostgreSQL connection and does not need a service-role key in the website.
The token is never included in Next.js environment variables or browser code.

## Troubleshooting

- `SLACK_BOT_TOKEN is missing`: add the token to the backend `.env` only.
- `invalid_auth`: verify the token belongs to the intended Slack app; the
  command does not print it.
- No channels listed: confirm the bot is a member of private committee
  channels and that the app has the intended read scopes.
- A channel appears but is not archived: run the explicit configure command.
- Old history is absent: Slack/API retention and workspace access bound the
  available history; V1 cannot recover unavailable content.

Future milestones may derive decisions/actions from these immutable source
records, but should never overwrite the canonical Slack message fields.

## Google Docs meeting logs

A successful non-dry sync of the enabled `meetings` channel now also imports
its Google Docs links as private, versioned meeting notes. Other channel links
are not fetched. See [Google Docs meeting sync](GOOGLE_DOCS_MEETINGS.md).
