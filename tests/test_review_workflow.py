import pytest

from efds.db.models import KnowledgeRequirement, KnowledgeReviewEvent
from efds.knowledge.review import (
    ReviewValidationError,
    preserve_original_extraction,
    transition_status,
    validate_review_version,
)


def test_review_status_transitions_cover_approve_reject_defer_and_supersede() -> None:
    assert transition_status("proposed", "approve") == "approved"
    assert transition_status("proposed", "edit_approve") == "approved"
    assert transition_status("approved", "reject", reason="outdated") == "rejected"
    assert transition_status("proposed", "needs_review") == "needs_review"
    assert transition_status("approved", "supersede") == "superseded"


def test_rejection_requires_a_reason_and_unknown_actions_are_denied() -> None:
    with pytest.raises(ReviewValidationError):
        transition_status("proposed", "reject")
    with pytest.raises(ReviewValidationError):
        transition_status("proposed", "publish")


def test_edit_approve_preserves_the_first_extracted_value() -> None:
    metadata = preserve_original_extraction({}, "requirement_text", "Raw extraction")
    edited = preserve_original_extraction(metadata, "requirement_text", "Second edit")
    assert edited["original_extraction"] == {"field": "requirement_text", "value": "Raw extraction"}


def test_schema_contains_profile_reviewer_publication_and_audit_fields() -> None:
    derived = KnowledgeRequirement.__table__.c
    assert {"reviewed_by_profile_id", "visibility", "published_at", "published_by_profile_id", "review_version"} <= set(derived.keys())
    assert {"knowledge_type", "knowledge_record_id", "action", "reviewer_profile_id", "changes"} <= set(KnowledgeReviewEvent.__table__.c.keys())


def test_review_version_rejects_stale_snapshots() -> None:
    validate_review_version(4, 4)
    with pytest.raises(ReviewValidationError, match="concurrency_conflict"):
        validate_review_version(5, 4)
