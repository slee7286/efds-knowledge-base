"""Create or update an explicitly approved external EFDS access exception."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from efds.auth import AccessRole, MemberType, normalize_email
from efds.db.models import AuthAccessException, Profile
from efds.db.session import session_scope


def parse_expiry(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--expires-at must include a timezone, for example 2027-09-01T00:00:00Z")
    return parsed


def set_access_exception(
    *,
    email: str,
    role: AccessRole,
    member_type: MemberType,
    reason: str | None = None,
    expires_at: datetime | None = None,
    active: bool = True,
    created_by_profile_id: UUID | None = None,
) -> str:
    normalized = normalize_email(email)
    with session_scope() as session:
        if created_by_profile_id is not None and session.get(Profile, created_by_profile_id) is None:
            raise LookupError(f"No profile exists with id {created_by_profile_id}.")
        exception = session.scalar(
            select(AuthAccessException).where(AuthAccessException.email == normalized)
        )
        if exception is None:
            exception = AuthAccessException(email=normalized)
            session.add(exception)
        exception.access_role = role
        exception.member_type = member_type
        exception.reason = reason
        exception.expires_at = expires_at
        exception.active = active
        if created_by_profile_id is not None:
            exception.created_by_profile_id = created_by_profile_id
        return f"Set {role} external access for {normalized} (active={active})."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or update an EFDS external access exception.")
    parser.add_argument("email")
    parser.add_argument("--role", choices=("viewer", "member", "committee", "admin"), required=True)
    parser.add_argument("--member-type", choices=("external", "alumni", "departmental_representative", "other"), default="external")
    parser.add_argument("--reason")
    parser.add_argument("--expires-at", help="ISO-8601 timestamp with timezone, or omit for no expiry")
    parser.add_argument("--inactive", action="store_true", help="Create/update the exception as inactive")
    parser.add_argument("--created-by-profile-id", type=UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(set_access_exception(
            email=args.email,
            role=args.role,
            member_type=args.member_type,
            reason=args.reason,
            expires_at=parse_expiry(args.expires_at),
            active=not args.inactive,
            created_by_profile_id=args.created_by_profile_id,
        ))
    except (LookupError, ValueError) as error:
        print(f"Access exception failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
