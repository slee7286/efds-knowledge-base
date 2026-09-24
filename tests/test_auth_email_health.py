"""A missed account-change email must fail the scheduled health check."""

import importlib.util
from pathlib import Path


def _health_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_auth_email_health.py"
    spec = importlib.util.spec_from_file_location("check_auth_email_health", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cursor:
    def __init__(self, auth, account):
        self.rows = iter((auth, account))
        self.deletes = 0
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        if sql.lstrip().startswith("DELETE"):
            self.deletes += 1
            self.rowcount = 0

    def fetchone(self):
        return next(self.rows)


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_stale_account_notice_fails_health_workflow(monkeypatch, capsys, tmp_path):
    module = _health_module()
    cursor = _Cursor((2, 0, 2, 0, 0, 0, 0), (0, 1, 0, 0))
    monkeypatch.setattr(module.psycopg, "connect", lambda *_args, **_kwargs: _Connection(cursor))
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert module.main() == 1
    assert cursor.deletes == 2
    assert "Account-change emails need investigation" in capsys.readouterr().out
    assert "pending over 5 minutes: 1" in summary.read_text()


def test_healthy_auth_and_account_mail_pass(monkeypatch, capsys):
    module = _health_module()
    cursor = _Cursor((2, 0, 2, 0, 0, 0, 0), (1, 0, 0, 0))
    monkeypatch.setattr(module.psycopg, "connect", lambda *_args, **_kwargs: _Connection(cursor))
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert module.main() == 0
    assert "accepted in 24 hours: 1" in capsys.readouterr().out
