"""Grant or update a durable EFDS application access role.

This is intentionally a backend CLI. It requires DATABASE_URL access, never
accepts credentials from command-line arguments, and is safe to run repeatedly.
The target profile must already exist from a successful authentication flow.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select

from efds.auth import AccessRole, apply_role_grant, normalize_email
from efds.db.models import Officer, Profile
from efds.db.session import session_scope

OfficerLike = TypeVar("OfficerLike")


def match_active_officer(
    officers: Sequence[OfficerLike], *, name: str, academic_year: str | None = None
) -> OfficerLike:
    """Resolve a roster name only when it identifies one active officer."""

    normalized_name = name.strip().casefold()
    if not normalized_name:
        raise ValueError("--officer-name must not be empty.")
    matches = [
        officer for officer in officers
        if officer.active
        and officer.name.strip().casefold() == normalized_name
        and (academic_year is None or officer.academic_year == academic_year)
    ]
    if not matches:
        suffix = f" in {academic_year}" if academic_year else ""
        raise LookupError(f"No active officer named {name.strip()!r}{suffix}. Check the roster spelling.")
    if len(matches) > 1:
        options = "; ".join(
            f"{officer.name} — {officer.role} ({officer.academic_year})" for officer in matches
        )
        raise ValueError(
            f"More than one active roster entry matches {name.strip()!r}: {options}. "
            "Add --academic-year or use --officer-id."
        )
    return matches[0]


def grant_access(
    *, email: str, role: AccessRole, officer_id: UUID | None = None,
    officer_name: str | None = None, academic_year: str | None = None,
) -> str:
    normalized = normalize_email(email)
    if officer_id is not None and officer_name is not None:
        raise ValueError("Choose either --officer-id or --officer-name.")
    if academic_year is not None and officer_name is None:
        raise ValueError("--academic-year requires --officer-name.")
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
            if not officer.active:
                raise ValueError("The selected officer roster entry is inactive.")
        elif officer_name is not None:
            officer = match_active_officer(
                session.scalars(select(Officer)).all(),
                name=officer_name,
                academic_year=academic_year,
            )
            officer_id = officer.id
        if officer_id is not None:
            existing_link = session.scalar(
                select(Profile).where(
                    Profile.officer_id == officer_id,
                    Profile.id != profile.id,
                    Profile.active.is_(True),
                )
            )
            if existing_link is not None:
                raise ValueError(
                    f"That roster entry is already linked to {existing_link.email}. "
                    "Resolve the existing link before granting access."
                )
        apply_role_grant(profile, role=role, officer_id=officer_id)
        roster = f"; linked to {officer.name} — {officer.role} ({officer.academic_year})" if officer_id else ""
        return f"Granted {role} access to {profile.email} (profile {profile.id}){roster}."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grant an EFDS profile an application access role.")
    parser.add_argument("email")
    parser.add_argument("--role", choices=("viewer", "member", "committee", "admin"), required=True)
    officer_selector = parser.add_mutually_exclusive_group()
    officer_selector.add_argument("--officer-id", type=UUID)
    officer_selector.add_argument("--officer-name", help="Exact full name from the active officer roster")
    parser.add_argument("--academic-year", help="Disambiguate a repeated roster name, e.g. 2026/27")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(grant_access(
            email=args.email, role=args.role, officer_id=args.officer_id,
            officer_name=args.officer_name, academic_year=args.academic_year,
        ))
    except (LookupError, ValueError) as error:
        print(f"Access grant failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
