"""Let committee members read the enabled, public Slack source archive.

Admin policies continue to cover every Slack source row and sync diagnostics.
Committee policies follow the channel allowlist and never grant write access.
"""

from alembic import op

revision: str = "0015_committee_slack_archive"
down_revision: str = "0014_agent_retrieval_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE POLICY slack_channel_sync_settings_select_committee
      ON public.slack_channel_sync_settings FOR SELECT TO authenticated
      USING (enabled AND (SELECT public.has_efds_role('committee')));

      CREATE POLICY slack_channels_select_committee
      ON public.slack_channels FOR SELECT TO authenticated
      USING (
        NOT is_private
        AND (SELECT public.has_efds_role('committee'))
        AND EXISTS (
          SELECT 1 FROM public.slack_channel_sync_settings s
          WHERE s.channel_id = slack_channels.id AND s.enabled
        )
      );

      CREATE POLICY slack_messages_select_committee
      ON public.slack_messages FOR SELECT TO authenticated
      USING (
        (SELECT public.has_efds_role('committee'))
        AND EXISTS (
          SELECT 1 FROM public.slack_channels c
          WHERE c.id = slack_messages.channel_id
        )
      );

      CREATE POLICY slack_users_select_committee
      ON public.slack_users FOR SELECT TO authenticated
      USING (
        (SELECT public.has_efds_role('committee'))
        AND EXISTS (
          SELECT 1 FROM public.slack_messages m
          WHERE m.author_user_id = slack_users.id
        )
      );
    """)
    for table in ("slack_reactions", "slack_message_links", "slack_files", "slack_message_changes"):
        op.execute(f"""
          CREATE POLICY {table}_select_committee
          ON public.{table} FOR SELECT TO authenticated
          USING (
            (SELECT public.has_efds_role('committee'))
            AND EXISTS (
              SELECT 1 FROM public.slack_messages m
              WHERE m.id = {table}.message_id
            )
          )
        """)


def downgrade() -> None:
    for table in (
        "slack_message_changes", "slack_files", "slack_message_links",
        "slack_reactions", "slack_users", "slack_messages", "slack_channels",
        "slack_channel_sync_settings",
    ):
        op.execute(f"DROP POLICY {table}_select_committee ON public.{table}")
