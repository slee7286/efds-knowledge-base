"""Small read-only Slack Web API client used by the backend sync command.

The client deliberately uses the Python standard library so ordinary backend
imports do not require a Slack SDK. It only sends Bearer authentication and
never includes the token in logs or raised error messages.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SlackApiError(RuntimeError):
    """A safe, credential-free Slack API failure."""

    def __init__(self, message: str, *, method: str, error_code: str | None = None) -> None:
        self.method = method
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SlackIdentity:
    team_id: str
    team_name: str
    team_domain: str | None
    bot_user_id: str | None
    bot_name: str | None


class SlackClient:
    """Read-only Slack Web API adapter with cursor pagination and backoff."""

    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = "https://slack.com/api/",
        timeout: float = 30.0,
        max_retries: int = 4,
        sleep: Any = time.sleep,
    ) -> None:
        if not token.strip():
            raise ValueError("Slack token cannot be empty")
        self._token = token
        self._base_url = api_base_url.rstrip("/") + "/"
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep
        self.retry_count = 0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        """Call one Slack method, retrying only rate limits/transient network errors."""

        encoded = urlencode({key: value for key, value in params.items() if value is not None})
        url = self._base_url + method
        if encoded:
            url += "?" + encoded
        for attempt in range(self._max_retries + 1):
            request = Request(
                url,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                method="GET",
            )
            try:
                with urlopen(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                if error.code == 429 and attempt < self._max_retries:
                    self._wait(attempt, error.headers.get("Retry-After"))
                    continue
                raise SlackApiError(f"Slack HTTP request failed ({error.code})", method=method) from error
            except (URLError, TimeoutError, OSError) as error:
                if attempt < self._max_retries:
                    self._wait(attempt, None)
                    continue
                raise SlackApiError("Slack network request failed", method=method) from error
            except (ValueError, UnicodeError) as error:
                raise SlackApiError("Slack returned malformed JSON", method=method) from error

            if not isinstance(payload, dict):
                raise SlackApiError("Slack returned an invalid response", method=method)
            if payload.get("ok"):
                return payload
            error_code = str(payload.get("error") or "unknown_error")
            if error_code == "ratelimited" and attempt < self._max_retries:
                self._wait(attempt, payload.get("retry_after"))
                continue
            scope_detail = str(payload.get("needed") or "").strip()
            if error_code == "missing_scope" and scope_detail:
                error_code = f"missing_scope (needed: {scope_detail})"
            raise SlackApiError(
                f"Slack API rejected {method}: {error_code}",
                method=method,
                error_code=error_code,
            )
        raise SlackApiError("Slack request retry limit reached", method=method)

    def _wait(self, attempt: int, retry_after: Any) -> None:
        self.retry_count += 1
        try:
            delay = max(float(retry_after), 0.0) if retry_after is not None else 2**attempt
        except (TypeError, ValueError):
            delay = 2**attempt
        self._sleep(delay + random.uniform(0, min(delay * 0.25, 1.0)))

    def validate(self) -> SlackIdentity:
        auth = self.call("auth.test")
        team_id = str(auth.get("team_id") or "")
        if not team_id:
            raise SlackApiError("Slack auth response did not include a team ID", method="auth.test")
        team = self.call("team.info", team=team_id).get("team") or {}
        return SlackIdentity(
            team_id=team_id,
            team_name=str(team.get("name") or auth.get("team") or team_id),
            team_domain=str(team["domain"]) if team.get("domain") else None,
            bot_user_id=str(auth["user_id"]) if auth.get("user_id") else None,
            bot_name=str(auth["user"]) if auth.get("user") else None,
        )

    def list_channels(self) -> list[dict[str, Any]]:
        return self._paginate("conversations.list", "channels", types="public_channel,private_channel", exclude_archived="false")

    def list_users(self) -> list[dict[str, Any]]:
        return self._paginate("users.list", "members")

    def history(self, channel_id: str, *, oldest: str | None = None) -> list[dict[str, Any]]:
        return self._paginate("conversations.history", "messages", channel=channel_id, oldest=oldest)

    def replies(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        return self._paginate("conversations.replies", "messages", channel=channel_id, ts=thread_ts)

    def permalink(self, channel_id: str, message_ts: str) -> str | None:
        payload = self.call("chat.getPermalink", channel=channel_id, message_ts=message_ts)
        value = payload.get("permalink")
        return str(value) if value else None

    def _paginate(self, method: str, result_key: str, **params: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            response = self.call(method, limit=200, cursor=cursor, **params)
            values = response.get(result_key) or []
            if isinstance(values, list):
                rows.extend(value for value in values if isinstance(value, dict))
            pagination = response.get("response_metadata") or {}
            next_cursor = str(pagination.get("next_cursor") or "")
            if not next_cursor:
                break
            cursor = next_cursor
        return rows
