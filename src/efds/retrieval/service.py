"""Permission-aware lexical, semantic, and hybrid retrieval."""

from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .embeddings import EmbeddingConfigurationError, EmbeddingProviderError, get_embedding_provider, vector_literal
from .types import AccessScope, ContextPackage, RetrievalFilters, RetrievalResult

RetrievalMode = Literal["lexical", "semantic", "hybrid"]
_IDENTIFIER_WORDS = re.compile(r"\b[A-Z][A-Z0-9_/-]{1,}\b")
_URL_OR_FILENAME = re.compile(r"(?:https?://|\b[\w.-]+\.(?:docx|xlsx|pdf|csv|md|html)\b)", re.I)


class RetrievalValidationError(ValueError):
    pass


def _validate(query: str, limit: int, offset: int) -> str:
    query = query.strip()
    if not query:
        raise RetrievalValidationError("query must not be empty")
    if not 1 <= limit <= 50 or offset < 0:
        raise RetrievalValidationError("limit must be between 1 and 50 and offset must be non-negative")
    return query


def _filter_params(filters: RetrievalFilters) -> dict[str, object]:
    return {"source_types": list(filters.source_types) or None, "area": filters.source_area,
            "topic": filters.topic, "channel": filters.channel, "author": filters.author,
            "from_date": filters.from_date, "to_date": filters.to_date,
            "include_history": filters.include_history}


def _map(row) -> RetrievalResult:
    return RetrievalResult(
        retrieval_unit_id=str(row["retrieval_unit_id"] if "retrieval_unit_id" in row else row["id"]),
        source_type=row["source_type"], source_record_id=row["source_record_id"],
        title=row["title"], snippet=row["snippet"], score=float(row["score"]),
        source_parent_id=row["source_parent_id"], source_version_id=row["source_version_id"],
        source_area=row["source_area"], topic=row["topic"], channel=row["channel"], author=row["author"],
        occurred_at=row["occurred_at"], source_updated_at=row["source_updated_at"],
        review_status=row["review_status"], visibility=row["visibility"], is_current=row["is_current"],
        is_stale=row["is_stale"], source_url=row["source_url"], permalink=row["permalink"],
        relative_path=row["relative_path"], content_hash=row["content_hash"], metadata=row["metadata"] or {},
        authority=row.get("authority"), semantic_similarity=float(row["semantic_similarity"]) if row.get("semantic_similarity") is not None else None,
    )


