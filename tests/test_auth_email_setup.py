"""Ensure the setup helper never rotates a live hook or changes SMTP settings."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("email_setup", Path(__file__).parents[1] / "scripts/configure_auth_email_fallback.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_configure_preserves_smtp_and_uses_same_generated_signing_secret(monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "hidden", lambda label: "re_test" if "Resend" in label else "test_token")

    def request(url, method="GET", payload=None, token=None):
        calls.append((url, method, payload))
        if url == setup.HOOK:
            return {"configured": True}
        return {"smtp_host": "smtp.resend.com", "hook_send_email_enabled": False}

    monkeypatch.setattr(setup, "request_json", request)
    assert setup.main(["--configure"]) == 0
    secret_write = next(payload for url, method, payload in calls if url.endswith("/secrets"))
    config_write = next(payload for _, method, payload in calls if method == "PATCH")
    assert config_write["hook_send_email_enabled"] is False
    assert not any(k.startswith("smtp_") for k in config_write)
    assert secret_write[1]["value"] == config_write["hook_send_email_secrets"]
    assert config_write["hook_send_email_secrets"].startswith("v1,whsec_")


def test_live_hook_cannot_be_overwritten(monkeypatch):
    monkeypatch.setattr(setup, "hidden", lambda _: "test_token")
    monkeypatch.setattr(setup, "request_json", lambda *a, **k: {"hook_send_email_enabled": True})
    with pytest.raises(RuntimeError, match="already enabled"):
        setup.main(["--configure"])


def test_activation_requires_ready_function(monkeypatch):
    monkeypatch.setattr(setup, "hidden", lambda _: "test_token")
    calls = []

    def request(url, method="GET", **kwargs):
        calls.append(method)
        return {"configured": False} if url == setup.HOOK else {"hook_send_email_uri": setup.HOOK}

    monkeypatch.setattr(setup, "request_json", request)
    with pytest.raises(RuntimeError, match="missing"):
        setup.main(["--activate"])
    assert "PATCH" not in calls
