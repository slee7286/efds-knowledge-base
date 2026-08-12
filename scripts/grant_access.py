"""Grant or update a durable EFDS application access role.

This is intentionally a backend CLI. It requires DATABASE_URL access, never
accepts credentials from command-line arguments, and is safe to run repeatedly.
The target profile must already exist from a successful authentication flow.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from sqlalchemy import select

from efds.auth import AccessRole, apply_role_grant, normalize_email
from efds.db.models import Officer, Profile
from efds.db.session import session_scope


def grant_access(*, email: str, role: AccessRole, officer_id: UUID | None = None) -> str:
    normalized = normalize_email(email)
    with session_scope() as session:
        profile = session.scalar(select(Profile).where(Profile.email == normalized))
        if profile is None:
            raise LookupError(
                f"No profile exists for {normalized}. Ask the user to sign in once, then rerun this command."
            )
        if officer_id is not None:
            officer = session.get(Officer, officer_id)
            if officer is None:
                raise LookupError(f"No officer exists with id {officer_id}.")
        apply_role_grant(profile, role=role, officer_id=officer_id)
        return f"Granted {role} access to {profile.email} (profile {profile.id})."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grant an EFDS profile an application access role.")
    parser.add_argument("email")
    parser.add_argument("--role", choices=("viewer", "member", "committee", "admin"), required=True)
    parser.add_argument("--officer-id", type=UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(grant_access(email=args.email, role=args.role, officer_id=args.officer_id))
    except (LookupError, ValueError) as error:
        print(f"Access grant failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
