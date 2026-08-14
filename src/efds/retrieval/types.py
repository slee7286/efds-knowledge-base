"""Typed contracts shared by website retrieval and future agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


AccessScope = Literal["public", "member", "committee", "admin"]


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    source_types: tuple[str, ...] = ()
    source_area: str | None = None
    topic: str | None = None
    channel: str | None = None
    author: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    include_history: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    retrieval_unit_id: str
    source_type: str
    source_record_id: str
    title: str
    snippet: str
    score: float
    source_parent_id: str | None = None
    source_version_id: str | None = None
    source_area: str | None = None
    topic: str | None = None
    channel: str | None = None
    author: str | None = None
    occurred_at: datetime | None = None
    source_updated_at: datetime | None = None
    review_status: str | None = None
    visibility: str | None = None
    is_current: bool = True
    is_stale: bool = False
    source_url: str | None = None
    permalink: str | None = None
    relative_path: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    authority: str | None = None
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    semantic_similarity: float | None = None
    hybrid_score: float | None = None
    score_components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextPackage:
    query: str
    scope: AccessScope
    results: tuple[RetrievalResult, ...]
    retrieval_metadata: dict[str, Any] = field(default_factory=dict)
