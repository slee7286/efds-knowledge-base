"""Configure the deployed EFDS hook through hidden prompts; never persist credentials."""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = "immldithmugfrpojetmm"
BASE = f"https://api.supabase.com/v1/projects/{PROJECT}"
HOOK = f"https://{PROJECT}.supabase.co/functions/v1/send-auth-email"


def request_json(url, method="GET", payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=25) as response:
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as error:
        # The response can contain configuration secrets: do not print it.
        raise RuntimeError(f"Request failed: HTTP {error.code}. Check project access and the dashboard; no response secrets were printed.") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Network request failed. Check dashboard state before retrying a configuration change.") from None


def hidden(label):
    if not sys.stdin.isatty():
        raise RuntimeError("Run interactively in a terminal so credentials can be entered at hidden prompts.")
    value = getpass.getpass(label).strip()
    if not value:
        raise RuntimeError("A credential was not supplied.")
    return value


def ensure_target(config):
    if config.get("hook_send_email_uri") != HOOK:
        raise RuntimeError("This is not the prepared EFDS hook. Refusing to change another email hook.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for action in ("configure", "activate", "status", "disable"):
        mode.add_argument(f"--{action}", action="store_true")
    args = parser.parse_args(argv)
    print(f"EFDS Supabase project: {PROJECT}")
    token = hidden("Supabase personal access token (hidden): ")
    config = request_json(f"{BASE}/config/auth", token=token)
    if args.status:
        health = request_json(HOOK)
        print(json.dumps({"hook_enabled": bool(config.get("hook_send_email_enabled")), "correct_hook_url": config.get("hook_send_email_uri") == HOOK, "function_configured": health.get("configured"), "missing_secret_names": health.get("missing")}, indent=2))
        return 0
    if args.configure:
        if config.get("hook_send_email_enabled"):
            raise RuntimeError("An email hook is already enabled. Use --status; configuration will not rotate a live signing secret.")
        current_uri = config.get("hook_send_email_uri")
        if current_uri and current_uri != HOOK:
            raise RuntimeError("A different hook is configured. Refusing to replace it.")
        if "resend" not in str(config.get("smtp_host", "")).lower():
            raise RuntimeError("Existing Resend SMTP rollback configuration was not found. Review SMTP settings first.")
        resend_key = hidden("Resend sending API key (hidden): ")
        if not resend_key.startswith("re_"):
            raise RuntimeError("Expected a Resend API key beginning re_.")
        hook_secret = "v1,whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()
        request_json(f"{BASE}/secrets", "POST", [
            {"name": "RESEND_API_KEY", "value": resend_key},
            {"name": "SEND_EMAIL_HOOK_SECRET", "value": hook_secret},
        ], token)
        request_json(f"{BASE}/config/auth", "PATCH", {
            "hook_send_email_enabled": False,
            "hook_send_email_uri": HOOK,
            "hook_send_email_secrets": hook_secret,
        }, token)
        print("Secrets stored; HTTP hook configured but DISABLED. Resend SMTP is unchanged.")
        for attempt in range(3):
            health = request_json(HOOK)
            if health.get("configured"):
                print("Function configuration is ready. Provider credentials and inbox delivery still require live testing.")
                return 0
            if attempt < 2:
                time.sleep(2)
        raise RuntimeError("Secrets are saved, but function propagation/readiness is incomplete. Run --status later; do not activate yet.")
    ensure_target(config)
    if args.activate:
        health = request_json(HOOK)
        if not health.get("configured"):
            raise RuntimeError("Required Edge Function secrets are missing; run --configure first.")
        if not config.get("hook_send_email_secrets"):
            raise RuntimeError("Hook signing configuration is missing; run --configure first.")
        request_json(f"{BASE}/config/auth", "PATCH", {"hook_send_email_enabled": True}, token)
        print("Email hook enabled. Test EFDS sign-in and password reset now. Use --disable immediately if either fails.")
    else:
        request_json(f"{BASE}/config/auth", "PATCH", {"hook_send_email_enabled": False}, token)
        print("Email hook disabled. Preserved SMTP configuration handles email again.")
    verified = request_json(f"{BASE}/config/auth", token=token)
    if bool(verified.get("hook_send_email_enabled")) != bool(args.activate):
        raise RuntimeError("The requested hook state was not confirmed. Inspect Authentication > Hooks.")
    print("Hook state verified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Cancelled. Check --status if a configuration request had already begun.", file=sys.stderr)
        raise SystemExit(130)
