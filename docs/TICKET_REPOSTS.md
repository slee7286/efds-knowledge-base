# Reposting open action tickets

The bot can keep incomplete `ACTION-###` tickets visible in `#actions-tickets`.
This is separate from the read-only archive collector. The current Slack bot
installation must be granted the **bot** `chat:write` scope and reinstalled
before posting. Slack's API reported `missing_scope (needed: chat:write:bot)`
with the current token on 23 September 2026, so no tickets were sent then.
Use a refreshed backend-only `SLACK_BOT_TOKEN`; never place it in the website.

Run a full archive sync before the first repost, so the original text and
reaction state are preserved:

```sh
.venv/bin/python scripts/configure_slack_channel.py C0BQPDP5T44 --enable
.venv/bin/python scripts/sync_slack.py --channel C0BQPDP5T44 --full
.venv/bin/python scripts/repost_open_tickets.py --dry-run
.venv/bin/python scripts/repost_open_tickets.py
.venv/bin/python scripts/sync_slack.py --channel C0BQPDP5T44
.venv/bin/python scripts/rebuild_retrieval_index.py --source slack_message
```

The command only reads archived `ACTION-###` originals from the enabled
`#actions-tickets` channel. It checks live reactions immediately before
posting. A check on either the original or a prior repost takes precedence,
so completed tickets are not sent again. An X makes a ticket eligible.
Every repost includes the full original text, a source link, a visible
`EFDS archive repost` marker, and instructions to react with a check when done.
Prior bot reposts are discovered from Slack before sending, so reruns do not
post duplicates. An open ticket becomes eligible again after 21 days, giving
it a fresh message before a possible 30-day retention cutoff. If a post's
network outcome is uncertain, stop and inspect the channel before retrying.

This command is not scheduled. A recurring job requires an always-on runner,
alerting, a durable token, and review of the workspace's actual retention
policy. The source archive survives Slack's display and retention changes;
reposts are for the channel's day-to-day visibility.

Slack API: https://docs.slack.dev/reference/methods/chat.postMessage/
