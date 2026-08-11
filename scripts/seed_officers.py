"""Idempotently seed the 2026-27 EFDS officers."""

from __future__ import annotations

import sys

from sqlalchemy import select

from efds.db.models import Officer
from efds.db.session import session_scope

ACADEMIC_YEAR = "2026-27"

OFFICERS = [
    ("Siheon Lee", "Chair", "efds_committee"),
    ("Katia Bubenok-Honchar", "Treasurer", "efds_committee"),
    ("Teja Sule", "Secretary", "efds_committee"),
    ("Queena Zeng", "Social Secretary", "efds_committee"),
    ("Shashwat Sarawagi", "Events Officer", "efds_committee"),
    ("Yifan Dai", "Vice President", "efds_committee"),
    ("Hannah Khalique", "Economics Industry Officer", "efds_committee"),
    ("Iphazha Masala", "Year 2 General Secretary", "efds_committee"),
    ("Nikodem Brol", "Competition Officer", "efds_committee"),
    ("Alice Ye", "Year 3 General Secretary", "efds_committee"),
    ("Eesa Jaswal", "Finance Industry Officer", "efds_committee"),
    ("Tanuj Kakumani", "Data Science Industry Officer", "efds_committee"),
    ("Snow Sun", "Departmental Wellbeing Representative", "departmental_representative"),
    ("Jack Rose", "Departmental Academic Representative", "departmental_representative"),
]


def main() -> int:
    created = 0
    try:
        with session_scope() as session:
            for name, role, relationship in OFFICERS:
                existing = session.scalar(
                    select(Officer).where(
                        Officer.name == name,
                        Officer.role == role,
                        Officer.academic_year == ACADEMIC_YEAR,
                    )
                )
                if existing is None:
                    session.add(
                        Officer(
                            name=name,
                            role=role,
                            academic_year=ACADEMIC_YEAR,
                            active=True,
                            metadata_={"relationship": relationship},
                        )
                    )
                    created += 1
                elif existing.metadata_.get("relationship") != relationship:
                    existing.metadata_ = {
                        **existing.metadata_,
                        "relationship": relationship,
                    }
    except Exception as error:
        print(f"Officer seeding failed: {error}", file=sys.stderr)
        return 1
    print(f"Seeded {created} new officers; {len(OFFICERS) - created} already existed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
