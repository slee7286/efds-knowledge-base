"""Pure review-transition rules shared by backend review tooling.

The website enforces the same transitions through its server action. Keeping
the rules here also gives backend scripts and tests a single vocabulary for
review history without touching source synchronisation behavior.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REVIEW_STATUSES = frozenset({"proposed", "approved", "rejected", "needs_review", "superseded"})
REVIEW_ACTIONS = frozenset({"approve", "edit_approve", "reject", "needs_review", "supersede"})
PUBLICATION_ACTIONS = frozenset({"publish", "unpublish"})


class ReviewValidationError(ValueError):
    """Raised when a review transition would lose auditability or meaning."""


def validate_review_version(current_version: int, expected_version: int) -> None:
    """Reject a stale browser snapshot before it can overwrite a decision."""

    if current_version < 1 or expected_version < 1:
        raise ReviewValidationError("Review versions must be positive")
    if current_version != expected_version:
        raise ReviewValidationError("concurrency_conflict")


def transition_status(current_status: str, action: str, *, reason: str | None = None) -> str:
    """Return the next status for a validated review action."""

    if current_status not in REVIEW_STATUSES:
        raise ReviewValidationError(f"Unsupported current review status: {current_status}")
    if action not in REVIEW_ACTIONS:
        raise ReviewValidationError(f"Unsupported review action: {action}")
    if action == "reject" and not (reason and reason.strip()):
        raise ReviewValidationError("Rejecting knowledge requires a reason")
    if action in {"approve", "edit_approve"}:
        return "approved"
    if action == "reject":
        return "rejected"
    if action == "needs_review":
        return "needs_review"
    return "superseded"


def preserve_original_extraction(metadata: dict[str, Any], field: str, value: Any) -> dict[str, Any]:
    """Capture the first extracted value before an edited interpretation replaces it."""

    result = deepcopy(metadata)
    result.setdefault("original_extraction", {"field": field, "value": value})
    return result
