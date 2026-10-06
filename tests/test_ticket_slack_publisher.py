"""Website-ticket Slack delivery: unit contracts with no live services."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest

from efds.integrations.ticket_slack_publisher import Publisher, format_root, format_event
from efds.integrations.ticket_slack_store import OutboxStore
from efds.integrations.slack_api import SlackApiError, SlackClient

TICKET = "11111111-1111-4111-8111-111111111111"
EVENT = "22222222-2222-4222-8222-222222222222"


def row(action="committee_create", event_id=EVENT, **snapshot):
    return {"event_id": event_id, "ticket_id": TICKET, "action": action,
            "snapshot": {"title": "ACTION-024 — Plan <review> & launch", "execution_status": "open",
                         "assignees": ["Alice <@U123> & team"], "due_at": "2026-11-05T12:00:00Z", **snapshot},
            "channel": "C0BQPDP5T44", "root_ts": None}


class Store:
    def __init__(self, events):
        self.events = events
        self.root = None
        self.root_event_id = None
        self.done = set()
        self.uncertain = set()
        self.retries = set()
        self.attempts = 0

    def claim(self):
        for event in self.events:
            if event["event_id"] not in self.done and event["event_id"] not in self.uncertain and event["event_id"] not in self.retries:
                return {**event, "root_ts": self.root, "root_event_id": self.root_event_id}
        return None

    def delivered(self, event, ts, root_ts):
        self.done.add(event["event_id"])
        self.root = root_ts
        if event["action"] == "committee_create":
            self.root_event_id = event["event_id"]

    def uncertain_delivery(self, event):
        self.uncertain.add(event["event_id"])

    def retry_later(self, event):
        self.retries.add(event["event_id"])


class Slack:
    def __init__(self):
        self.posts = []
        self.updates = []
        self.messages = []
        self.fail = None

    def history(self, channel):
        return list(self.messages)

    def replies(self, channel, root):
        return list(self.messages)

    def post_message(self, channel, text, *, thread_ts=None):
        self.posts.append((channel, text, thread_ts))
        if self.fail:
            raise self.fail
        ts = f"1234.{len(self.posts):06d}"
        self.messages.append({"ts": ts, "text": text})
        return ts

    def update_message(self, channel, ts, text):
        self.updates.append((channel, ts, text))


def test_root_contains_safe_preview_and_deep_link():
    text = format_root(row(), "https://www.imperial-efds.com")
    assert "ACTION-024" in text and "Open" in text
    assert "Plan &lt;review&gt; &amp; launch" in text
    assert "Alice &lt;@U123&gt; &amp; team" in text
    assert "<https://www.imperial-efds.com/dashboard/tickets/" + TICKET + "|Open on website>" in text
    assert "2026-11-05" in text
    assert "[EFDS ticket:" + TICKET + " event:" + EVENT + "]" in text

def test_display_names_never_emit_mass_mentions_or_untrusted_urls():
    event = row(assignees=["@here <!channel>", "@everyone"], title="@channel <unsafe>")
    text = format_root(event, "https://www.imperial-efds.com") + format_event(event)
    assert "@here" not in text and "@channel" not in text and "@everyone" not in text
    assert "<!channel>" not in text
    with pytest.raises(ValueError, match="HTTPS origin"):
        format_root(event, "https://www.imperial-efds.com/redirect")


def test_first_event_posts_once_and_persists_root():
    store, slack = Store([row()]), Slack()
    publisher = Publisher(store, slack, "https://www.imperial-efds.com")
    assert publisher.run_once() == "posted"
    assert publisher.run_once() == "empty"
    assert store.root == "1234.000001"
    assert len(slack.posts) == 1


def test_reconciles_post_accepted_before_database_ack():
    store, slack = Store([row()]), Slack()
    slack.messages.append({"ts": "999.000001", "text": format_root(row(), "https://www.imperial-efds.com")})
    assert Publisher(store, slack, "https://www.imperial-efds.com").run_once() == "reconciled"
    assert store.root == "999.000001"
    assert not slack.posts


def test_ambiguous_post_is_quarantined_not_replayed():
    store, slack = Store([row()]), Slack()
    slack.fail = TimeoutError("timeout")
    assert Publisher(store, slack, "https://www.imperial-efds.com").run_once() == "uncertain"
    assert Publisher(store, slack, "https://www.imperial-efds.com").run_once() == "empty"
    assert len(slack.posts) == 1


def test_definitively_rejected_post_is_deferred_for_retry():
    store, slack = Store([row()]), Slack()
    slack.fail = SlackApiError("rate limited", method="chat.postMessage", error_code="ratelimited")
    assert Publisher(store, slack, "https://www.imperial-efds.com").run_once() == "retry"
    assert EVENT in store.retries


def test_reconcile_only_never_reposts_when_marker_is_absent():
    event = {**row(), "reconcile_only": True}
    store, slack = Store([event]), Slack()
    assert Publisher(store, slack, "https://www.imperial-efds.com").run_once() == "uncertain"
    assert not slack.posts


def test_root_update_preserves_create_marker_not_change_marker():
    create = row()
    change = row("committee_status", "33333333-3333-4333-8333-333333333333", execution_status="completed")
    store, slack = Store([create, change]), Slack()
    publisher = Publisher(store, slack, "https://www.imperial-efds.com")
    publisher.run_once()
    publisher.run_once()
    assert f"event:{EVENT}]" in slack.updates[0][2]
    assert "event:33333333-3333-4333-8333-333333333333]" not in slack.updates[0][2]


def test_change_updates_root_and_posts_thread_in_order():
    create = row()
    update = row("committee_status", "33333333-3333-4333-8333-333333333333", execution_status="completed")
    store, slack = Store([create, update]), Slack()
    pub = Publisher(store, slack, "https://www.imperial-efds.com")
    assert pub.run_once() == "posted"
    assert pub.run_once() == "posted"
    assert len(slack.updates) == 1
    assert slack.posts[1][2] == store.root
    assert "Completed" in slack.updates[0][2]
    assert "Completed" in slack.posts[1][1]
    assert pub.run_once() == "empty"


def test_failed_read_never_posts():
    store, slack = Store([row()]), Slack()
    def unavailable(channel):
        raise RuntimeError("missing_scope")
    slack.history = unavailable
    assert Publisher(store, slack, "https://www.imperial-efds.com").run_once() == "retry"
    assert not slack.posts


def test_migration_is_forward_only_and_gates_old_tickets():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0025_website_ticket_slack_outbox.py"
    spec = importlib.util.spec_from_file_location("ticket_outbox_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0024_committee_ticket_reference"
    sql = module.OUTBOX_SQL
    assert "AFTER INSERT ON public.operational_review_events" in sql
    assert "committee_dashboard" in sql and "committee_create" in sql
    assert "committee_assign" in sql and "committee_status" in sql and "committee_update" in sql
    assert "slack_actions_tickets" not in sql
    assert "ticket_slack_outbox" in sql and "UNIQUE" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL" in sql and "authenticated" in sql and "anon" in sql
    assert "created_at >= (SELECT installed_at" in sql
    assert "EXISTS (SELECT 1 FROM public.ticket_slack_outbox" in sql
    assert "INSERT INTO public.ticket_slack_outbox" in sql
    assert "ON CONFLICT" not in sql  # duplicate event should fail atomically, never overwrite
    assert "INSERT INTO public.ticket_slack_outbox SELECT" not in sql

def test_evidence_suggestion_cannot_publish_as_manual_ticket():
    # create_committee_ticket_from_evidence calls mutate_committee_ticket first,
    # then changes metadata.origin in the same transaction. The outbox must be
    # withdrawn before commit, otherwise an automated suggestion is announced.
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0025_website_ticket_slack_outbox.py"
    spec = importlib.util.spec_from_file_location("ticket_outbox_suggestion_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "committee_agent_suggestion" in module.OUTBOX_SQL
    assert "AFTER UPDATE OF metadata ON public.operational_records" in module.OUTBOX_SQL
    assert "DELETE FROM public.ticket_slack_outbox" in module.OUTBOX_SQL


class Cursor:
    def __init__(self, result=None):
        self.result = result
        self.queries = []

    def execute(self, query, values=None):
        self.queries.append((query, values))

    def fetchone(self):
        return self.result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Connection:
    def __init__(self, result=None):
        self.cur = Cursor(result)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def test_claim_commits_sending_before_post_and_enforces_order():
    conn = Connection((EVENT, TICKET, "committee_create", row()["snapshot"], "C0BQPDP5T44", None, None, False))
    item = OutboxStore(conn).claim()
    assert item["event_id"] == EVENT
    assert conn.commits == 1
    sql = conn.cur.queries[0][0]
    assert "FOR UPDATE OF o SKIP LOCKED" in sql
    assert "earlier.state <> 'posted'" in sql
    assert "earlier.id < o.id" in sql
    assert "state='sending'" in sql
    assert "interval '15 minutes'" in sql


def test_ack_of_create_stores_root_and_event_in_one_transaction():
    conn = Connection()
    OutboxStore(conn).delivered(row(), "123.456", "123.456")
    assert conn.commits == 1
    sql = "\n".join(query for query, _ in conn.cur.queries)
    assert "INSERT INTO public.ticket_slack_roots" in sql
    assert "UPDATE public.ticket_slack_outbox" in sql
    assert "state='posted'" in sql


def test_slack_thread_post_and_root_update_disable_unfurls(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok":true,"ts":"123.456"}'

    def send(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr("efds.integrations.slack_api.urlopen", send)
    slack = SlackClient("test-token")
    assert slack.post_message("C0BQPDP5T44", "hi", thread_ts="123.000") == "123.456"
    slack.update_message("C0BQPDP5T44", "123.000", "new title")
    import json
    payloads = [json.loads(req.data) for req in requests]
    assert payloads[0]["thread_ts"] == "123.000"
    assert payloads[0]["parse"] == "none" and payloads[0]["unfurl_links"] is False
    assert requests[1].full_url.endswith("chat.update")
    assert payloads[1]["ts"] == "123.000"


def test_worker_refuses_to_run_without_credentials(monkeypatch):
    from efds.integrations.ticket_slack_worker import main
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert main([]) == 2


def test_scheduled_publisher_runs_without_freshness_gate_or_legacy_import():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/slack-sync.yml").read_text()
    assert "python scripts/publish_website_tickets.py" in workflow
    assert "scripts/import_slack_tickets.py" not in workflow
    assert "scripts/repost_open_tickets.py" not in workflow
    assert "scripts/sync_slack.py --all-enabled" in workflow
    publisher_step = workflow.split("- name: Publish new website tickets", 1)[1]
    assert "steps.freshness.outputs.refresh" not in publisher_step
