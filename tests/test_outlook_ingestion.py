"""The Outlook boundary never persists mail from outside the two senders."""

from datetime import datetime, timezone

import httpx
import pytest

from efds.integrations.outlook import parse_graph_message
from efds.integrations.outlook_graph import OutlookGraphClient, OutlookGraphError


def graph_message(address="one@example.org", **values):
    item = {
        "id": "immutable-message-id",
        "from": {"emailAddress": {"address": address}},
        "subject": "Confirm the booking",
        "receivedDateTime": "2026-09-23T12:00:00Z",
        "lastModifiedDateTime": "2026-09-23T12:30:00Z",
        "body": {"contentType": "text", "content": "Please confirm the booking."},
        "webLink": "https://outlook.office.com/mail/id/example",
    }
    item.update(values)
    return item


def test_sender_query_is_exact_and_filters_graph_results_again_locally():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "value": [graph_message(), graph_message("other@example.org")],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=next",
            })
        return httpx.Response(200, json={"value": [graph_message(id="second-message")]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OutlookGraphClient("synthetic-token", client=http)
    items = list(client.iter_sender_messages("ONE@example.org", datetime(2026, 9, 1, tzinfo=timezone.utc)))
    assert [item["id"] for item in items] == ["immutable-message-id", "second-message"]
    assert "from/emailAddress/address eq 'one@example.org'" in requests[0].url.params["$filter"]
    assert "receivedDateTime ge 2026-09-01" in requests[0].url.params["$filter"]
    assert requests[0].headers["authorization"] == "Bearer synthetic-token"
    assert "ImmutableId" in requests[0].headers["prefer"]
    assert requests[1].url.host == "graph.microsoft.com"
    http.close()


def test_graph_client_rejects_cross_origin_pagination_and_handles_404_without_mail_content():
    def handler(request: httpx.Request):
        if "/messages/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://attacker.example/messages"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OutlookGraphClient("synthetic-token", client=http)
    assert client.get_message("old-id") is None
    with pytest.raises(OutlookGraphError, match="unexpected pagination URL"):
        list(client.iter_sender_messages("one@example.org", datetime(2026, 9, 1, tzinfo=timezone.utc)))
    http.close()


def test_parser_requires_exact_sender_and_converts_html_without_a_foreign_link():
    item = graph_message(body={"contentType": "html", "content": "<p>Confirm <strong>venue</strong>.</p>"},
                         webLink="https://attacker.example/read")
    parsed = parse_graph_message(item, "one@example.org")
    assert parsed.body_text == "Confirm\nvenue\n."
    assert parsed.web_link is None
    assert parsed.received_at.tzinfo is not None
    with pytest.raises(ValueError, match="outside the sender allowlist"):
        parse_graph_message(item, "two@example.org")


def test_sender_query_escapes_apostrophes_and_never_advances_after_truncation():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={
            "value": [graph_message("o'ne@example.org")],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=more",
        })

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OutlookGraphClient("synthetic-token", client=http)
    with pytest.raises(OutlookGraphError, match="page limit"):
        list(client.iter_sender_messages("o'ne@example.org", datetime(2026, 9, 1, tzinfo=timezone.utc), max_pages=1))
    assert "o''ne@example.org" in calls[0].url.params["$filter"]
    assert len(calls) == 1
    http.close()


def test_graph_client_retries_a_bounded_rate_limit_without_logging_message_content():
    attempts = []
    sleeps = []

    def handler(request: httpx.Request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"value": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OutlookGraphClient("synthetic-token", client=http, sleep=sleeps.append)
    assert list(client.iter_sender_messages("one@example.org", datetime(2026, 9, 1, tzinfo=timezone.utc))) == []
    assert len(attempts) == 2
    assert sleeps == [1]
    http.close()
