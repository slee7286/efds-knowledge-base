"""Synchronise the ICU crawler's canonical filesystem archive into PostgreSQL."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from efds.db.models import IngestionRun, KnowledgeArticle, KnowledgeArticleChange
from efds.ingestion.hashing import sha256_bytes

logger = logging.getLogger(__name__)

SOURCE_TYPE = "icu_freshdesk"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class IcuArticle:
    """A validated article read from one current crawler directory."""

    external_id: str
    title: str
    url: str
    category: str | None
    folder: str | None
    raw_html: str
    markdown: str
    content_hash: str
    source_updated_at: datetime | None
    crawled_at: datetime | None
    metadata: dict[str, Any]
    metadata_path: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class SyncDecision:
    """Pure comparison result used by the database sync and unit tests."""

    change_type: str
    fields_changed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncSummary:
    run_id: str | None
    status: str
    records_seen: int
    records_created: int
    records_updated: int
    records_skipped: int
    records_failed: int


def _parse_datetime(value: object, *, now: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    reference = now or utc_now()
    formats = (
        "%a, %d %b, %Y at %I:%M %p",
        "%a, %d %b at %I:%M %p",
        "%d %b, %Y at %I:%M %p",
        "%d %b at %I:%M %p",
    )
    for date_format in formats:
        try:
            parsed = datetime.strptime(text, date_format)
        except ValueError:
            continue
        if "%Y" not in date_format:
            parsed = parsed.replace(year=reference.year)
        return parsed.replace(tzinfo=timezone.utc)
    return None


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _source_path(data_dir: Path, value: object, fallback: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        return fallback
    relative = value.replace("\\", os.sep)
    candidate = Path(relative)
    return candidate if candidate.is_absolute() else data_dir / candidate


def _required_text(metadata: dict[str, Any], key: str, path: Path) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: metadata field {key!r} is required")
    return value.strip()


def load_article(metadata_path: Path, data_dir: Path, *, now: datetime | None = None) -> IcuArticle:
    """Load one crawler metadata record and its current HTML/Markdown files."""

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{metadata_path}: cannot read metadata: {error}") from error
    if not isinstance(metadata, dict):
        raise ValueError(f"{metadata_path}: metadata must be a JSON object")

    external_id = _required_text(metadata, "id", metadata_path)
    title = _required_text(metadata, "title", metadata_path)
    url = _canonical_url(_required_text(metadata, "source_url", metadata_path))
    article_dir = metadata_path.parent
    raw_html_path = _source_path(data_dir, metadata.get("raw_html_path"), article_dir / "article.html")
    markdown_path = _source_path(data_dir, metadata.get("markdown_path"), article_dir / "article.md")
    try:
        raw_html = raw_html_path.read_text(encoding="utf-8")
        markdown = markdown_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"{metadata_path}: cannot read article files: {error}") from error

    article_text = metadata.get("article_text")
    hash_input = article_text if isinstance(article_text, str) else markdown
    content_hash = metadata.get("content_hash")
    if not isinstance(content_hash, str) or not content_hash.strip():
        content_hash = sha256_bytes(hash_input.encode("utf-8"))

    return IcuArticle(
        external_id=external_id,
        title=title,
        url=url,
        category=metadata.get("category") or None,
        folder=metadata.get("folder") or None,
        raw_html=raw_html,
        markdown=markdown,
        content_hash=content_hash,
        source_updated_at=_parse_datetime(metadata.get("modified_at"), now=now),
        crawled_at=_parse_datetime(metadata.get("crawled_at"), now=now),
        metadata=metadata,
        metadata_path=metadata_path,
    )


def iter_metadata_paths(data_dir: Path) -> Iterable[Path]:
    """Yield current article metadata files in stable order."""

    article_root = data_dir / "articles"
    if not article_root.exists():
        raise FileNotFoundError(f"ICU article directory does not exist: {article_root}")
    yield from sorted(article_root.rglob("metadata.json"))


def compare_article(existing: KnowledgeArticle | None, article: IcuArticle) -> SyncDecision:
    """Compare source fields while keeping content hash separate from identity."""

    if existing is None:
        return SyncDecision("created", ("title", "category", "folder", "content", "source_updated_at"))
    changed: list[str] = []
    if existing.title != article.title:
        changed.append("title")
    if existing.category != article.category:
        changed.append("category")
    if existing.folder != article.folder:
        changed.append("folder")
    if existing.content_hash != article.content_hash:
        changed.append("content")
    if existing.source_updated_at != article.source_updated_at:
        changed.append("source_updated_at")
    if changed:
        return SyncDecision("updated", tuple(changed))
    if (existing.metadata_ or {}).get("missing_from_source"):
        return SyncDecision("restored", ("missing_from_source",))
    return SyncDecision("skipped")


def _description(change_type: str, fields: tuple[str, ...]) -> str:
    if change_type == "created":
        return "ICU article created."
    if change_type == "missing":
        return "ICU article is no longer present in the complete crawler corpus."
    if change_type == "restored":
        return "ICU article reappeared in the crawler corpus."
    labels = {"content": "content", "source_updated_at": "source modification timestamp"}
    readable = [labels.get(field, field) for field in fields]
    if len(readable) == 1:
        changed = readable[0]
    elif len(readable) == 2:
        changed = f"{readable[0]} and {readable[1]}"
    else:
        changed = ", ".join(readable[:-1]) + f", and {readable[-1]}"
    return f"ICU article updated: {changed} changed."


def _metadata(article: IcuArticle, data_dir: Path, *, missing: bool = False) -> dict[str, Any]:
    crawler_metadata = {key: value for key, value in article.metadata.items() if key != "article_text"}
    return {
        "source": SOURCE_TYPE,
        "data_directory": str(data_dir.resolve()),
        "crawler": crawler_metadata,
        "missing_from_source": missing,
    }


def _find_existing(session: Session, article: IcuArticle) -> KnowledgeArticle | None:
    existing = session.scalar(
        select(KnowledgeArticle).where(
            and_(
                KnowledgeArticle.source_type == SOURCE_TYPE,
                KnowledgeArticle.external_id == article.external_id,
            )
        )
    )
    if existing is not None:
        return existing
    return session.scalar(select(KnowledgeArticle).where(KnowledgeArticle.url == article.url))


def _add_change(
    session: Session,
    article: KnowledgeArticle,
    run: IngestionRun | None,
    *,
    change_type: str,
    fields: tuple[str, ...],
    previous_hash: str | None,
    new_hash: str | None,
    previous_source_updated_at: datetime | None,
    new_source_updated_at: datetime | None,
    detected_at: datetime,
) -> None:
    session.add(
        KnowledgeArticleChange(
            knowledge_article_id=article.id,
            ingestion_run_id=run.id if run is not None else None,
            change_type=change_type,
            previous_content_hash=previous_hash,
            new_content_hash=new_hash,
            previous_source_updated_at=previous_source_updated_at,
            new_source_updated_at=new_source_updated_at,
            fields_changed=list(fields),
            description=_description(change_type, fields),
            metadata_={"source": SOURCE_TYPE},
            detected_at=detected_at,
        )
    )


def _apply_article(
    session: Session,
    article: IcuArticle,
    data_dir: Path,
    run: IngestionRun | None,
    *,
    now: datetime,
    dry_run: bool,
) -> str:
    existing = _find_existing(session, article)
    decision = compare_article(existing, article)
    if dry_run:
        return decision.change_type
    if decision.change_type == "skipped":
        assert existing is not None
        existing.last_checked_at = now
        existing.crawled_at = article.crawled_at or existing.crawled_at
        existing.metadata_ = _metadata(article, data_dir)
        return "skipped"

    if existing is None:
        existing = KnowledgeArticle(
            source_type=SOURCE_TYPE,
            external_id=article.external_id,
            title=article.title,
            url=article.url,
            category=article.category,
            folder=article.folder,
            raw_html=article.raw_html,
            markdown=article.markdown,
            content_hash=article.content_hash,
            source_updated_at=article.source_updated_at,
            crawled_at=article.crawled_at or now,
            first_seen_at=now,
            last_checked_at=now,
            last_changed_at=now,
            metadata_=_metadata(article, data_dir),
        )
        session.add(existing)
        session.flush()
        _add_change(
            session,
            existing,
            run,
            change_type="created",
            fields=decision.fields_changed,
            previous_hash=None,
            new_hash=article.content_hash,
            previous_source_updated_at=None,
            new_source_updated_at=article.source_updated_at,
            detected_at=now,
        )
        return "created"

    previous_hash = existing.content_hash
    previous_source_updated_at = existing.source_updated_at
    existing.source_type = SOURCE_TYPE
    existing.external_id = article.external_id
    existing.title = article.title
    existing.url = article.url
    existing.category = article.category
    existing.folder = article.folder
    existing.raw_html = article.raw_html
    existing.markdown = article.markdown
    existing.content_hash = article.content_hash
    existing.source_updated_at = article.source_updated_at
    existing.crawled_at = article.crawled_at or now
    existing.last_checked_at = now
    existing.metadata_ = _metadata(article, data_dir)
    if decision.change_type == "updated":
        existing.last_changed_at = now
    _add_change(
        session,
        existing,
        run,
        change_type=decision.change_type,
        fields=decision.fields_changed,
        previous_hash=previous_hash,
        new_hash=article.content_hash,
        previous_source_updated_at=previous_source_updated_at,
        new_source_updated_at=article.source_updated_at,
        detected_at=now,
    )
    return decision.change_type


def _mark_missing(
    session: Session,
    data_dir: Path,
    run: IngestionRun,
    seen_ids: set[str],
    *,
    now: datetime,
) -> int:
    changed = 0
    rows = session.scalars(
        select(KnowledgeArticle).where(KnowledgeArticle.source_type == SOURCE_TYPE)
    ).all()
    for article in rows:
        if not article.external_id or article.external_id in seen_ids:
            continue
        metadata = dict(article.metadata_ or {})
        if metadata.get("missing_from_source"):
            continue
        metadata["missing_from_source"] = True
        metadata["first_missing_at"] = now.isoformat()
        article.metadata_ = metadata
        article.last_checked_at = now
        _add_change(
            session,
            article,
            run,
            change_type="missing",
            fields=("missing_from_source",),
            previous_hash=article.content_hash,
            new_hash=article.content_hash,
            previous_source_updated_at=article.source_updated_at,
            new_source_updated_at=article.source_updated_at,
            detected_at=now,
        )
        changed += 1
    return changed


def sync_icu(
    session: Session,
    data_dir: Path,
    *,
    dry_run: bool = False,
    max_articles: int | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> SyncSummary:
    """Synchronise the current ICU archive with per-article transaction boundaries."""

    del force  # The adapter always evaluates every current file; it is kept for CLI compatibility.
    data_dir = data_dir.expanduser().resolve()
    event_time = now or utc_now()
    if not data_dir.is_dir():
        raise NotADirectoryError(f"Expected an ICU data directory, got: {data_dir}")

    run: IngestionRun | None = None
    if not dry_run:
        run = IngestionRun(
            source_type=SOURCE_TYPE,
            source_path=str(data_dir),
            status="running",
            metadata_={
                "crawler_repo": "icu-crawler",
                "data_directory": str(data_dir),
                "articles_discovered": 0,
                "attachments_seen": 0,
            },
        )
        session.add(run)
        session.commit()
        session.refresh(run)

    seen_ids: set[str] = set()
    records_seen = records_created = records_updated = records_skipped = records_failed = 0
    paths: list[Path] = []

    try:
        paths = list(iter_metadata_paths(data_dir))
        if max_articles is not None:
            paths = paths[: max(0, max_articles)]
        for metadata_path in paths:
            records_seen += 1
            if run is not None:
                run.records_seen = records_seen
            try:
                article = load_article(metadata_path, data_dir, now=event_time)
                if article.external_id in seen_ids:
                    raise ValueError(f"duplicate ICU article external ID: {article.external_id}")
                seen_ids.add(article.external_id)
                if run is None:
                    # Dry-run comparison intentionally performs no writes.
                    decision = compare_article(_find_existing(session, article), article)
                    outcome = decision.change_type
                else:
                    with session.begin_nested():
                        outcome = _apply_article(
                            session, article, data_dir, run, now=event_time, dry_run=False
                        )
                if outcome == "created":
                    records_created += 1
                elif outcome in {"updated", "restored"}:
                    records_updated += 1
                else:
                    records_skipped += 1
                logger.info("ICU article %s: %s", article.external_id, outcome)
            except Exception as error:
                records_failed += 1
                if run is not None:
                    run.records_failed = records_failed
                    run.error_log = [
                        *(run.error_log or []),
                        {
                            "path": str(metadata_path),
                            "error_type": type(error).__name__,
                            "message": str(error),
                        },
                    ]
                logger.exception("Could not sync ICU article metadata %s", metadata_path)
            if run is not None:
                run.records_created = records_created
                run.records_updated = records_updated
                run.records_skipped = records_skipped
                session.commit()
                session.refresh(run)

        if run is not None and max_articles is None and records_failed == 0:
            with session.begin_nested():
                records_updated += _mark_missing(session, data_dir, run, seen_ids, now=event_time)
            run.records_updated = records_updated
            session.commit()

        status = "completed_dry_run" if dry_run else ("completed" if records_failed == 0 else "completed_with_errors")
        if run is not None:
            run.records_seen = records_seen
            run.records_created = records_created
            run.records_updated = records_updated
            run.records_skipped = records_skipped
            run.records_failed = records_failed
            run.status = status
            run.finished_at = utc_now()
            run.metadata_ = {
                **(run.metadata_ or {}),
                "articles_discovered": len(paths),
                "attachments_seen": len(list((data_dir / "attachments").iterdir()))
                if (data_dir / "attachments").is_dir()
                else 0,
                "max_articles": max_articles,
            }
            session.commit()
            session.refresh(run)
        return SyncSummary(
            run_id=str(run.id) if run is not None else None,
            status=status,
            records_seen=records_seen,
            records_created=records_created,
            records_updated=records_updated,
            records_skipped=records_skipped,
            records_failed=records_failed,
        )
    except Exception as error:
        if run is not None:
            session.rollback()
            run.status = "failed"
            run.finished_at = utc_now()
            run.error_log = [
                *(run.error_log or []),
                {"error_type": type(error).__name__, "message": str(error)},
            ]
            session.add(run)
            session.commit()
        raise
