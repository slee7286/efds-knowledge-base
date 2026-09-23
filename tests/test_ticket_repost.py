"""Ticket reposting must not revive completed work or flood a channel."""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from efds.db.models import SlackChannel, SlackChannelSyncSetting, SlackMessage, SlackReaction
from efds.integrations.slack_api import SlackClient
from efds.integrations.ticket_repost import plan_reposts, repost_text, execute_reposts


def fixture_session(now=300, closed_codes=(), archived_messages=(), extra_reactions=()):
    session = Mock()
    session.get.side_effect = [
        SlackChannel(id="C1", name="actions-tickets"),
        SlackChannelSyncSetting(channel_id="C1", enabled=True, last_successful_sync_at=datetime.fromtimestamp(now - 60, timezone.utc)),
    ]
    source = [
        SlackMessage(id=uuid.uuid4(), channel_id="C1", slack_ts=f"100.{n:06d}",
                     message_text=f"ACTION-{n:03d} — Task {n}\nDue: tomorrow", permalink=f"https://slack.test/{n}", is_deleted=False)
        for n in (1, 2, 3)
    ]
    reactions = [
        SlackReaction(message_id=source[0].id, name="x", slack_user_id="U1"),
        SlackReaction(message_id=source[1].id, name="white_check_mark", slack_user_id="U1"),
        SlackReaction(message_id=source[2].id, name="x", slack_user_id="U1"),
    ]
    closed = [Mock(metadata_={"origin": "slack_actions_tickets", "slack_ticket_code": code}) for code in closed_codes]
    session.scalars.side_effect = [Mock(all=Mock(return_value=[*source, *archived_messages])), Mock(all=Mock(return_value=closed)), Mock(all=Mock(return_value=[*reactions, *extra_reactions]))]
    return session


def test_only_open_tickets_are_eligible_and_repost_is_idempotent():
    client = Mock()
    client.history.return_value = [
        {"ts": "100.000001", "text": "ACTION-001 — Task 1", "reactions": [{"name": "x"}]},
        {"ts": "100.000002", "text": "ACTION-002 — Task 2", "reactions": [{"name": "white_check_mark"}]},
        {"ts": "100.000003", "text": "ACTION-003 — Task 3", "reactions": [{"name": "x"}]},
        {"ts": "250.000001", "text": "[EFDS archive repost: ACTION-003]\n:x: Still open", "reactions": []},
    ]
    candidates = plan_reposts(fixture_session(), client, "C1", now=300)
    assert [candidate.ticket_id for candidate in candidates] == ["ACTION-001"]
    assert "ACTION-001 — Task 1" in repost_text(candidates[0])
    assert "https://slack.test/1" in repost_text(candidates[0])
    assert execute_reposts(client, "C1", candidates, sleep=lambda _: None) == [("ACTION-001", client.post_message.return_value)]
    client.post_message.assert_called_once()


def test_check_on_repost_overrides_archived_x():
    client = Mock()
    client.history.return_value = [
        {"ts": "250.000001", "text": "[EFDS archive repost: ACTION-001]", "reactions": [{"name": "white_check_mark"}]},
    ]
    assert [ticket.ticket_id for ticket in plan_reposts(fixture_session(now=2_000_000), client, "C1", now=2_000_000)] == ["ACTION-003"]


def test_check_on_expired_archived_repost_prevents_resurrection():
    archived = SlackMessage(id=uuid.uuid4(), channel_id="C1", slack_ts="150.000001", message_text="[EFDS archive repost: ACTION-001]", is_deleted=False)
    check = SlackReaction(message_id=archived.id, name="white_check_mark", slack_user_id="U2")
    client = Mock()
    client.history.return_value = []
    candidates = plan_reposts(fixture_session(archived_messages=(archived,), extra_reactions=(check,)), client, "C1", now=300)
    assert "ACTION-001" not in [candidate.ticket_id for candidate in candidates]


def test_removed_x_is_not_treated_as_unfinished_just_because_a_repost_exists():
    archived = SlackMessage(id=uuid.uuid4(), channel_id="C1", slack_ts="150.000001", message_text="[EFDS archive repost: ACTION-001]", is_deleted=False)
    client = Mock()
    client.history.return_value = [
        {"ts": "100.000001", "text": "ACTION-001 — Task 1", "reactions": []},
        {"ts": "150.000001", "text": "[EFDS archive repost: ACTION-001]", "reactions": []},
    ]
    candidates = plan_reposts(fixture_session(archived_messages=(archived,)), client, "C1", now=300)
    assert "ACTION-001" not in [candidate.ticket_id for candidate in candidates]


def test_dashboard_completion_and_stale_sync_prevent_reposts():
    client = Mock()
    client.history.return_value = [{"ts": "100.000001", "text": "ACTION-001 — Task 1", "reactions": [{"name": "x"}]}]
    assert not plan_reposts(fixture_session(closed_codes=("ACTION-001", "ACTION-003")), client, "C1", now=300)
    stale = fixture_session(now=300)
    try:
        plan_reposts(stale, client, "C1", now=300 + 3 * 60 * 60)
    except ValueError as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("stale archive should stop reposting")


def test_repost_escapes_old_mentions_without_losing_source_reference():
    from efds.integrations.ticket_repost import TicketCandidate

    ticket = TicketCandidate("ACTION-001", "100.000001", "ACTION-001 <@U123> @here <!channel> & task", "https://slack.test/1", None)
    text = repost_text(ticket)
    assert "<@U123>" not in text and "<!channel>" not in text
    assert "@here" not in text and "<https://slack.test/1|view source>" in text


def test_slack_post_uses_post_and_does_not_retry_uncertain_result():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({"ok": True, "ts": "300.1"}).encode()
    with patch("efds.integrations.slack_api.urlopen", return_value=response) as opener:
        assert SlackClient("xoxb-test").post_message("C1", "Ticket body") == "300.1"
        request = opener.call_args.args[0]
        assert request.get_method() == "POST"
        assert json.loads(request.data)["text"] == "Ticket body"


def test_cli_does_not_post_without_chat_write(monkeypatch, capsys):
    from contextlib import contextmanager

    from scripts import repost_open_tickets

    @contextmanager
    def session():
        yield Mock()

    client = Mock()
    client.has_scope.return_value = False
    monkeypatch.setattr(repost_open_tickets, "get_slack_bot_token", lambda: "xoxb-test")
    monkeypatch.setattr(repost_open_tickets, "SlackClient", lambda _token: client)
    monkeypatch.setattr(repost_open_tickets, "session_scope", session)
    monkeypatch.setattr(repost_open_tickets, "plan_reposts", lambda *_args: [Mock(ticket_id="ACTION-001")])
    monkeypatch.setattr(repost_open_tickets, "execute_reposts", lambda *_args: (_ for _ in ()).throw(AssertionError("posted")))
    assert repost_open_tickets.main([]) == 0
    assert "missing_chat_write_scope" in capsys.readouterr().out
