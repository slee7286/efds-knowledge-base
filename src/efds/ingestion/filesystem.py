"""Safe, versioned synchronization of the local EFDS OneDrive tree."""

from __future__ import annotations

import fnmatch
import logging
import os
import time
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import Document, DocumentSourceChange, DocumentVersion, IngestionRun

from .extractors import SUPPORTED_EXTENSIONS, extract_file, infer_document_type, infer_mime_type
from .hashing import sha256_file

logger = logging.getLogger(__name__)
SOURCE_TYPE = "onedrive_filesystem"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "filesystem_ingestion.toml"
DEFAULT_EXCLUDED_DIRECTORIES = {
    ".git", ".github", ".next", "node_modules", "dist", "build", "coverage",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "12_technology/efds-site", "12_technology/efds-knowledge-base", "12_technology/icu-crawler",
}
DEFAULT_EXCLUDED_PATTERNS = {
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.sqlite", "*.sqlite3",
    "*.db", "*.tmp", "*.part", "*.crdownload", "~$*",
}
SENSITIVE_PATTERNS = {".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx"}
WINDOWS_RECALL_ON_DATA_ACCESS = 0x40000000


@dataclass(frozen=True, slots=True)
class FilesystemConfig:
    excluded_directories: frozenset[str] = frozenset(DEFAULT_EXCLUDED_DIRECTORIES)
    excluded_file_patterns: frozenset[str] = frozenset(DEFAULT_EXCLUDED_PATTERNS)

    @classmethod
    def load(cls, path: Path | None = None) -> "FilesystemConfig":
        path = path or DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        with path.open("rb") as file:
            data = tomllib.load(file)
        directories = set(DEFAULT_EXCLUDED_DIRECTORIES)
        directories.update(_normal_path(str(value)) for value in data.get("excluded_directories", []))
        patterns = set(DEFAULT_EXCLUDED_PATTERNS)
        patterns.update(str(value).casefold() for value in data.get("excluded_file_patterns", []))
        return cls(frozenset(directories), frozenset(patterns))


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    path: Path
    relative_path: str
    normalized_relative_path: str
    source_area: str | None
    stat: os.stat_result | None
    unavailable: bool = False


@dataclass(slots=True)
class FilesystemSummary:
    run_id: str | None
    status: str
    files_discovered: int = 0
    files_supported: int = 0
    files_unsupported: int = 0
    files_created: int = 0
    files_updated: int = 0
    files_unchanged: int = 0
    files_missing: int = 0
    files_restored: int = 0
    files_renamed: int = 0
    files_moved: int = 0
    versions_created: int = 0
    duplicates_detected: int = 0
    extraction_failures: int = 0
    unavailable: int = 0
    excluded: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "source_type": SOURCE_TYPE,
            "files_discovered": self.files_discovered,
            "files_supported": self.files_supported,
            "files_unsupported": self.files_unsupported,
            "files_created": self.files_created,
            "files_updated": self.files_updated,
            "files_unchanged": self.files_unchanged,
            "files_missing": self.files_missing,
            "files_restored": self.files_restored,
            "files_renamed": self.files_renamed,
            "files_moved": self.files_moved,
            "versions_created": self.versions_created,
            "duplicates_detected": self.duplicates_detected,
            "extraction_failures": self.extraction_failures,
            "unavailable": self.unavailable,
            "excluded": self.excluded,
            "errors": self.errors,
        }


