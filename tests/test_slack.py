import json
from io import BytesIO
from unittest.mock import patch

import pytest

from efds.db.models import (
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
from efds.integrations.slack import extract_links, max_slack_ts, message_content_hash
from efds.integrations.slack_api import SlackApiError, SlackClient


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_slack_client_rejects_missing_token_without_network() -> None:
    with pytest.raises(ValueError):
        SlackClient("")


def test_slack_client_paginates_without_exposing_token() -> None:
    responses = iter([
        FakeResponse({"ok": True, "channels": [{"id": "C1"}], "response_metadata": {"next_cursor": "next"}}),
        FakeResponse({"ok": True, "channels": [{"id": "C2"}], "response_metadata": {"next_cursor": ""}}),
    ])
    with patch("efds.integrations.slack_api.urlopen", side_effect=lambda *_args, **_kwargs: next(responses)):
        client = SlackClient("xoxb-secret", sleep=lambda _seconds: None)
        assert [row["id"] for row in client.list_channels()] == ["C1", "C2"]


def test_slack_api_errors_are_credential_free() -> None:
    with patch("efds.integrations.slack_api.urlopen", return_value=FakeResponse({"ok": False, "error": "invalid_auth"})):
        with pytest.raises(SlackApiError) as error:
            SlackClient("xoxb-secret").call("auth.test")
    assert "xoxb-secret" not in str(error.value)
    assert "invalid_auth" in str(error.value)


def test_slack_identity_validation_uses_auth_and_team_identity() -> None:
    responses = iter([
        FakeResponse({"ok": True, "team_id": "T1", "team": "EFDS", "user_id": "Ubot", "user": "efds-archive"}),
        FakeResponse({"ok": True, "team": {"id": "T1", "name": "EFDS", "domain": "efds"}}),
    ])
    with patch("efds.integrations.slack_api.urlopen", side_effect=lambda *_args, **_kwargs: next(responses)):
        identity = SlackClient("xoxb-test", sleep=lambda _seconds: None).validate()
    assert identity.team_id == "T1"
    assert identity.team_name == "EFDS"
    assert identity.bot_user_id == "Ubot"


def test_message_identity_hash_is_stable_and_content_sensitive() -> None:
    message = {"ts": "1.000", "user": "U1", "text": "hello", "reactions": [{"name": "thumbsup"}]}
    assert message_content_hash(message) == message_content_hash({**message, "reactions": []})
    assert message_content_hash(message) != message_content_hash({**message, "text": "edited"})


def test_links_are_deterministic_and_normalized_without_fetching() -> None:
    links = extract_links("See HTTPS://Example.com/path?x=1 and https://example.com/path?x=1.")
    assert links == [("HTTPS://Example.com/path?x=1", "https://example.com/path?x=1", "example.com")]


def test_checkpoint_comparison_uses_slack_timestamp_semantics() -> None:
    assert max_slack_ts("10.000", "9.000") == "10.000"
    assert max_slack_ts("10.000", "11.000") == "11.000"


def test_slack_schema_contains_private_archive_entities() -> None:
    assert SlackWorkspace.__tablename__ == "slack_workspaces"
    assert SlackUser.__table__.c.slack_user_id is not None
    assert SlackChannel.__table__.c.is_private is not None
    assert SlackChannelSyncSetting.__table__.c.enabled is not None
    assert SlackMessage.__table__.c.parent_message_id is not None
    assert SlackMessage.__table__.c.content_hash is not None
    assert SlackReaction.__table__.c.slack_user_id is not None
    assert SlackMessageLink.__table__.c.normalized_url is not None
    assert SlackFile.__table__.c.slack_file_id is not None
    assert SlackMessageChange.__table__.c.previous_text is not None
