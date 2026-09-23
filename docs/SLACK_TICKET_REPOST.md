# Slack action-ticket reminders

The `Slack archive refresh` GitHub Actions workflow runs at 03:17 and 15:17 UTC. After every successful refresh it imports new `#actions-tickets` roots into the committee register and considers up to five unfinished tickets for a channel reminder. A reminder is eligible only when the original or a repost has an X reaction, no current or archived original/repost has a check reaction, the corresponding dashboard ticket is not completed/cancelled, and no reminder has appeared within 21 days. A currently visible message whose reaction was removed no longer counts as X-marked. The archive must have been refreshed within two hours. Original mentions are escaped so a reminder cannot ping people from an old ticket. A failed or uncertain Slack post is never automatically retried; the next run inspects the channel again.

The committee dashboard shows dated Slack updates, reaction actors and official ticket edits. It suggests status changes from Slack evidence for a committee member to confirm. Slack does not provide the exact time a reaction was made through this archive; the log labels the first observation time. Refreshes now preserve that first observation rather than making old reactions appear new on every run. Reactions archived before this fix cannot have their earlier observation time reconstructed.

## Current live block

The live EFDS bot token was checked on 2026-09-23: it has `reactions:read` and message-history access, but **does not have `chat:write`**. The `#actions-tickets` archive was refreshed successfully the same day. A dry run found at least ten eligible historic tickets; the first five were `ACTION-004`, `ACTION-005`, `ACTION-006`, `ACTION-009` and `ACTION-012`. The posting command then confirmed `posting_skipped: missing_chat_write_scope`; it sent nothing. The scheduled workflow will likewise skip posting and emit a warning until the scope is granted.

In [your EFDS Slack app settings](https://api.slack.com/apps), open **OAuth & Permissions → Bot Token Scopes**, add only `chat:write`, and **Reinstall to Workspace**. Slack requires reauthorization when scopes are added. Keep the bot in `#actions-tickets`; `chat:write.public` is unnecessary for a channel the bot has joined. If Slack issues a new Bot User OAuth Token, update the ignored local `.env` and the encrypted GitHub Actions `SLACK_BOT_TOKEN` secret. Never paste a token into chat or commit it.

Linux/WSL verification from this repository:

```bash
cd /home/siheon/projects/efds-knowledge-base
.venv/bin/python -c "from efds.config import get_slack_bot_token; from efds.integrations.slack_api import SlackClient; print('chat:write granted:', SlackClient(get_slack_bot_token()).has_scope('chat:write'))"
.venv/bin/python scripts/repost_open_tickets.py --dry-run --limit 5
```

If the token changed, sign in to the GitHub CLI and store it at a hidden prompt:

```bash
cd /home/siheon/projects/efds-knowledge-base
gh auth login -h github.com
gh secret set SLACK_BOT_TOKEN -R slee7286/efds-knowledge-base
gh secret list -R slee7286/efds-knowledge-base
```

The scheduled workflow will start posting automatically after its token has `chat:write`. For an immediate controlled run, use `.venv/bin/python scripts/repost_open_tickets.py --limit 5`; it posts to the configured `#actions-tickets` channel only. Check that channel and the workflow summary afterward. Do not run manual and scheduled reposts at the same time.
