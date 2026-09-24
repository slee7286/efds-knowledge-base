"""A stalled queue or scheduler fails the reminder health workflow."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_ticket_reminder_health.py"
    spec = importlib.util.spec_from_file_location("check_ticket_reminder_health", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql):
        pass

    def fetchone(self):
        return next(self.rows)


class _Connection:
    def __init__(self, rows):
        self._cursor = _Cursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_stale_reminder_fails(monkeypatch, capsys):
    module = _module()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(module.psycopg, "connect", lambda *_args, **_kwargs:
                        _Connection([(1, 0, 0, 0), (0, 0), (now,), (now,)]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only")
    assert module.main() == 1
    assert "Ticket reminder delivery needs investigation" in capsys.readouterr().out


def test_healthy_queue_passes(monkeypatch):
    module = _module()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(module.psycopg, "connect", lambda *_args, **_kwargs:
                        _Connection([(0, 0, 0, 2), (0, 0), (now-timedelta(minutes=1),), (now,)]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only")
    assert module.main() == 0
