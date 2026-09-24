"""Read two approved senders from one delegated Outlook mailbox."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from efds.db.session import session_scope
from efds.integrations.outlook import sync_outlook
from efds.integrations.outlook_graph import OutlookGraphClient, OutlookGraphError

SCOPES = ["Mail.Read", "User.Read"]


def _cache_path() -> Path:
    configured = os.environ.get("OUTLOOK_TOKEN_CACHE_PATH", "").strip()
    path = Path(configured).expanduser() if configured else Path.home() / ".local/share/efds/outlook-token-cache.json"
    if path.resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError("Keep the Outlook token cache outside the repository")
    return path


def _token_cache(path: Path):
    try:
        import msal
    except ImportError as exc:
        raise RuntimeError("Install the optional Outlook dependency: uv pip install --python .venv/bin/python -e '.[outlook]'") from exc
    cache = msal.SerializableTokenCache()
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError("Outlook token cache must be owner-only (0600)")
        cache.deserialize(path.read_text())
    return msal, cache


def _save_cache(path: Path, cache) -> None:
    if not cache.has_state_changed:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="outlook-token-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(cache.serialize())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _access_token(*, authorize: bool, expected_mailbox: str, cache_path: Path):
    client_id = os.environ.get("OUTLOOK_CLIENT_ID", "").strip()
    tenant_id = os.environ.get("OUTLOOK_TENANT_ID", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", client_id):
        raise RuntimeError("OUTLOOK_CLIENT_ID must be a Microsoft Entra app UUID")
    if not re.fullmatch(r"[A-Za-z0-9.-]{2,100}", tenant_id):
        raise RuntimeError("OUTLOOK_TENANT_ID must be an Entra tenant ID or domain")
    msal, cache = _token_cache(cache_path)
    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )
    token = None
    for account in app.get_accounts():
        if str(account.get("username") or "").casefold() == expected_mailbox.casefold():
            result = app.acquire_token_silent(SCOPES, account=account)
            if result and result.get("access_token"):
                token = result["access_token"]
                break
    if token is None and authorize:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError("Microsoft device authorization could not start")
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
        token = result.get("access_token")
    if not token:
        raise RuntimeError("No delegated Outlook token is available; run once with --authorize")
    return token, cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync exactly two allowlisted Outlook senders using delegated Mail.Read.")
    parser.add_argument("--authorize", action="store_true", help="Start a one-time Microsoft device-code sign-in if needed")
    parser.add_argument("--check", action="store_true", help="Verify the delegated mailbox without reading messages")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without database writes")
    parser.add_argument("--since", help="UTC ISO date/time override for a controlled backfill")
    args = parser.parse_args(argv)
    load_dotenv()
    expected = os.environ.get("OUTLOOK_EXPECTED_MAILBOX_EMAIL", "").strip()
    senders = tuple(part.strip() for part in os.environ.get("OUTLOOK_ALLOWED_SENDERS", "").split(",") if part.strip())
    if not expected or len(senders) != 2:
        print("Configure OUTLOOK_EXPECTED_MAILBOX_EMAIL and exactly two OUTLOOK_ALLOWED_SENDERS in the private environment.", file=sys.stderr)
        return 2
    try:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since else None
        token, cache = _access_token(authorize=args.authorize, expected_mailbox=expected, cache_path=_cache_path())
        client = OutlookGraphClient(token)
        try:
            if args.check:
                from efds.integrations.outlook import _match_mailbox
                _match_mailbox(client.identity(), expected)
                print("Delegated mailbox identity verified; no messages read.")
                _save_cache(_cache_path(), cache)
                return 0
            with session_scope() as session:
                summary = sync_outlook(session, client, expected_mailbox_email=expected,
                                       allowed_senders=senders, since=since, dry_run=args.dry_run)
            _save_cache(_cache_path(), cache)
            print(f"Outlook {'dry run' if args.dry_run else 'sync'} complete: senders={summary.senders}, "
                  f"seen={summary.seen}, created={summary.created}, updated={summary.updated}, "
                  f"unchanged={summary.unchanged}, missing={summary.missing}, deleted={summary.deleted}")
            return 0
        finally:
            client.close()
    except (OutlookGraphError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Outlook sync failed ({type(error).__name__}); no credentials or message content were logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
