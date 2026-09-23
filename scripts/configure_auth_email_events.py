"""Store provider webhook credentials without echoing or persisting them locally."""

from __future__ import annotations

import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from configure_auth_email_fallback import BASE, PROJECT, hidden, request_json

FUNCTION = f"https://{PROJECT}.supabase.co/functions/v1/auth-email-events"


def rejects_forged_event(provider: str) -> bool:
    request = Request(
        f"{FUNCTION}/{provider}", data=b"{}",
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return response.status == 401
    except HTTPError as error:
        return error.code == 401
    except (URLError, TimeoutError):
        return False


def main() -> int:
    print(f"EFDS Supabase project: {PROJECT}")
    print("Create the Resend and Brevo webhooks in their dashboards first; see docs/AUTH_EMAIL_FALLBACK.md.")
    token = hidden("Supabase personal access token (hidden): ")
    resend = hidden("Resend webhook SIGNING secret, beginning whsec_ (hidden): ")
    brevo = hidden("Brevo webhook bearer token, at least 32 characters (hidden): ")
    if not resend.startswith("whsec_") or len(resend) < 20:
        raise RuntimeError("Expected the webhook signing secret from Resend, not its API key.")
    if len(brevo) < 32 or any(c.isspace() for c in brevo):
        raise RuntimeError("Expected a Brevo bearer token of at least 32 characters without spaces.")
    request_json(f"{BASE}/secrets", "POST", [
        {"name": "RESEND_WEBHOOK_SECRET", "value": resend},
        {"name": "BREVO_WEBHOOK_TOKEN", "value": brevo},
    ], token)
    for attempt in range(5):
        if all(rejects_forged_event(provider) for provider in ("resend", "brevo")):
            print("Both endpoints reject unsigned requests. Send a controlled auth email and check delivery callbacks in /admin/integrations.")
            return 0
        if attempt < 4:
            time.sleep(3)
    raise RuntimeError("Secrets were saved but endpoint readiness was not confirmed. Check Edge Function logs and both webhook dashboards.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Cancelled. Inspect both provider webhooks before retrying.", file=sys.stderr)
        raise SystemExit(130)
