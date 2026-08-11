"""Recursive filesystem ingestion into the ``documents`` table."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import Document, IngestionRun

from .extractors import SUPPORTED_EXTENSIONS, extract_file, infer_document_type, infer_mime_type
from .hashing import sha256_file

logger = logging.getLogger(__name__)

_IGNORED_NAMES = {"System Volume Information", "$RECYCLE.BIN"}


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    """Counters returned after a folder ingestion completes."""

    run_id: str
    status: str
    records_seen: int
    records_created: int
    records_updated: int
    records_skipped: int
    records_failed: int


def _ignored_name(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$") or name in _IGNORED_NAMES


def iter_supported_files(folder: Path) -> Iterator[Path]:
    """Yield supported files recursively, ignoring common hidden/temp entries."""

    for root, directories, filenames in os.walk(folder, followlinks=False):
        directories[:] = sorted(name for name in directories if not _ignored_name(name))
        for filename in sorted(filenames):
            if _ignored_name(filename):
                continue
            path = Path(root) / filename
            if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
                yield path


def _error_detail(path: Path, error: Exception) -> dict[str, str]:
    return {
        "path": str(path),
        "error_type": type(error).__name__,
        "message": str(error),
    }


def _summary(run: IngestionRun) -> IngestionSummary:
    return IngestionSummary(
        run_id=str(run.id),
        status=str(run.status),
        records_seen=run.records_seen,
        records_created=run.records_created,
        records_updated=run.records_updated,
        records_skipped=run.records_skipped,
        records_failed=run.records_failed,
    )


def ingest_folder(
    session: Session,
    folder: Path,
    *,
    academic_year: str | None = None,
    source_type: str = "filesystem",
    dry_run: bool = False,
) -> IngestionSummary:
    """Ingest supported files from ``folder`` with content-hash deduplication.

    The run row is committed at the beginning and after each file, so a long
    run leaves useful progress information if an individual file fails.
    """

    folder = folder.expanduser()
    run = IngestionRun(
        source_type=source_type,
        source_path=str(folder.resolve()),
        status="running",
        records_seen=0,
        records_created=0,
        records_updated=0,
        records_skipped=0,
        records_failed=0,
        error_log=[],
        metadata_={"dry_run": dry_run} if dry_run else {},
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        if not folder.exists():
            raise FileNotFoundError(f"Folder does not exist: {folder}")
        if not folder.is_dir():
            raise NotADirectoryError(f"Expected a folder, got: {folder}")

        for path in iter_supported_files(folder):
            run.records_seen += 1
            try:
                content_hash = sha256_file(path)
                existing_id = session.scalar(
                    select(Document.id).where(Document.content_hash == content_hash)
                )
                if existing_id is not None:
                    run.records_skipped += 1
                    logger.info("Skipping duplicate document: %s", path)
                elif dry_run:
                    run.records_skipped += 1
                    logger.info("Dry run; would ingest: %s", path)
                else:
                    extracted = extract_file(path)
                    metadata: dict[str, Any] = {
                        "extension": path.suffix.lower(),
                        "original_filename": path.name,
                        **extracted.metadata,
                    }
                    title = str(metadata.get("subject") or path.stem).strip() or path.name
                    session.add(
                        Document(
                            title=title,
                            document_type=infer_document_type(path),
                            source_type=source_type,
                            file_path=str(path.resolve()),
                            mime_type=infer_mime_type(path),
                            academic_year=academic_year,
                            raw_text=extracted.text,
                            content_hash=content_hash,
                            file_size_bytes=path.stat().st_size,
                            metadata_=metadata,
                        )
                    )
                    run.records_created += 1
                    logger.info("Ingested document: %s", path)
            except Exception as error:
                run.records_failed += 1
                run.error_log.append(_error_detail(path, error))
                logger.exception("Could not ingest %s", path)
            session.commit()
            session.refresh(run)

        run.status = "completed_dry_run" if dry_run else "completed"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return _summary(run)
    except Exception as error:
        session.rollback()
        try:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error_log = [*run.error_log, {"error_type": type(error).__name__, "message": str(error)}]
            session.add(run)
            session.commit()
        except Exception:
            session.rollback()
        raise
