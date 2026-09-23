# Hosted Slack refresh

`.github/workflows/slack-sync.yml` refreshes every enabled Slack channel at **03:17 and 15:17 UTC** daily, then ingests Google Docs linked in the meetings channel. It runs on GitHub-hosted infrastructure, independently of any committee member's laptop. Manual runs are available in GitHub Actions → Slack archive refresh → Run workflow.

The repository needs encrypted Actions secrets `DATABASE_URL` and `SLACK_BOT_TOKEN`. These are backend credentials, never website environment variables or committed files. Configure them with `gh secret set NAME --repo slee7286/efds-knowledge-base` and paste each value at the hidden prompt. Only trusted maintainers should have repository write access.

The job has read-only repository permissions, pinned actions, a 90-minute timeout, serial execution, and up to three attempts. The existing idempotent synchronizer resumes from each channel's checkpoint and reconciles a seven-day lookback. It never posts tickets or messages to Slack. Meeting documents must remain accessible to the existing Google Docs importer.

The committee/admin dashboard shows a collapsed recap of the past 24 hours and warns when any visible enabled channel has not refreshed for 15 hours. Supabase's ingestion run history and channel checkpoints are the source of truth; a green workflow also requires the linked-meeting import to succeed. Enable GitHub Actions failure notifications for maintainers in their GitHub notification preferences.

GitHub cron is best effort: runs can be delayed or dropped during high load, and scheduled workflows in public repositories are disabled after 60 days without repository activity. Review Actions regularly. If exact execution times or unattended operation beyond this restriction are required, move the same command and credentials to a managed scheduled worker with retries and failure alerts. See [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Verify with `gh run list --workflow slack-sync.yml --repo slee7286/efds-knowledge-base --limit 5`, then inspect a run using `gh run view RUN_ID --repo slee7286/efds-knowledge-base`. Check that all enabled channels succeeded, rather than only that some messages were imported.
