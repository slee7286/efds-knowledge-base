from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from efds.auth import (
    apply_role_grant,
    highest_role,
    is_active_exception,
    is_allowed_domain,
    normalize_email,
    resolve_access,
)
from efds.db.models import AuthAccessException, Profile


def test_email_normalization_and_exact_domain_matching() -> None:
    assert normalize_email(" Person@IC.AC.UK ") == "person@ic.ac.uk"
    assert is_allowed_domain("person@ic.ac.uk") is True
    assert is_allowed_domain("person@imperial.ac.uk") is True
    assert is_allowed_domain("person@fakeic.ac.uk") is False
    assert is_allowed_domain("person@ic.ac.uk.attacker.com") is False


def test_email_and_auth_identity_constraints_are_unique() -> None:
    assert Profile.__table__.c.auth_user_id.unique is True
    assert Profile.__table__.c.email.unique is True
    assert AuthAccessException.__table__.c.email.unique is True


def test_exception_activity_and_expiry() -> None:
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert is_active_exception(active=True, expires_at=None, now=now) is True
    assert is_active_exception(active=True, expires_at=datetime(2026, 8, 12, tzinfo=timezone.utc), now=now) is True
    assert is_active_exception(active=True, expires_at=datetime(2026, 8, 10, tzinfo=timezone.utc), now=now) is False
    assert is_active_exception(active=False, expires_at=None, now=now) is False


def test_provisioning_decision_and_no_role_downgrade() -> None:
    internal = resolve_access("student@imperial.ac.uk")
    assert internal.allowed is True
    assert internal.access_role == "member"
    external = resolve_access(
        "guest@example.com",
        exception_role="viewer",
        exception_active=True,
        now=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    assert external.allowed is True
    assert external.access_role == "member"
    assert external.member_type == "external"
    assert highest_role("admin", "member") == "admin"
    assert highest_role("viewer", "committee") == "committee"
    assert highest_role("member", "efds_member") == "efds_member"


def test_grant_access_cli_update_is_idempotent() -> None:
    profile = SimpleNamespace(access_role="member", officer_id=None)
    officer_id = uuid4()
    apply_role_grant(profile, role="admin", officer_id=officer_id)
    apply_role_grant(profile, role="admin", officer_id=officer_id)
    assert profile.access_role == "admin"
    assert profile.officer_id == officer_id


def test_invalid_email_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_email("not-an-email")