def _search_lexical(session: Session, query: str, *, scope: AccessScope, filters: RetrievalFilters, limit: int, offset: int) -> list[RetrievalResult]:
    params = {"query": query, "limit": limit, "offset": offset, "scope": scope, **_filter_params(filters)}
    rows = session.execute(text("""
      SELECT id, source_type, source_record_id, source_parent_id, source_version_id, title,
             ts_headline('simple', content, websearch_to_tsquery('simple', :query),
               'MaxFragments=2,MaxWords=50,MinWords=15,StartSel=<mark>,StopSel=</mark>') AS snippet,
             ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query), 32)
               + CASE WHEN lower(title) = lower(:query) THEN 2.0 ELSE 0.0 END
               + CASE WHEN title ILIKE '%' || :query || '%' THEN .35 ELSE 0.0 END
               + CASE authority WHEN 'approved_operational' THEN .35 WHEN 'icu_source' THEN .20
                                WHEN 'governance' THEN .20 WHEN 'committee_record' THEN .10
                                WHEN 'meeting_transcript' THEN .08 WHEN 'meeting_summary' THEN .03 ELSE 0.0 END
               + CASE WHEN is_current THEN .15 ELSE 0.0 END AS score,
             source_area, topic, channel, author, occurred_at, source_updated_at,
             review_status, visibility, authority, is_current, is_stale, source_url, permalink,
             relative_path, content_hash, metadata
      FROM retrieval_units
      WHERE search_vector @@ websearch_to_tsquery('simple', :query)
        AND (CAST(:source_types AS text[]) IS NULL OR source_type = ANY(CAST(:source_types AS text[])))
        AND (CAST(:area AS text) IS NULL OR source_area = CAST(:area AS text))
        AND (CAST(:topic AS text) IS NULL OR topic = CAST(:topic AS text))
        AND (CAST(:channel AS text) IS NULL OR channel = CAST(:channel AS text))
        AND (CAST(:author AS text) IS NULL OR author = CAST(:author AS text))
        AND (CAST(:from_date AS timestamptz) IS NULL OR coalesce(occurred_at, source_updated_at, updated_at) >= CAST(:from_date AS timestamptz))
        AND (CAST(:to_date AS timestamptz) IS NULL OR coalesce(occurred_at, source_updated_at, updated_at) < CAST(:to_date AS timestamptz))
        AND (CAST(:include_history AS boolean) OR is_current)
        AND (:scope = 'admin' OR (source_type NOT IN ('document', 'slack_message', 'meeting_transcript', 'meeting_summary', 'meeting_notes')
             AND review_status = 'approved' AND NOT is_stale AND NOT is_deleted
             AND ((visibility = 'public') OR (:scope IN ('member','committee') AND visibility = 'member')
             OR (:scope = 'committee' AND visibility = 'committee'))))
      ORDER BY score DESC, coalesce(occurred_at, source_updated_at, updated_at) DESC, id
      LIMIT :limit OFFSET :offset
    """).bindparams(), params).mappings()
    results = [_map(row) for row in rows]
    for rank, result in enumerate(results, start=1):
        object.__setattr__(result, "lexical_rank", rank)
    return results


def _search_semantic(session: Session, query_vector: list[float], *, provider: str, model: str, model_version: str,
                     scope: AccessScope, filters: RetrievalFilters, limit: int) -> list[RetrievalResult]:
    params = {"query_embedding": vector_literal(query_vector), "provider": provider, "model": model,
              "model_version": model_version, "scope": scope, "limit": min(max(limit, 1), 100), **_filter_params(filters)}
    rows = session.execute(text("""
      SELECT * FROM public.search_retrieval_units_semantic(
        CAST(:query_embedding AS vector), :provider, :model, :model_version, :scope,
        CAST(:source_types AS text[]), :area, :topic, :channel, :author,
        CAST(:from_date AS timestamptz), CAST(:to_date AS timestamptz), :include_history, :limit, 0)
    """).bindparams(), params).mappings()
    results = [_map(row) for row in rows]
    for rank, result in enumerate(results, start=1):
        object.__setattr__(result, "semantic_rank", rank)
    return results


def _fuse(lexical: list[RetrievalResult], semantic: list[RetrievalResult], limit: int, *, semantic_weight: float = 1.0) -> list[RetrievalResult]:
    by_id: dict[str, RetrievalResult] = {result.retrieval_unit_id: result for result in lexical}
    by_id.update({result.retrieval_unit_id: result for result in semantic})
    lexical_rank = {result.retrieval_unit_id: index for index, result in enumerate(lexical, start=1)}
    semantic_rank = {result.retrieval_unit_id: index for index, result in enumerate(semantic, start=1)}
    scored: list[tuple[float, RetrievalResult]] = []
    for unit_id, result in by_id.items():
        components = {"rrf": 0.0, "authority": 0.0, "current": 0.0}
        if unit_id in lexical_rank:
            components["rrf"] += 1 / (60 + lexical_rank[unit_id])
        if unit_id in semantic_rank:
            components["rrf"] += semantic_weight / (60 + semantic_rank[unit_id])
        if result.authority == "approved_operational":
            components["authority"] = 0.012
        elif result.authority in {"icu_source", "governance"}:
            components["authority"] = 0.008
        if result.is_current:
            components["current"] = 0.004
        score = sum(components.values())
        object.__setattr__(result, "score", score)
        object.__setattr__(result, "hybrid_score", score)
        object.__setattr__(result, "score_components", components)
        object.__setattr__(result, "lexical_rank", lexical_rank.get(unit_id))
        object.__setattr__(result, "semantic_rank", semantic_rank.get(unit_id))
        scored.append((score, result))
    scored.sort(key=lambda item: (-item[0], item[1].retrieval_unit_id))
    return [result for _, result in scored[:limit]]


