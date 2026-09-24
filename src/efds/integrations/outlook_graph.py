"""Read-only Microsoft Graph access limited to exact sender queries."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
MESSAGE_FIELDS = "id,internetMessageId,receivedDateTime,lastModifiedDateTime,subject,from,body,webLink"
EMAIL_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class OutlookGraphError(RuntimeError):
    """A sanitized Graph failure; never includes tokens, mail content or URLs."""


def normalize_sender(address: str) -> str:
    value = address.strip().casefold()
    if not EMAIL_ADDRESS.fullmatch(value) or len(value) > 254:
        raise ValueError("invalid sender address")
    return value


class OutlookGraphClient:
    """A delegated-token client that cannot send, move or enumerate unfiltered mail."""

    def __init__(self, access_token: str, *, client: httpx.Client | None = None, sleep=time.sleep) -> None:
        if not access_token:
            raise ValueError("delegated access token is required")
        self._token = access_token
        self._client = client or httpx.Client(timeout=25)
        self._owns_client = client is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _safe_url(url: str) -> bool:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.netloc == "graph.microsoft.com" and (parsed.path == "/v1.0/me" or parsed.path.startswith("/v1.0/me/"))

    def _get(self, url: str, *, params: dict[str, str] | None = None, allow_not_found: bool = False) -> dict | None:
        if not self._safe_url(url):
            raise OutlookGraphError("Graph returned an unexpected pagination URL")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"',
        }
        for attempt in range(3):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise OutlookGraphError("Graph connection failed") from exc
            if response.status_code == 404 and allow_not_found:
                return None
            if response.status_code in {429, 502, 503, 504} and attempt < 2:
                raw_delay = response.headers.get("Retry-After", "1")
                delay = min(max(int(raw_delay), 1), 5) if raw_delay.isdigit() and len(raw_delay) < 10 else 1
                self._sleep(delay)
                continue
            if response.status_code >= 400:
                raise OutlookGraphError(f"Graph request failed (HTTP {response.status_code})")
            try:
                payload = response.json()
            except ValueError as exc:
                raise OutlookGraphError("Graph returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise OutlookGraphError("Graph returned invalid JSON")
            return payload
        raise OutlookGraphError("Graph request could not complete")

    def identity(self) -> dict:
        payload = self._get(f"{GRAPH_ROOT}/me", params={"$select": "id,mail,userPrincipalName"})
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise OutlookGraphError("Graph identity is unavailable")
        return payload

    def iter_sender_messages(self, sender: str, since: datetime, *, max_pages: int = 20):
        sender = normalize_sender(sender)
        if since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        if max_pages < 1 or max_pages > 50:
            raise ValueError("max_pages must be between 1 and 50")
        stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_sender = sender.replace("'", "''")
        params = {
            "$filter": f"receivedDateTime ge {stamp} and from/emailAddress/address eq '{safe_sender}'",
            "$select": MESSAGE_FIELDS,
            "$top": "100",
        }
        url: str | None = f"{GRAPH_ROOT}/me/messages"
        pages = 0
        while url:
            if pages >= max_pages:
                raise OutlookGraphError("Graph sender query exceeded its page limit; checkpoint was not advanced")
            payload = self._get(url, params=params)
            if payload is None or not isinstance(payload.get("value"), list):
                raise OutlookGraphError("Graph returned an invalid message page")
            for item in payload["value"]:
                if not isinstance(item, dict):
                    continue
                from_value = item.get("from")
                email_value = from_value.get("emailAddress") if isinstance(from_value, dict) else None
                from_address = email_value.get("address") if isinstance(email_value, dict) else None
                if not isinstance(from_address, str) or from_address.strip().casefold() != sender:
                    continue
                yield item
            next_link = payload.get("@odata.nextLink")
            if next_link is not None and (not isinstance(next_link, str) or not self._safe_url(next_link)
                                          or urlsplit(next_link).path != "/v1.0/me/messages"):
                raise OutlookGraphError("Graph returned an unexpected pagination URL")
            url = next_link
            params = None
            pages += 1

    def get_message(self, graph_message_id: str) -> dict | None:
        if not graph_message_id or len(graph_message_id) > 2048:
            raise ValueError("invalid Graph message ID")
        payload = self._get(
            f"{GRAPH_ROOT}/me/messages/{quote(graph_message_id, safe='')}",
            params={"$select": MESSAGE_FIELDS}, allow_not_found=True,
        )
        return payload
