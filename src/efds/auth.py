"""Pure EFDS identity and authorization decisions used by scripts and tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

AccessRole = Literal["viewer", "member", "efds_member", "committee", "admin"]
MemberType = Literal[
    "imperial", "external", "alumni", "departmental_representative", "other"
]

ROLE_RANK: dict[AccessRole, int] = {
    "viewer": 1,
    "member": 2,
    "efds_member": 3,
    "committee": 4,
    "admin": 5,
}


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    access_role: AccessRole | None
    member_type: MemberType | None


def normalize_email(email: str) -> str:
    """Normalize and validate an email for case-insensitive identity matching."""

    value = email.strip().casefold()
    if value.count("@") != 1:
        raise ValueError("email must contain exactly one @")
    local, domain = value.split("@")
    if not local or not domain or "." not in domain:
        raise ValueError("email must contain a non-empty local part and domain")
    return f"{local}@{domain}"


def email_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[1]


def is_allowed_domain(email: str, allowed_domains: tuple[str, ...] = ("ic.ac.uk", "imperial.ac.uk")) -> bool:
    domain = email_domain(email)
    return domain in {item.strip().casefold().lstrip("@") for item in allowed_domains}


def is_active_exception(*, active: bool, expires_at: datetime | None, now: datetime | None = None) -> bool:
    if not active:
        return False
    if expires_at is None:
        return True
    comparison_time = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > comparison_time


def highest_role(first: AccessRole, second: AccessRole) -> AccessRole:
    return first if ROLE_RANK[first] >= ROLE_RANK[second] else second


def apply_role_grant(profile: object, *, role: AccessRole, officer_id: object | None = None) -> None:
    """Apply the idempotent part of the grant CLI to a loaded profile."""

    profile.access_role = role
    if officer_id is not None:
        profile.officer_id = officer_id


def resolve_access(
    email: str,
    *,
    allowed_domains: tuple[str, ...] = ("ic.ac.uk", "imperial.ac.uk"),
    exception_role: AccessRole | None = None,
    exception_member_type: MemberType | None = None,
    exception_active: bool = False,
    exception_expires_at: datetime | None = None,
    now: datetime | None = None,
) -> AccessDecision:
    if is_allowed_domain(email, allowed_domains):
        return AccessDecision(True, "member", "imperial")
    if exception_role and is_active_exception(
        active=exception_active, expires_at=exception_expires_at, now=now
    ):
        # An exception permits an external identity to register. It never
        # confers a privileged role without a separate reviewed grant.
        return AccessDecision(True, "member", exception_member_type or "external")
    return AccessDecision(False, None, None)
