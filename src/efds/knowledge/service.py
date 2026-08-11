"""Persist deterministic ICU extraction with idempotency and stale-version handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from efds.db.models import (
    KnowledgeArticle,
    KnowledgeArticleRole,
    KnowledgeArticleTopic,
    KnowledgeContact,
    KnowledgeExtractionRun,
    KnowledgeProcess,
    KnowledgeProcessResource,
    KnowledgeProcessRole,
    KnowledgeProcessStep,
    KnowledgeRequirement,
    KnowledgeRequirementRole,
    KnowledgeResource,
    KnowledgeRole,
    KnowledgeTimingRule,
    KnowledgeTopic,
)
from efds.ingestion.hashing import sha256_bytes

from .deterministic import (
    article_text,
    extract_contacts,
    extract_process,
    extract_requirements,
    extract_resources,
    extract_timing_rules,
)
from .semantic import NullSemanticExtractor, SemanticExtractor
from .taxonomy import (
    RELEVANCE_ORDER,
    ROLES,
    TOPICS,
    classify_article,
    relevance_at_least,
)

EXTRACTOR_VERSION = "icu-knowledge-v1"
DERIVED_TYPES = (
    KnowledgeRequirement,
    KnowledgeTimingRule,
    KnowledgeProcess,
    KnowledgeProcessStep,
    KnowledgeResource,
    KnowledgeContact,
)


@dataclass(slots=True)
class ExtractionSummary:
    run_id: str | None
    status: str
    article_count: int = 0
    deterministic_count: int = 0
    semantic_count: int = 0
    proposed_count: int = 0
    skipped_unchanged: int = 0
    failed_count: int = 0
    errors: list[dict[str, str]] | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(*values: object) -> str:
    return hashlib.sha256("\x1f".join(str(value or "") for value in values).encode("utf-8")).hexdigest()


def _topic_map(session: Session) -> dict[str, UUID]:
    result: dict[str, UUID] = {}
    for slug, label in TOPICS:
        row = session.scalar(select(KnowledgeTopic).where(KnowledgeTopic.slug == slug))
        if row is None:
            row = KnowledgeTopic(slug=slug, label=label)
            session.add(row)
            session.flush()
        result[slug] = row.id
    return result


def _role_map(session: Session) -> dict[str, UUID]:
    result: dict[str, UUID] = {}
    for slug, label, role_group in ROLES:
        row = session.scalar(select(KnowledgeRole).where(KnowledgeRole.slug == slug))
        if row is None:
            row = KnowledgeRole(slug=slug, label=label, role_group=role_group)
            session.add(row)
            session.flush()
        result[slug] = row.id
    return result


def _mark_stale(session: Session, article: KnowledgeArticle) -> int:
    marked = 0
    for model in DERIVED_TYPES:
        rows = session.scalars(
            select(model).where(
                model.source_article_id == article.id,
                model.source_content_hash != article.content_hash,
                model.is_stale.is_(False),
            )
        ).all()
        for row in rows:
            row.is_stale = True
            marked += 1
    return marked


def _base_fields(article: KnowledgeArticle, run: KnowledgeExtractionRun, evidence: Any, *, now: datetime, fingerprint: str) -> dict[str, Any]:
    return {
        "source_article_id": article.id,
        "source_url": article.url or "",
        "source_content_hash": article.content_hash or "",
        "source_updated_at": article.source_updated_at,
        "evidence_text": evidence.text,
        "evidence_start": evidence.start,
        "evidence_end": evidence.end,
        "extraction_run_id": run.id,
        "extracted_at": now,
        "extraction_method": "deterministic",
        "confidence": 0.82,
        "review_status": "proposed",
        "is_stale": False,
        "fingerprint": fingerprint,
    }


def _upsert_derived(session: Session, model: type, fields: dict[str, Any]) -> tuple[Any, bool]:
    existing = session.scalar(
        select(model).where(
            model.source_article_id == fields["source_article_id"],
            model.source_content_hash == fields["source_content_hash"],
            model.fingerprint == fields["fingerprint"],
        )
    )
    if existing is not None:
        existing.is_stale = False
        existing.extracted_at = fields["extracted_at"]
        existing.extraction_run_id = fields["extraction_run_id"]
        existing.evidence_text = fields["evidence_text"]
        existing.evidence_start = fields["evidence_start"]
        existing.evidence_end = fields["evidence_end"]
        # Deliberately preserve review_status, reviewer, and reviewed_at.
        return existing, False
    row = model(**fields)
    session.add(row)
    session.flush()
    return row, True


def _replace_article_mappings(session: Session, article: KnowledgeArticle, topics: Iterable[str], roles: Iterable[str], topic_ids: dict[str, UUID], role_ids: dict[str, UUID]) -> None:
    session.execute(delete(KnowledgeArticleTopic).where(KnowledgeArticleTopic.knowledge_article_id == article.id))
    session.execute(delete(KnowledgeArticleRole).where(KnowledgeArticleRole.knowledge_article_id == article.id))
    for topic in topics:
        session.add(KnowledgeArticleTopic(knowledge_article_id=article.id, topic_id=topic_ids[topic], confidence=0.82))
    for role in roles:
        session.add(KnowledgeArticleRole(knowledge_article_id=article.id, role_id=role_ids[role], confidence=0.78))


def _extract_one(session: Session, article: KnowledgeArticle, run: KnowledgeExtractionRun, *, now: datetime, topic_ids: dict[str, UUID], role_ids: dict[str, UUID]) -> tuple[int, int, int]:
    raw = article.markdown or article.raw_html or ""
    text = article_text(article.markdown, article.raw_html)
    classification = classify_article(title=article.title, category=article.category, folder=article.folder, text=text)
    new_source_version = article.last_extracted_content_hash != article.content_hash
    article.efds_relevance = classification.relevance
    article.relevance_confidence = classification.confidence
    article.relevance_method = "deterministic"
    if new_source_version or not article.relevance_review_status:
        article.relevance_review_status = "proposed"
    article.relevance_evidence = classification.evidence
    article.relevance_updated_at = now
    _replace_article_mappings(session, article, classification.topics, classification.roles, topic_ids, role_ids)

    deterministic_count = proposed_count = 0
    requirements = extract_requirements(text)
    for candidate in requirements:
        fp = _fingerprint("requirement", candidate.requirement_type, candidate.text)
        fields = _base_fields(article, run, candidate, now=now, fingerprint=fp)
        fields.update(requirement_text=candidate.text, requirement_type=candidate.requirement_type, topic_id=topic_ids.get(classification.topics[0]), applies_to=candidate.applies_to, mandatory=candidate.mandatory)
        row, created = _upsert_derived(session, KnowledgeRequirement, fields)
        if created:
            deterministic_count += 1
            proposed_count += 1
        for role in classification.roles:
            session.merge(KnowledgeRequirementRole(requirement_id=row.id, role_id=role_ids[role]))

    timings = extract_timing_rules(text)
    for candidate in timings:
        fp = _fingerprint("timing", candidate.deadline_type, candidate.text, candidate.absolute_date, candidate.notice_period_value)
        fields = _base_fields(article, run, candidate, now=now, fingerprint=fp)
        fields.update(deadline_type=candidate.deadline_type, description=candidate.text, absolute_date=candidate.absolute_date, notice_period_value=candidate.notice_period_value, notice_period_unit=candidate.notice_period_unit, working_days=candidate.working_days, recurrence_rule=candidate.recurrence_rule, relative_to_event_type=candidate.relative_to_event_type, topic_id=topic_ids.get(classification.topics[0]))
        _row, created = _upsert_derived(session, KnowledgeTimingRule, fields)
        if created:
            deterministic_count += 1
            proposed_count += 1

    resources = extract_resources(raw)
    resource_rows: list[Any] = []
    for candidate in resources:
        fp = _fingerprint("resource", candidate.resource_type, candidate.url, candidate.name)
        fields = _base_fields(article, run, candidate, now=now, fingerprint=fp)
        fields.update(name=candidate.name, resource_type=candidate.resource_type, url=candidate.url, email=candidate.email, system_name=candidate.system_name, anchor_text=candidate.anchor_text, topic_id=topic_ids.get(classification.topics[0]))
        row, created = _upsert_derived(session, KnowledgeResource, fields)
        resource_rows.append(row)
        if created:
            deterministic_count += 1
            proposed_count += 1

    for candidate in extract_contacts(text):
        fp = _fingerprint("contact", candidate.email)
        fields = _base_fields(article, run, candidate, now=now, fingerprint=fp)
        fields.update(name=candidate.name, organisation=None, email=candidate.email, contact_type="email", url=None, description=candidate.text)
        _row, created = _upsert_derived(session, KnowledgeContact, fields)
        if created:
            deterministic_count += 1
            proposed_count += 1

    process_candidate = extract_process(article.title, text)
    if process_candidate is not None:
        process_fp = _fingerprint("process", process_candidate.name, process_candidate.text)
        process_fields = _base_fields(article, run, process_candidate, now=now, fingerprint=process_fp)
        process_fields.update(name=process_candidate.name, description=process_candidate.description, topic_id=topic_ids.get(classification.topics[0]))
        process, created = _upsert_derived(session, KnowledgeProcess, process_fields)
        if created:
            deterministic_count += 1
            proposed_count += 1
        for role in classification.roles:
            session.merge(KnowledgeProcessRole(process_id=process.id, role_id=role_ids[role]))
        for resource in resource_rows:
            session.merge(KnowledgeProcessResource(process_id=process.id, resource_id=resource.id))
        for step in process_candidate.steps:
            step_fp = _fingerprint("step", process.id, step.step_number, step.instruction)
            step_fields = _base_fields(article, run, step, now=now, fingerprint=step_fp)
            step_fields.update(process_id=process.id, step_number=step.step_number, title=step.title, instruction=step.instruction, condition=None)
            _row, created = _upsert_derived(session, KnowledgeProcessStep, step_fields)
            if created:
                deterministic_count += 1
                proposed_count += 1

    return deterministic_count, proposed_count, len(classification.roles)


def extract_icu_knowledge(
    session: Session,
    *,
    article_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
    deterministic_only: bool = False,
    reprocess_stale: bool = False,
    relevance_min: str | None = None,
    dry_run: bool = False,
    semantic_extractor: SemanticExtractor | None = None,
) -> ExtractionSummary:
    """Extract current ICU rows; all writes are version-keyed and repeatable."""

    # The default semantic boundary is inert, but the flag remains meaningful
    # for callers that inject a provider in tests or a future integration.
    semantic_extractor = NullSemanticExtractor() if deterministic_only else (semantic_extractor or NullSemanticExtractor())
    query = select(KnowledgeArticle).where(KnowledgeArticle.source_type == "icu_freshdesk").order_by(KnowledgeArticle.title)
    if article_id:
        conditions = [KnowledgeArticle.external_id == article_id]
        try:
            conditions.append(KnowledgeArticle.id == UUID(article_id))
        except ValueError:
            pass
        query = query.where(or_(*conditions))
    if limit is not None:
        query = query.limit(max(0, limit))
    articles = list(session.scalars(query))
    now = utc_now()
    run = None
    if not dry_run:
        run = KnowledgeExtractionRun(extractor_version=EXTRACTOR_VERSION, model_provider=semantic_extractor.provider, model_name=semantic_extractor.model)
        session.add(run)
        session.flush()
    summary = ExtractionSummary(run_id=str(run.id) if run else None, status="completed", errors=[])
    topic_ids = _topic_map(session) if not dry_run else {}
    role_ids = _role_map(session) if not dry_run else {}
    try:
        for article in articles:
            classification = classify_article(title=article.title, category=article.category, folder=article.folder, text=article.markdown or article.raw_html or "")
            if relevance_min and not relevance_at_least(classification.relevance, relevance_min):
                continue
            if not force and not reprocess_stale and article.last_extracted_content_hash == article.content_hash:
                summary.skipped_unchanged += 1
                continue
            try:
                nested = session.begin_nested() if not dry_run else None
                if nested is not None:
                    nested.__enter__()
                summary.article_count += 1
                if not dry_run:
                    _mark_stale(session, article)
                if not dry_run:
                    deterministic_count, proposed_count, _role_count = _extract_one(session, article, run, now=now, topic_ids=topic_ids, role_ids=role_ids)
                else:
                    clean_text = article_text(article.markdown, article.raw_html)
                    deterministic_count = (
                        len(extract_requirements(clean_text))
                        + len(extract_timing_rules(clean_text))
                        + len(extract_resources(article.markdown or article.raw_html or ""))
                        + len(extract_contacts(clean_text))
                    )
                    process_candidate = extract_process(article.title, clean_text)
                    if process_candidate is not None:
                        deterministic_count += 1 + len(process_candidate.steps)
                    proposed_count = deterministic_count
                    _role_count = 0
                summary.deterministic_count += deterministic_count
                summary.proposed_count += proposed_count
                if not dry_run:
                    article.last_extracted_content_hash = article.content_hash
                    article.last_extracted_at = now
                    article.last_extraction_run_id = run.id
                semantic = semantic_extractor.extract(title=article.title, text=article_text(article.markdown, article.raw_html))
                summary.semantic_count += len(semantic.requirements) + len(semantic.timing_rules) + len(semantic.processes)
                if nested is not None:
                    nested.__exit__(None, None, None)
            except Exception as error:
                if nested is not None:
                    nested.__exit__(type(error), error, error.__traceback__)
                summary.failed_count += 1
                summary.errors = [*(summary.errors or []), {"article_id": str(article.external_id or article.id), "error_type": type(error).__name__, "message": str(error)}]
        if run is not None:
            run.article_count = summary.article_count
            run.deterministic_count = summary.deterministic_count
            run.semantic_count = summary.semantic_count
            run.proposed_count = summary.proposed_count
            run.skipped_unchanged = summary.skipped_unchanged
            run.failed_count = summary.failed_count
            run.status = "completed"
            run.finished_at = utc_now()
        if dry_run:
            summary.status = "completed_dry_run"
        return summary
    except Exception as error:
        summary.failed_count += 1
        summary.status = "completed_with_errors"
        summary.errors = [{"error_type": type(error).__name__, "message": str(error)}]
        if run is not None:
            run.status = summary.status
            run.failed_count = summary.failed_count
            run.error_log = summary.errors
            run.finished_at = utc_now()
        return summary
