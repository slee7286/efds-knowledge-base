"""Idempotent, allowlisted Slack source ingestion."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from efds.db.models import (
    IngestionRun,
    SlackChannel,
    SlackChannelSyncSetting,
    SlackFile,
    SlackMessage,
    SlackMessageChange,
    SlackMessageLink,
    SlackReaction,
    SlackUser,
    SlackWorkspace,
)

from .slack_api import SlackApiError, SlackClient, SlackIdentity

logger = logging.getLogger(__name__)
SOURCE_TYPE = "slack"
URL_PATTERN = re.compile(r"https?://[^\s<>\[\](){}]+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,;:!?\"'"


@dataclass(slots=True)
class SlackSyncSummary:
    run_id: str | None
    status: str
    workspace_id: str | None = None
    channels_attempted: int = 0
    channels_succeeded: int = 0
    channels_failed: int = 0
    synced_channel_ids: list[str] = field(default_factory=list)
    users_seen: int = 0
    messages_seen: int = 0
    messages_created: int = 0
    messages_updated: int = 0
    messages_skipped: int = 0
    messages_deleted: int = 0
    messages_restored: int = 0
    threads_fetched: int = 0
    reactions_seen: int = 0
    files_seen: int = 0
    links_extracted: int = 0
    rate_limit_retries: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "channels_attempted": self.channels_attempted,
            "channels_succeeded": self.channels_succeeded,
            "channels_failed": self.channels_failed,
            "synced_channel_ids": self.synced_channel_ids,
            "users_seen": self.users_seen,
            "messages_seen": self.messages_seen,
            "messages_created": self.messages_created,
            "messages_updated": self.messages_updated,
            "messages_skipped": self.messages_skipped,
            "messages_deleted": self.messages_deleted,
            "messages_restored": self.messages_restored,
            "threads_fetched": self.threads_fetched,
            "reactions_seen": self.reactions_seen,
            "files_seen": self.files_seen,
            "links_extracted": self.links_extracted,
            "rate_limit_retries": self.rate_limit_retries,
            "errors": self.errors,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slack_ts_datetime(value: str | float | int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def message_content_hash(message: dict[str, Any]) -> str:
    """Hash source content/version fields, excluding volatile reaction/file state."""

    source = {
        key: message.get(key)
        for key in ("text", "subtype", "blocks", "attachments", "thread_ts", "user")
        if key in message
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract_links(text: str | None) -> list[tuple[str, str, str | None]]:
    """Return URL, normalized URL and lowercase domain tuples without fetching them."""

    links: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for match in URL_PATTERN.findall(text or ""):
        url = match.rstrip(TRAILING_URL_PUNCTUATION)
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append((url, normalized, parsed.netloc.lower()))
    return links


def _channel_created_at(channel: dict[str, Any]) -> datetime | None:
    return slack_ts_datetime(channel.get("created"))


class SlackSynchronizer:
    """Coordinates API reads and SQLAlchemy upserts for one Slack workspace."""

    def __init__(self, session: Session, client: SlackClient, identity: SlackIdentity, *, persist: bool = True) -> None:
        self.session = session
        self.client = client
        self.identity = identity
        self.persist = persist
        self.ingestion_run_id: uuid.UUID | None = None
        self.workspace = self._get_or_create_workspace()

    def _get_or_create_workspace(self) -> SlackWorkspace:
        workspace = self.session.scalar(select(SlackWorkspace).where(SlackWorkspace.slack_team_id == self.identity.team_id))
        now = utc_now()
        if workspace is None:
            workspace = SlackWorkspace(id=uuid.uuid4(), slack_team_id=self.identity.team_id, name=self.identity.team_name, domain=self.identity.team_domain, metadata_={})
            if self.persist:
                self.session.add(workspace)
                self.session.flush()
        elif self.persist:
            workspace.name = self.identity.team_name
            workspace.domain = self.identity.team_domain
        if self.persist:
            workspace.metadata_ = {
                **(workspace.metadata_ or {}),
                "bot_user_id": self.identity.bot_user_id,
                "bot_name": self.identity.bot_name,
            }
        if self.persist:
            workspace.last_synced_at = now
        return workspace

    def discover_channels(self, *, persist: bool = True) -> list[SlackChannel]:
        discovered = self.client.list_channels()
        result: list[SlackChannel] = []
        now = utc_now()
        for payload in discovered:
            channel_id = str(payload.get("id") or "").strip()
            if not channel_id:
                continue
            channel = self.session.get(SlackChannel, channel_id)
            if channel is None:
                channel = SlackChannel(id=channel_id, workspace_id=self.workspace.id, name=str(payload.get("name") or channel_id))
                if self.persist:
                    self.session.add(channel)
                    self.session.flush()
                    self.session.add(SlackChannelSyncSetting(channel_id=channel_id))
            elif self.persist and self.session.get(SlackChannelSyncSetting, channel_id) is None:
                self.session.add(SlackChannelSyncSetting(channel_id=channel_id))
            if self.persist:
                channel.workspace_id = self.workspace.id
                channel.name = str(payload.get("name") or channel_id)
                channel.topic = str((payload.get("topic") or {}).get("value") or "") or None
                channel.purpose = str((payload.get("purpose") or {}).get("value") or "") or None
                channel.is_private = bool(payload.get("is_private"))
                channel.archived = bool(payload.get("is_archived"))
                channel.source_created_at = _channel_created_at(payload)
                channel.last_seen_at = now
                channel.metadata_ = {key: value for key, value in payload.items() if key not in {"topic", "purpose"}}
            result.append(channel)
        if persist:
            self.session.flush()
        return result

    def sync_users(self, *, persist: bool = True) -> int:
        count = 0
        now = utc_now()
        for payload in self.client.list_users():
            slack_user_id = str(payload.get("id") or "").strip()
            if not slack_user_id:
                continue
            user = self.session.scalar(select(SlackUser).where(SlackUser.workspace_id == self.workspace.id, SlackUser.slack_user_id == slack_user_id))
            profile = payload.get("profile") or {}
            if user is None:
                user = SlackUser(workspace_id=self.workspace.id, slack_user_id=slack_user_id)
                self.session.add(user)
            user.display_name = str(profile.get("display_name") or payload.get("name") or "") or None
            user.real_name = str(profile.get("real_name") or payload.get("real_name") or "") or None
            user.is_bot = bool(payload.get("is_bot") or payload.get("is_app_user"))
            user.is_deleted = bool(payload.get("deleted"))
            user.last_seen_at = now
            user.metadata_ = {key: value for key, value in payload.items() if key != "profile"}
            count += 1
        if persist:
            self.session.flush()
        return count

    def sync_channel(
        self,
        channel: SlackChannel,
        *,
        full: bool = False,
        since: str | None = None,
        lookback_days: int = 7,
        dry_run: bool = False,
        summary: SlackSyncSummary,
    ) -> None:
        setting = self.session.get(SlackChannelSyncSetting, channel.id)
        if setting is None or not setting.enabled:
            raise ValueError(f"Channel {channel.name} is not enabled for archival")
        oldest = None if full else since
        if oldest is None and not full and setting.newest_message_ts:
            try:
                oldest = str(max(float(setting.newest_message_ts) - lookback_days * 86400, 0))
            except ValueError:
                oldest = None
        messages = self.client.history(channel.id, oldest=oldest)
        fetched: list[dict[str, Any]] = list(messages)
        if setting.include_threads:
            for message in messages:
                if int(message.get("reply_count") or 0) <= 0 or not message.get("ts"):
                    continue
                replies = self.client.replies(channel.id, str(message["ts"]))
                summary.threads_fetched += 1
                fetched.extend(replies)
        deduplicated: dict[str, dict[str, Any]] = {}
        without_timestamp: list[dict[str, Any]] = []
        for payload in fetched:
            timestamp = str(payload.get("ts") or payload.get("deleted_ts") or "").strip()
            if timestamp:
                deduplicated.setdefault(timestamp, payload)
            else:
                without_timestamp.append(payload)
        fetched = [*deduplicated.values(), *without_timestamp]
        if dry_run:
            summary.messages_seen += len(fetched)
            summary.reactions_seen += sum(len(reaction.get("users") or []) for message in fetched for reaction in (message.get("reactions") or []))
            summary.files_seen += sum(len(message.get("files") or []) for message in fetched)
            summary.links_extracted += sum(len(extract_links(str(message.get("text") or ""))) for message in fetched)
            return

        newest: str | None = setting.newest_message_ts
        for payload in fetched:
            summary.messages_seen += 1
            result = self._upsert_message(channel, payload, summary)
            if result is not None:
                newest = max_slack_ts(newest, result.slack_ts)
        for message in self.session.scalars(select(SlackMessage).where(SlackMessage.workspace_id == self.workspace.id, SlackMessage.channel_id == channel.id, SlackMessage.thread_ts.is_not(None), SlackMessage.parent_message_id.is_(None))).all():
            if message.thread_ts == message.slack_ts:
                continue
            parent = self.session.scalar(select(SlackMessage).where(SlackMessage.workspace_id == self.workspace.id, SlackMessage.channel_id == channel.id, SlackMessage.slack_ts == message.thread_ts))
            if parent is not None:
                message.parent_message_id = parent.id
        setting.newest_message_ts = newest
        setting.last_successful_sync_at = utc_now()
        setting.updated_at = utc_now()
        channel.last_synced_at = utc_now()
        self.session.flush()

    def _upsert_message(self, channel: SlackChannel, payload: dict[str, Any], summary: SlackSyncSummary) -> SlackMessage | None:
        now = utc_now()
        if payload.get("subtype") == "message_changed" and isinstance(payload.get("message"), dict):
            message_payload = {**payload["message"], "edited": payload.get("message", {}).get("edited") or payload.get("edited")}
        else:
            message_payload = payload
        slack_ts = str(message_payload.get("ts") or payload.get("deleted_ts") or "").strip()
        if not slack_ts:
            return None
        current = self.session.scalar(select(SlackMessage).where(SlackMessage.channel_id == channel.id, SlackMessage.slack_ts == slack_ts, or_(SlackMessage.workspace_id == self.workspace.id, SlackMessage.workspace_id.is_(None))))
        explicit_deleted = payload.get("subtype") == "message_deleted" or bool(payload.get("deleted_ts"))
        deleted = explicit_deleted or bool(message_payload.get("is_deleted"))
        content_hash = None if deleted else message_content_hash(message_payload)
        text = None if deleted else (str(message_payload.get("text")) if message_payload.get("text") is not None else None)
        user_slack_id = str(message_payload.get("user") or "").strip() or None
        author = self._find_user(user_slack_id)
        thread_ts = str(message_payload.get("thread_ts") or "").strip() or None
        parent = None
        if thread_ts and thread_ts != slack_ts:
            parent = self.session.scalar(select(SlackMessage).where(SlackMessage.workspace_id == self.workspace.id, SlackMessage.channel_id == channel.id, SlackMessage.slack_ts == thread_ts))
        permalink = str(message_payload.get("permalink") or "").strip() or None
        if permalink is None and not deleted:
            try:
                permalink = self.client.permalink(channel.id, slack_ts)
            except SlackApiError:
                logger.debug("Could not obtain permalink for a Slack message", exc_info=True)
        source_edited_at = slack_ts_datetime((message_payload.get("edited") or {}).get("ts") if isinstance(message_payload.get("edited"), dict) else message_payload.get("edited"))
        if current is None:
            current = SlackMessage(workspace_id=self.workspace.id, channel_id=channel.id, slack_ts=slack_ts, first_seen_at=now)
            self.session.add(current)
            self.session.flush()
            summary.messages_created += 1
            self._record_change(current, "created", None, content_hash, None, text, source_edited_at)
        else:
            changed = current.content_hash != content_hash or current.is_deleted != deleted
            if changed:
                change_type = "restored" if current.is_deleted and not deleted else "deleted" if deleted else "edited"
                self._record_change(current, change_type, current.content_hash, content_hash, current.message_text, text, source_edited_at)
                current.last_changed_at = now
                summary.messages_updated += 1
                if change_type == "deleted":
                    summary.messages_deleted += 1
                elif change_type == "restored":
                    summary.messages_restored += 1
            else:
                summary.messages_skipped += 1
        current.workspace_id = self.workspace.id
        current.channel_id = channel.id
        current.user_slack_id = user_slack_id
        current.author_user_id = author.id if author else None
        current.thread_ts = thread_ts
        current.parent_message_id = parent.id if parent else None
        current.message_text = text
        current.subtype = str(message_payload.get("subtype") or "") or None
        current.source_posted_at = slack_ts_datetime(slack_ts)
        current.source_edited_at = source_edited_at
        current.permalink = permalink
        current.content_hash = content_hash
        current.is_deleted = deleted
        current.last_seen_at = now
        current.raw_event = payload
        current.posted_at = current.source_posted_at
        self._sync_reactions(current, message_payload, summary)
        self._sync_links(current, text, summary)
        self._sync_files(current, message_payload, summary)
        return current

    def _find_user(self, slack_user_id: str | None) -> SlackUser | None:
        if not slack_user_id:
            return None
        return self.session.scalar(select(SlackUser).where(SlackUser.workspace_id == self.workspace.id, SlackUser.slack_user_id == slack_user_id))

    def _record_change(self, message: SlackMessage, change_type: str, previous_hash: str | None, new_hash: str | None, previous_text: str | None, new_text: str | None, edited_at: datetime | None) -> None:
        self.session.add(SlackMessageChange(message_id=message.id, change_type=change_type, previous_content_hash=previous_hash, new_content_hash=new_hash, previous_text=previous_text, new_text=new_text, source_edited_at=edited_at, ingestion_run_id=self.ingestion_run_id))

    def _sync_reactions(self, message: SlackMessage, payload: dict[str, Any], summary: SlackSyncSummary) -> None:
        self.session.execute(delete(SlackReaction).where(SlackReaction.message_id == message.id))
        now = utc_now()
        for reaction in payload.get("reactions") or []:
            name = str(reaction.get("name") or "").strip()
            if not name:
                continue
            for slack_user_id in reaction.get("users") or []:
                slack_user_id = str(slack_user_id)
                user = self._find_user(slack_user_id)
                self.session.add(SlackReaction(message_id=message.id, name=name, slack_user_id=slack_user_id, user_id=user.id if user else None, first_seen_at=now, last_seen_at=now))
                summary.reactions_seen += 1

    def _sync_links(self, message: SlackMessage, text: str | None, summary: SlackSyncSummary) -> None:
        self.session.execute(delete(SlackMessageLink).where(SlackMessageLink.message_id == message.id))
        for url, normalized, domain in extract_links(text):
            self.session.add(SlackMessageLink(message_id=message.id, url=url, normalized_url=normalized, domain=domain))
            summary.links_extracted += 1

    def _sync_files(self, message: SlackMessage, payload: dict[str, Any], summary: SlackSyncSummary) -> None:
        setting = self.session.get(SlackChannelSyncSetting, message.channel_id)
        if not setting or not setting.include_file_metadata:
            return
        self.session.execute(delete(SlackFile).where(SlackFile.message_id == message.id))
        for file_payload in payload.get("files") or []:
            file_id = str(file_payload.get("id") or "").strip()
            if not file_id:
                continue
            self.session.merge(SlackFile(slack_file_id=file_id, message_id=message.id, filename=file_payload.get("name"), title=file_payload.get("title"), mime_type=file_payload.get("mimetype"), file_type=file_payload.get("filetype"), size_bytes=file_payload.get("size"), permalink=file_payload.get("permalink"), source_created_at=slack_ts_datetime(file_payload.get("created")), metadata_={key: value for key, value in file_payload.items() if key not in {"url_private", "url_private_download"}}))
            summary.files_seen += 1


def max_slack_ts(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    try:
        return right if float(right) > float(left) else left
    except ValueError:
        return max(left, right)


def sync_slack(
    session: Session,
    client: SlackClient,
    *,
    channel_id: str | None = None,
    all_enabled: bool = False,
    full: bool = False,
    since: str | None = None,
    lookback_days: int = 7,
    dry_run: bool = False,
) -> SlackSyncSummary:
    """Validate, discover and sync only explicitly enabled channels."""

    identity = client.validate()
    synchronizer = SlackSynchronizer(session, client, identity, persist=not dry_run)
    channels = synchronizer.discover_channels(persist=not dry_run)
    summary = SlackSyncSummary(run_id=None, status="running", workspace_id=str(synchronizer.workspace.id))
    if not dry_run:
        run = IngestionRun(source_type=SOURCE_TYPE, source_path=identity.team_id, status="running", metadata_={"team_id": identity.team_id, "team_name": identity.team_name})
        session.add(run)
        session.commit()
        session.refresh(run)
        summary.run_id = str(run.id)
        synchronizer.ingestion_run_id = run.id
    else:
        run = None

    if not all_enabled and not channel_id:
        all_enabled = True
    selected: list[SlackChannel] = []
    for channel in channels:
        setting = session.get(SlackChannelSyncSetting, channel.id)
        if channel_id and channel.id == channel_id:
            selected.append(channel)
        elif all_enabled and setting and setting.enabled:
            selected.append(channel)
    if channel_id and not selected:
        error = {"channel_id": channel_id, "error_type": "ValueError", "message": "Channel was not discovered or is not enabled"}
        summary.errors.append(error)
    summary.channels_attempted = len(selected)
    if not dry_run:
        summary.users_seen = synchronizer.sync_users()

    for channel in selected:
        try:
            transaction = session.begin_nested() if not dry_run else None
            if transaction is not None:
                with transaction:
                    synchronizer.sync_channel(channel, full=full, since=since, lookback_days=lookback_days, dry_run=dry_run, summary=summary)
            else:
                synchronizer.sync_channel(channel, full=full, since=since, lookback_days=lookback_days, dry_run=dry_run, summary=summary)
            summary.channels_succeeded += 1
            summary.synced_channel_ids.append(channel.id)
        except Exception as error:
            summary.channels_failed += 1
            detail = {"channel_id": channel.id, "channel_name": channel.name, "error_type": type(error).__name__, "message": str(error)}
            summary.errors.append(detail)
            logger.exception("Slack channel sync failed for %s", channel.name)

    summary.status = "completed_dry_run" if dry_run else "completed" if not summary.errors else "completed_with_errors"
    summary.rate_limit_retries = client.retry_count
    if run is not None:
        synchronizer.workspace.last_synced_at = utc_now()
        run.records_seen = summary.messages_seen
        run.records_created = summary.messages_created
        run.records_updated = summary.messages_updated
        run.records_skipped = summary.messages_skipped
        run.records_failed = summary.channels_failed
        run.status = summary.status
        run.finished_at = utc_now()
        run.error_log = summary.errors
        run.metadata_ = {**(run.metadata_ or {}), **summary.metadata()}
        session.commit()
    elif not dry_run:
        session.commit()
    return summary
