from datetime import UTC, datetime, timedelta

import pytest

from scripts.slack_refresh_due import refresh_due

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


def test_hourly_check_skips_fresh_enabled_channels():
    assert not refresh_due([NOW - timedelta(hours=11), NOW - timedelta(minutes=5)], NOW)


def test_oldest_channel_controls_catch_up():
    assert refresh_due([NOW - timedelta(hours=12), NOW - timedelta(minutes=5)], NOW)
    assert refresh_due([None, NOW - timedelta(minutes=5)], NOW)


def test_manual_refresh_overrides_recent_checkpoints():
    assert refresh_due([NOW - timedelta(minutes=5)], NOW, force=True)


def test_missing_enabled_channels_is_an_operational_error():
    with pytest.raises(ValueError, match="No Slack channels are enabled"):
        refresh_due([], NOW)