def _semantic_primary_exact(query: str, lexical: list[RetrievalResult], semantic: list[RetrievalResult]) -> list[RetrievalResult]:
    """Keep semantic ranking primary and rescue high-confidence identifiers."""

    identifiers = [word.casefold() for word in _IDENTIFIER_WORDS.findall(query) if word not in {"I", "OK"}]
    if not identifiers and not _URL_OR_FILENAME.search(query):
        return semantic
    query_lower = query.casefold()
    rescue: list[RetrievalResult] = []
    for result in lexical:
        title = (result.title or "").casefold()
        if title == query_lower or query_lower in title or any(word in title for word in identifiers):
            rescue.append(result)
    rescued = {result.retrieval_unit_id for result in rescue}
    return rescue + [result for result in semantic if result.retrieval_unit_id not in rescued]


def make_context_package(query: str, scope: AccessScope, results: list[RetrievalResult]) -> ContextPackage:
    """Build a citation-ready package with conservative retrieval diagnostics."""

    ordered = tuple(results)
    scores = [result.score for result in ordered]
    metadata = {
        "retrieval_quality": "low" if not ordered else ("limited" if len(ordered) == 1 else "normal"),
        "result_count": len(ordered),
        "source_family_count": len({result.source_type for result in ordered}),
        "top_score": scores[0] if scores else None,
        "rank_gap": scores[0] - scores[1] if len(scores) >= 2 else None,
        "exact_lexical_match": any(result.lexical_rank == 1 and result.semantic_rank is None for result in ordered),
    }
    return ContextPackage(query=query, scope=scope, results=ordered, retrieval_metadata=metadata)


def search_retrieval(session: Session, query: str, *, scope: AccessScope = "admin",
                     filters: RetrievalFilters | None = None, limit: int = 20,
                     offset: int = 0, mode: RetrievalMode = "hybrid") -> list[RetrievalResult]:
    """Search with lexical, semantic, or RRF hybrid mode.

    Hybrid gracefully falls back to lexical when no backend embedding provider
    is configured or the provider is temporarily unavailable.
    """

    query = _validate(query, limit, offset)
    if mode not in {"lexical", "semantic", "hybrid"}:
        raise RetrievalValidationError("mode must be lexical, semantic, or hybrid")
    filters = filters or RetrievalFilters()
    candidate_limit = min(max(limit * 3, 30), 100)
    lexical = _search_lexical(session, query, scope=scope, filters=filters, limit=candidate_limit, offset=0)
    if mode == "lexical":
        return lexical[offset:offset + limit]
    try:
        provider = get_embedding_provider()
        vector = provider.embed_query(query)
        semantic = _search_semantic(session, vector, provider=provider.spec.provider, model=provider.spec.model,
                                    model_version=provider.spec.model_version, scope=scope, filters=filters,
                                    limit=candidate_limit)
    except (EmbeddingConfigurationError, EmbeddingProviderError):
        if mode == "semantic":
            raise
        return lexical[offset:offset + limit]
    if mode == "semantic":
        return semantic[offset:offset + limit]
    # V2 benchmark tuning selected semantic-primary retrieval. Lexical results
    # are used only for high-confidence acronym, URL, filename, or exact-title
    # rescue; generic RRF allowed weak lexical matches to displace relevant
    # semantic results. This remains a hybrid path because both engines are
    # consulted and lexical fallback is retained when embeddings are absent.
    return _semantic_primary_exact(query, lexical, semantic)[offset:offset + limit]
