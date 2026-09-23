from uuid import uuid4

from efds.db.models import Officer
from efds.integrations.ticket_import import parse_ticket


def _officers():
    return [
        Officer(id=uuid4(), name="Siheon Lee", role="Chair", academic_year="2026-27"),
        Officer(id=uuid4(), name="Nikodem Brol", role="Competition Officer", academic_year="2026-27"),
        Officer(id=uuid4(), name="Queena Zeng", role="Social Secretary", academic_year="2026-27"),
    ]


def test_reaction_is_authoritative_for_completion_and_title_is_decoded():
    officers = _officers()
    draft = parse_ticket(
        "ACTION-012 — Recover Society Accounts &amp; Access\nPeople Assigned: Siheon\nDue: As soon as possible\nPriority: High\nStatus: Not Started\n\nRecover account access.",
        {"white_check_mark"}, officers,
    )
    assert draft is not None
    assert draft.title == "ACTION-012 — Recover Society Accounts & Access"
    assert draft.status == "completed"
    assert draft.officer_ids == (officers[0].id,)
    assert draft.due_text == "As soon as possible"


def test_named_and_shared_assignments_remain_distinct():
    officers = _officers()
    named = parse_ticket("ACTION-020 — Prepare promotion\nPeople Assigned: Queena, Nik, and Relevant People\nDue: Before launch\nPriority: Medium\nStatus: Not Started\nPrepare assets.", {"x"}, officers)
    shared = parse_ticket("ACTION-023 — Confirm training\nPeople Assigned: All Committee\nDue: Soon\nPriority: High\nStatus: Not Started\nConfirm quizzes.", {"x"}, officers)
    assert named is not None and shared is not None
    assert set(named.officer_ids) == {officers[1].id, officers[2].id}
    assert set(shared.officer_ids) == {officer.id for officer in officers}
    assert named.status == shared.status == "open"


def test_in_progress_text_does_not_override_a_completion_reaction():
    draft = parse_ticket("ACTION-019 — Book room\nPeople Assigned: Siheon\nDue: Soon\nPriority: Urgent\nStatus: In Progress\nBook a room.", {"white_check_mark"}, _officers())
    assert draft is not None
    assert draft.status == "completed"