def _normal_path(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\\", "/").strip("/")).casefold()


def normalize_relative_path(root: Path, path: Path) -> tuple[str, str]:
    relative = path.relative_to(root).as_posix()
    return relative, _normal_path(relative)


def _matches_pattern(value: str, patterns: Iterable[str]) -> bool:
    name = Path(value).name.casefold()
    normalized = _normal_path(value)
    return any(fnmatch.fnmatch(name, pattern.casefold()) or fnmatch.fnmatch(normalized, pattern.casefold()) for pattern in patterns)


def _is_cloud_only(stat: os.stat_result) -> bool:
    attributes = getattr(stat, "st_file_attributes", 0)
    return bool(attributes & WINDOWS_RECALL_ON_DATA_ACCESS)


def _excluded_directory(relative: str, name: str, config: FilesystemConfig) -> bool:
    normalized = _normal_path(relative)
    return name.casefold() in config.excluded_directories or any(
        normalized == directory or normalized.startswith(f"{directory}/")
        for directory in config.excluded_directories
    )


def discover_files(
    root: Path,
    config: FilesystemConfig,
    *,
    area: str | None = None,
    includes: tuple[str, ...] = (),
    extra_excludes: tuple[str, ...] = (),
    limit: int | None = None,
) -> tuple[list[DiscoveredFile], int, list[dict[str, str]], bool]:
    """Discover files without reading contents; return files, exclusions, errors, completeness."""

    root = root.resolve()
    discovered: list[DiscoveredFile] = []
    excluded = 0
    errors: list[dict[str, str]] = []
    complete = True
    config = FilesystemConfig(
        frozenset(set(config.excluded_directories) | {_normal_path(value) for value in extra_excludes}),
        config.excluded_file_patterns,
    )

    def onerror(error: OSError) -> None:
        nonlocal complete
        complete = False
        errors.append({"path": str(error.filename or root), "error_type": type(error).__name__, "message": str(error)})

    for current_root, directories, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        current = Path(current_root)
        relative_root = "" if current == root else current.relative_to(root).as_posix()
        if area and relative_root:
            top = relative_root.split("/", 1)[0]
            if _normal_path(top) != _normal_path(area):
                directories[:] = [] if not _normal_path(area).startswith(_normal_path(top)) else directories
                if directories == []:
                    continue
        kept_directories: list[str] = []
        for name in sorted(directories):
            relative = f"{relative_root}/{name}".strip("/")
            if name.startswith(".") or _excluded_directory(relative, name, config):
                excluded += 1
                continue
            kept_directories.append(name)
        directories[:] = kept_directories
        for filename in sorted(filenames, key=str.casefold):
            relative = f"{relative_root}/{filename}".strip("/")
            normalized = _normal_path(relative)
            if area and (not relative_root or _normal_path(relative_root.split("/", 1)[0]) != _normal_path(area)):
                continue
            if filename.startswith(".") or _matches_pattern(relative, config.excluded_file_patterns):
                excluded += 1
                continue
            if includes and not any(fnmatch.fnmatch(normalized, _normal_path(pattern)) or normalized.startswith(f"{_normal_path(pattern).rstrip('*')}") for pattern in includes):
                continue
            try:
                stat = (current / filename).stat()
                unavailable = _is_cloud_only(stat)
                if unavailable:
                    complete = True
            except (OSError, PermissionError) as error:
                stat = None
                unavailable = True
                complete = False
                errors.append({"path": relative, "error_type": type(error).__name__, "message": str(error)})
            top_level = relative.split("/", 1)[0] if relative else None
            discovered.append(DiscoveredFile(current / filename, relative, normalized, _normal_path(top_level) if top_level else None, stat, unavailable))
            if limit is not None and len(discovered) >= limit:
                return discovered, excluded, errors, False
    return discovered, excluded, errors, complete


def _source_area(relative_path: str) -> str | None:
    value = relative_path.split("/", 1)[0] if relative_path else ""
    return _normal_path(value) or None


def _safe_error(path: str, error: Exception) -> dict[str, str]:
    return {"path": path, "error_type": type(error).__name__, "message": str(error)}


def _metadata_for(path: Path, stat: os.stat_result | None) -> tuple[datetime | None, datetime | None, int | None]:
    if stat is None:
        return None, None, None
    return datetime.fromtimestamp(stat.st_ctime, timezone.utc), datetime.fromtimestamp(stat.st_mtime, timezone.utc), stat.st_size


def _change(session: Session, document: Document, change_type: str, run_id: Any, *, previous_path: str | None = None, new_path: str | None = None, previous_hash: str | None = None, new_hash: str | None = None, previous_modified: datetime | None = None, new_modified: datetime | None = None, previous_size: int | None = None, new_size: int | None = None) -> None:
    session.add(DocumentSourceChange(document_id=document.id, change_type=change_type, previous_path=previous_path, new_path=new_path, previous_content_hash=previous_hash, new_content_hash=new_hash, previous_modified_at=previous_modified, new_modified_at=new_modified, previous_size=previous_size, new_size=new_size, ingestion_run_id=run_id))


def _extract(path: Path, content_hash: str, mime_type: str, modified_at: datetime | None) -> tuple[str, str | None, dict[str, Any], str | None]:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported", None, {}, None
    try:
        extracted = extract_file(path)
        return "extracted", extracted.text, extracted.metadata, None
    except (OSError, PermissionError) as error:
        return "unavailable", None, {}, str(error)
    except Exception as error:
        return "failed", None, {}, str(error)


def sync_filesystem(
    session: Session,
    root: Path,
    *,
    config: FilesystemConfig | None = None,
    area: str | None = None,
    includes: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
    limit: int | None = None,
    dry_run: bool = False,
    full: bool = False,
) -> FilesystemSummary:
    """Reconcile one local root; missing detection occurs only on complete full scans."""

    root = root.expanduser().resolve()
    config = config or FilesystemConfig.load()
    run = IngestionRun(source_type=SOURCE_TYPE, source_path=str(root), status="running", metadata_={"dry_run": dry_run, "full": full, "area": area})
    session.add(run)
    session.commit()
    session.refresh(run)
    summary = FilesystemSummary(str(run.id), "running")
    try:
        if not root.exists() or not root.is_dir():
            raise NotADirectoryError(f"Filesystem root is not a directory: {root}")
        discovered, summary.excluded, discovery_errors, complete = discover_files(root, config, area=area, includes=includes, extra_excludes=excludes, limit=limit)
        summary.errors.extend(discovery_errors)
        summary.files_discovered = len(discovered)
        source_root = str(root)
        existing = {document.normalized_relative_path: document for document in session.scalars(select(Document).where(Document.source_type == SOURCE_TYPE, Document.source_root == source_root)).all() if document.normalized_relative_path}
        observed: set[str] = set()
        new_documents: list[tuple[Document, DiscoveredFile, str | None]] = []
        now = datetime.now(timezone.utc)

        for item in discovered:
            observed.add(item.normalized_relative_path)
            document = existing.get(item.normalized_relative_path)
            if document is None:
                if dry_run:
                    if item.unavailable:
                        summary.unavailable += 1
                    elif item.path.suffix.lower() in SUPPORTED_EXTENSIONS:
                        summary.files_supported += 1
                        try:
                            content_hash = sha256_file(item.path)
                            status, _raw_text, _metadata, extraction_error = _extract(item.path, content_hash, infer_mime_type(item.path), datetime.fromtimestamp(item.stat.st_mtime, timezone.utc) if item.stat else None)
                            summary.extraction_failures += int(status == "failed")
                            summary.unavailable += int(status == "unavailable")
                            summary.errors.extend([_safe_error(item.relative_path, RuntimeError(extraction_error))] if extraction_error else [])
                        except (OSError, PermissionError) as error:
                            summary.unavailable += 1
                            summary.errors.append(_safe_error(item.relative_path, error))
                    else:
                        summary.files_unsupported += 1
                    summary.files_created += 1
                    continue
                document = Document(title=item.path.stem or item.path.name, document_type=infer_document_type(item.path), source_type=SOURCE_TYPE, source_root=source_root, relative_path=item.relative_path, normalized_relative_path=item.normalized_relative_path, source_area=item.source_area, file_path=None, mime_type=infer_mime_type(item.path), first_seen_at=now, last_seen_at=now, last_synced_at=now, metadata_={"relative_path": item.relative_path})
                session.add(document)
                session.flush()
                new_documents.append((document, item, None))
            if item.path.suffix.lower() in SUPPORTED_EXTENSIONS:
                summary.files_supported += 1
            else:
                summary.files_unsupported += 1
            if not dry_run and not item.unavailable and item.stat is not None and document.file_size_bytes == item.stat.st_size and document.filesystem_modified_at and document.filesystem_modified_at == datetime.fromtimestamp(item.stat.st_mtime, timezone.utc) and not document.is_missing:
                document.last_seen_at = now
                document.last_synced_at = now
                summary.files_unchanged += 1
                continue
            if item.unavailable:
                summary.unavailable += 1
                if document.extraction_status != "unavailable":
                    document.extraction_status = "unavailable"
                    document.is_unavailable = True
                    document.last_seen_at = now
                continue
            if item.stat is None:
                continue
            try:
                content_hash = sha256_file(item.path)
            except (OSError, PermissionError) as error:
                document.is_unavailable = True
                document.extraction_status = "unavailable"
                summary.unavailable += 1
                summary.errors.append(_safe_error(item.relative_path, error))
                continue
            created_at, modified_at, size = _metadata_for(item.path, item.stat)
            previous_hash = document.content_hash
            previous_path = document.relative_path
            previous_modified = document.filesystem_modified_at
            previous_size = document.file_size_bytes
            was_missing = document.is_missing
            if document.content_hash == content_hash and document.current_version_id is not None and not document.is_missing:
                document.last_seen_at = now
                document.last_synced_at = now
                document.is_unavailable = False
                summary.files_unchanged += 1
                continue
            status, raw_text, extracted_metadata, extraction_error = _extract(item.path, content_hash, infer_mime_type(item.path), modified_at)
            if dry_run:
                summary.files_updated += int(document.content_hash is not None)
                summary.files_created += int(document.content_hash is None)
                continue
            document.title = str(extracted_metadata.get("subject") or item.path.stem or item.path.name).strip()
            document.document_type = infer_document_type(item.path)
            document.file_path = None
            document.relative_path = item.relative_path
            document.normalized_relative_path = item.normalized_relative_path
            document.source_area = item.source_area
            document.mime_type = infer_mime_type(item.path)
            document.content_hash = content_hash
            document.file_size_bytes = size
            document.filesystem_created_at = created_at
            document.filesystem_modified_at = modified_at
            document.last_seen_at = now
            document.last_synced_at = now
            document.last_changed_at = now if previous_hash != content_hash or previous_path != item.relative_path else document.last_changed_at
            document.is_missing = False
            document.is_unavailable = False
            document.first_missing_at = None
            document.extraction_status = status
            document.extraction_error = extraction_error
            document.raw_text = raw_text
            document.metadata_ = {**(document.metadata_ or {}), "relative_path": item.relative_path, "extension": item.path.suffix.lower(), **extracted_metadata}
            version = session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.content_hash == content_hash))
            if version is None:
                version = DocumentVersion(document_id=document.id, content_hash=content_hash, raw_text=raw_text, extraction_status=status, extraction_error=extraction_error, mime_type=document.mime_type, source_modified_at=modified_at, file_size_bytes=size, metadata_=extracted_metadata)
                session.add(version)
                session.flush()
                document.current_version_id = version.id
                summary.versions_created += 1
            else:
                document.current_version_id = version.id
            if previous_hash is None:
                _change(session, document, "created", run.id, new_path=item.relative_path, new_hash=content_hash, new_modified=modified_at, new_size=size)
                summary.files_created += 1
            elif was_missing:
                _change(session, document, "restored", run.id, previous_path=previous_path, new_path=item.relative_path, previous_hash=previous_hash, new_hash=content_hash, previous_modified=previous_modified, new_modified=modified_at, previous_size=previous_size, new_size=size)
                summary.files_restored += 1
            elif previous_path != item.relative_path:
                _change(session, document, "moved" if Path(previous_path or "").parent != Path(item.relative_path).parent else "renamed", run.id, previous_path=previous_path, new_path=item.relative_path, previous_hash=previous_hash, new_hash=content_hash, previous_modified=previous_modified, new_modified=modified_at, previous_size=previous_size, new_size=size)
                summary.files_moved += int(Path(previous_path or "").parent != Path(item.relative_path).parent)
                summary.files_renamed += int(Path(previous_path or "").parent == Path(item.relative_path).parent)
            else:
                _change(session, document, "content_updated", run.id, previous_path=previous_path, new_path=item.relative_path, previous_hash=previous_hash, new_hash=content_hash, previous_modified=previous_modified, new_modified=modified_at, previous_size=previous_size, new_size=size)
                summary.files_updated += 1
            if extraction_error:
                summary.extraction_failures += 1
            if session.scalar(select(Document.id).where(Document.content_hash == content_hash, Document.id != document.id)):
                summary.duplicates_detected += 1
            session.commit()
            session.refresh(run)

        # High-confidence rename/move: exactly one new path and one previously
        # missing path share the same current hash. Ambiguous matches remain
        # separate created/missing records.
        if not dry_run and complete and full and not area and not includes and not limit:
            missing = [document for key, document in existing.items() if key not in observed and not document.is_missing]
            new_by_hash: dict[str, list[Document]] = {}
            for document, _item, _ in new_documents:
                if document.content_hash:
                    new_by_hash.setdefault(document.content_hash, []).append(document)
            missing_by_hash: dict[str, list[Document]] = {}
            for document in missing:
                if document.content_hash:
                    missing_by_hash.setdefault(document.content_hash, []).append(document)
            for content_hash, new_items in new_by_hash.items():
                old_items = missing_by_hash.get(content_hash, [])
                if len(new_items) != 1 or len(old_items) != 1:
                    continue
                new_document = new_items[0]
                old_document = old_items[0]
                old_path = old_document.relative_path
                new_path = new_document.relative_path
                old_document.title = new_document.title
                old_document.document_type = new_document.document_type
                old_document.relative_path = new_path
                old_document.normalized_relative_path = new_document.normalized_relative_path
                old_document.source_area = new_document.source_area
                old_document.file_path = None
                old_document.mime_type = new_document.mime_type
                old_document.file_size_bytes = new_document.file_size_bytes
                old_document.filesystem_modified_at = new_document.filesystem_modified_at
                old_document.last_seen_at = now
                old_document.last_synced_at = now
                old_document.last_changed_at = now
                old_document.is_missing = False
                old_document.first_missing_at = None
                old_document.extraction_status = new_document.extraction_status
                old_document.extraction_error = new_document.extraction_error
                old_document.raw_text = new_document.raw_text
                old_document.current_version_id = old_document.current_version_id
                _change(session, old_document, "moved" if Path(old_path or "").parent != Path(new_path or "").parent else "renamed", run.id, previous_path=old_path, new_path=new_path, previous_hash=content_hash, new_hash=content_hash, previous_size=old_document.file_size_bytes, new_size=new_document.file_size_bytes)
                session.delete(new_document)
                missing.remove(old_document)
                summary.files_created = max(summary.files_created - 1, 0)
                summary.versions_created = max(summary.versions_created - 1, 0)
                if Path(old_path or "").parent != Path(new_path or "").parent:
                    summary.files_moved += 1
                else:
                    summary.files_renamed += 1
            for document in missing:
                document.is_missing = True
                document.first_missing_at = document.first_missing_at or now
                document.last_synced_at = now
                _change(session, document, "missing", run.id, previous_path=document.relative_path, previous_hash=document.content_hash, previous_modified=document.filesystem_modified_at, previous_size=document.file_size_bytes)
                summary.files_missing += 1
            session.commit()
        summary.status = "completed_dry_run" if dry_run else "completed" if complete and not summary.errors else "completed_with_warnings"
        run.status = summary.status
        run.finished_at = datetime.now(timezone.utc)
        run.records_seen = summary.files_discovered
        run.records_created = summary.files_created
        run.records_updated = summary.files_updated + summary.files_restored + summary.files_renamed + summary.files_moved
        run.records_skipped = summary.files_unchanged
        run.records_failed = summary.extraction_failures + summary.unavailable + len(summary.errors)
        run.error_log = summary.errors
        run.metadata_ = {**(run.metadata_ or {}), **summary.metadata(), "complete_discovery": complete}
        session.commit()
        return summary
    except Exception as error:
        session.rollback()
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.error_log = [*run.error_log, _safe_error("<filesystem sync>", error)]
        session.add(run)
        session.commit()
        raise


def watch_filesystem(*args: Any, interval_seconds: float = 5.0, **kwargs: Any) -> None:
    """Polling watcher for Windows/OneDrive; periodic full reconciliation remains authoritative."""

    from efds.db.session import session_scope

    root = Path(args[0]).expanduser().resolve() if args else Path(kwargs.get("root", ".")).expanduser().resolve()
    last_snapshot: tuple[tuple[str, int, int], ...] | None = None
    logger.info("Watching filesystem; polling interval=%ss. Press Ctrl+C to stop.", interval_seconds)
    while True:
        config = kwargs.get("config") or FilesystemConfig.load()
        discovered, _excluded, _errors, _complete = discover_files(root, config, area=kwargs.get("area"), includes=kwargs.get("includes", ()), extra_excludes=kwargs.get("excludes", ()), limit=kwargs.get("limit"))
        snapshot = tuple(sorted((item.normalized_relative_path, item.stat.st_size if item.stat else -1, item.stat.st_mtime_ns if item.stat else -1) for item in discovered))
        if snapshot != last_snapshot:
            # Wait for one quiet interval so Office/OneDrive temporary writes
            # settle before extraction begins.
            time.sleep(interval_seconds)
            settled, _excluded, _errors, _complete = discover_files(root, config, area=kwargs.get("area"), includes=kwargs.get("includes", ()), extra_excludes=kwargs.get("excludes", ()), limit=kwargs.get("limit"))
            settled_snapshot = tuple(sorted((item.normalized_relative_path, item.stat.st_size if item.stat else -1, item.stat.st_mtime_ns if item.stat else -1) for item in settled))
            if settled_snapshot != snapshot:
                continue
            with session_scope() as session:
                sync_filesystem(session, *args, full=False, **kwargs)
            last_snapshot = snapshot
        time.sleep(interval_seconds)
