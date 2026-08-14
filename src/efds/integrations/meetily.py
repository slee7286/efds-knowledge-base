"""Read-only Meetily adapter and idempotent EFDS meeting synchronizer.

Meetily's current Windows installation stores its local database at
``%APPDATA%/com.meetily.ai/meeting_minutes.sqlite``.  The adapter never writes
to that database.  A simple export-directory adapter is also supported for
portable/staged imports and future Meetily versions.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import fields
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from efds.db.models import IngestionRun, Meeting, MeetingArtifact, MeetingSourceChange, MeetingTranscriptSegment


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _milliseconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 1000) if number < 100000 else round(number)


def _json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class MeetilySegment:
    sequence: int
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MeetilyMeeting:
    external_id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    folder_path: str | None = None
    transcript: str | None = None
    transcript_format: str | None = None
    transcript_segments: list[MeetilySegment] = field(default_factory=list)
    summary: str | None = None
    summary_format: str | None = None
    summary_template: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _segment_from_mapping(value: dict[str, Any], sequence: int) -> MeetilySegment | None:
    text = value.get("text") or value.get("transcript") or value.get("content") or value.get("utterance")
    if not isinstance(text, str) or not text.strip():
        return None
    speaker = value.get("speaker") or value.get("speaker_name") or value.get("speakerLabel")
    start_is_ms = "start_ms" in value
    end_is_ms = "end_ms" in value
    start = value.get("start_ms", value.get("start", value.get("startTime")))
    end = value.get("end_ms", value.get("end", value.get("endTime")))
    start_ms = int(float(start)) if start_is_ms and start is not None else _milliseconds(start)
    end_ms = int(float(end)) if end_is_ms and end is not None else _milliseconds(end)
    return MeetilySegment(sequence, text.strip(), start_ms, end_ms, str(speaker) if speaker else None, value)


def parse_transcript_segments(value: str | None) -> list[MeetilySegment]:
    """Parse structured JSON or common timestamped text without inventing speakers."""

    if not value or not value.strip():
        return []
    parsed = _json(value)
    candidates: Any = parsed
    if isinstance(parsed, dict):
        candidates = parsed.get("segments") or parsed.get("transcript") or parsed.get("chunks") or []
    if isinstance(candidates, list):
        segments: list[MeetilySegment] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            segment = _segment_from_mapping(candidate, index)
            if segment is not None:
                segments.append(segment)
        if segments:
            return segments
    segments: list[MeetilySegment] = []
    timestamped = re.compile(r"^\s*(?:\[)?(?P<time>\d{1,2}:\d{2}(?::\d{2})?)(?:\])?\s*(?P<body>.*)$")
    for index, line in enumerate(value.replace("\r\n", "\n").splitlines()):
        text = line.strip()
        if not text:
            continue
        match = timestamped.match(text)
        start = None
        body = text
        if match:
            parts = [int(part) for part in match.group("time").split(":")]
            seconds = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
            start = seconds * 1000
            body = match.group("body").strip()
        speaker = None
        speaker_match = re.match(r"^(?P<speaker>[^:]{1,80}):\s+(?P<text>.+)$", body)
        if speaker_match and start is not None:
            speaker = speaker_match.group("speaker").strip()
            body = speaker_match.group("text").strip()
        if body:
            segments.append(MeetilySegment(index, body, start, None, speaker))
    return segments


class MeetilyReader:
    """Read Meetily's SQLite database in read-only mode."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def _connection(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise FileNotFoundError(f"Meetily database not found: {self.path}")
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def meetings(self) -> list[MeetilyMeeting]:
        connection = self._connection()
        try:
            rows = connection.execute("SELECT id, title, created_at, updated_at, folder_path FROM meetings ORDER BY created_at, id").fetchall()
            output: list[MeetilyMeeting] = []
            for row in rows:
                meeting = MeetilyMeeting(str(row["id"]), str(row["title"] or "Untitled meeting"), _datetime(row["created_at"]), _datetime(row["updated_at"]), row["folder_path"])
                transcript_rows = connection.execute("SELECT id, transcript, timestamp, audio_start_time, audio_end_time, duration, speaker FROM transcripts WHERE meeting_id = ? ORDER BY timestamp, id", (meeting.external_id,)).fetchall()
                chunk = connection.execute("SELECT transcript_text, model, model_name, created_at FROM transcript_chunks WHERE meeting_id = ?", (meeting.external_id,)).fetchone()
                transcript_parts = [str(item["transcript"] or "") for item in transcript_rows if item["transcript"]]
                if chunk and chunk["transcript_text"]:
                    transcript_parts = [str(chunk["transcript_text"])]
                meeting.transcript = "\n\n".join(part for part in transcript_parts if part).strip() or None
                meeting.transcript_format = "text"
                if meeting.transcript:
                    meeting.transcript_segments = parse_transcript_segments(meeting.transcript)
                    if not meeting.transcript_segments and transcript_rows:
                        meeting.transcript_segments = [MeetilySegment(index, str(item["transcript"]), _milliseconds(item["audio_start_time"]), _milliseconds(item["audio_end_time"]), item["speaker"]) for index, item in enumerate(transcript_rows) if item["transcript"]]
                process = connection.execute("SELECT result, metadata, updated_at FROM summary_processes WHERE meeting_id = ? ORDER BY updated_at DESC LIMIT 1", (meeting.external_id,)).fetchone()
                if process and process["result"]:
                    meeting.summary = _render_summary(process["result"])
                    meeting.summary_format = "json" if _json(process["result"]) is not None else "text"
                    metadata = _json(process["metadata"])
                    if isinstance(metadata, dict):
                        meeting.summary_template = metadata.get("template") or metadata.get("template_name")
                notes = connection.execute("SELECT notes_markdown, notes_json, updated_at FROM meeting_notes WHERE meeting_id = ?", (meeting.external_id,)).fetchone()
                if notes and notes["notes_markdown"]:
                    meeting.notes = str(notes["notes_markdown"])
                meeting.metadata = {"source_database": str(self.path), "transcript_model": chunk["model_name"] if chunk else None}
                output.append(meeting)
            return output
        finally:
            connection.close()


def _render_summary(value: str) -> str:
    parsed = _json(value)
    if parsed is None:
        return value
    if isinstance(parsed, str):
        return parsed
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def read_export_directory(path: Path) -> list[MeetilyMeeting]:
    """Read a conservative export convention without reverse-engineering Meetily."""

    if not path.is_dir():
        raise NotADirectoryError(f"Meetily export directory not found: {path}")
    output: list[MeetilyMeeting] = []
    for directory in sorted(item for item in path.iterdir() if item.is_dir()):
        manifest_path = directory / "meeting.json"
        manifest_error = None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            manifest = {}
            manifest_error = str(error)[:300]
        external_id = str(manifest.get("id") or manifest.get("meeting_id") or directory.name)
        title = str(manifest.get("title") or directory.name)
        transcript_path = next((directory / name for name in ("transcript.json", "transcript.md", "transcript.txt") if (directory / name).is_file()), None)
        summary_path = next((directory / name for name in ("summary.json", "summary.md", "summary.txt", "notes.md") if (directory / name).is_file()), None)
        try:
            transcript = transcript_path.read_text(encoding="utf-8") if transcript_path else None
        except (OSError, UnicodeError):
            transcript = None
        try:
            summary = summary_path.read_text(encoding="utf-8") if summary_path else None
        except (OSError, UnicodeError):
            summary = None
        metadata = {"source_export": str(directory)}
        if manifest_error:
            metadata["manifest_error"] = manifest_error
        output.append(MeetilyMeeting(external_id, title, _datetime(manifest.get("created_at")), _datetime(manifest.get("updated_at")), str(directory), transcript, transcript_path.suffix.lstrip(".") if transcript_path else None, parse_transcript_segments(transcript), summary, summary_path.suffix.lstrip(".") if summary_path else None, manifest.get("summary_template"), metadata=metadata))
    return output


@dataclass(slots=True)
class MeetilySyncSummary:
    run_id: str | None = None
    status: str = "running"
    meetings_discovered: int = 0
    meetings_created: int = 0
    meetings_updated: int = 0
    meetings_unchanged: int = 0
    transcripts_created: int = 0
    transcripts_updated: int = 0
    summaries_created: int = 0
    summaries_updated: int = 0
    notes_created: int = 0
    notes_updated: int = 0
    segments_created: int = 0
    missing: int = 0
    restored: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self) if item.name not in {"run_id", "status", "errors"}}


def _change(session: Session, meeting: Meeting, change_type: str, run_id: Any, previous_hash: str | None = None, new_hash: str | None = None, previous_value: str | None = None, new_value: str | None = None) -> None:
    session.add(MeetingSourceChange(meeting_id=meeting.id, change_type=change_type, previous_hash=previous_hash, new_hash=new_hash, previous_value=previous_value, new_value=new_value, ingestion_run_id=run_id))


def _upsert_artifact(session: Session, meeting: Meeting, artifact_type: str, content: str | None, source_reference: str | None, source_record_id: str | None, source_created_at: datetime | None, source_updated_at: datetime | None, generated_by: str | None, summary_template: str | None, segments: list[MeetilySegment], summary: MeetilySyncSummary, run: IngestionRun | None, dry_run: bool) -> None:
    if not content:
        return
    digest = _hash(content)
    current = session.scalar(select(MeetingArtifact).where(MeetingArtifact.meeting_id == meeting.id, MeetingArtifact.artifact_type == artifact_type, MeetingArtifact.is_current.is_(True)))
    if current and current.content_hash == digest:
        return
    if dry_run:
        if artifact_type == "transcript": summary.transcripts_updated += int(current is not None); summary.transcripts_created += int(current is None)
        elif artifact_type == "summary": summary.summaries_updated += int(current is not None); summary.summaries_created += int(current is None)
        else: summary.notes_updated += int(current is not None); summary.notes_created += int(current is None)
        return
    if current:
        current.is_current = False
        change_type = {"transcript": "transcript_updated", "summary": "summary_updated", "notes": "notes_updated"}.get(artifact_type, "artifact_updated")
        _change(session, meeting, change_type, run.id if run else None, current.content_hash, digest)
    artifact = MeetingArtifact(meeting_id=meeting.id, artifact_type=artifact_type, source_record_id=source_record_id, source_reference=source_reference, content=content, content_hash=digest, format="text", source_created_at=source_created_at, source_updated_at=source_updated_at, generated_by=generated_by, summary_template=summary_template, metadata_={"generated_by": generated_by, "ai_generated": artifact_type == "summary"})
    session.add(artifact)
    session.flush()
    if artifact_type == "transcript":
        summary.transcripts_updated += int(current is not None); summary.transcripts_created += int(current is None)
        for segment in segments:
            session.add(MeetingTranscriptSegment(meeting_id=meeting.id, artifact_id=artifact.id, sequence=segment.sequence, start_ms=segment.start_ms, end_ms=segment.end_ms, speaker=segment.speaker, text=segment.text, content_hash=_hash(segment.text), metadata_=segment.metadata))
            summary.segments_created += 1
    elif artifact_type == "summary":
        summary.summaries_updated += int(current is not None); summary.summaries_created += int(current is None)
    else:
        summary.notes_updated += int(current is not None); summary.notes_created += int(current is None)


def sync_meetily(session: Session, meetings: list[MeetilyMeeting], *, source_reference: str, full: bool = False, meeting_id: str | None = None, dry_run: bool = False) -> MeetilySyncSummary:
    summary = MeetilySyncSummary(meetings_discovered=len(meetings))
    run = None if dry_run else IngestionRun(source_type="meetily", source_path=source_reference, status="running")
    if run:
        session.add(run); session.flush(); summary.run_id = str(run.id)
    seen: set[str] = set()
    for item in meetings:
        if meeting_id and item.external_id != meeting_id:
            continue
        seen.add(item.external_id)
        try:
            existing = session.scalar(select(Meeting).where(Meeting.source_type == "meetily", Meeting.external_meeting_id == item.external_id))
            if existing is None:
                if dry_run:
                    summary.meetings_created += 1
                    continue
                existing = Meeting(title=item.title, source_type="meetily", external_meeting_id=item.external_id, meeting_date=item.created_at, started_at=item.created_at, source_created_at=item.created_at, source_updated_at=item.updated_at, metadata_=item.metadata)
                session.add(existing); session.flush(); summary.meetings_created += 1
                _change(session, existing, "created", run.id if run else None, new_value=item.title)
            else:
                changed = existing.title != item.title or existing.source_updated_at != item.updated_at
                if existing.is_missing:
                    existing.is_missing = False; summary.restored += 1; _change(session, existing, "restored", run.id if run else None)
                if changed:
                    old_title = existing.title
                    existing.title = item.title; existing.source_updated_at = item.updated_at; existing.last_changed_at = datetime.now(timezone.utc); existing.metadata_ = item.metadata; summary.meetings_updated += 1
                    _change(session, existing, "title_changed" if old_title != item.title else "metadata_updated", run.id if run else None, previous_value=old_title, new_value=item.title)
                else:
                    summary.meetings_unchanged += 1
            if dry_run:
                continue
            existing.last_seen_at = datetime.now(timezone.utc); existing.meeting_date = item.created_at; existing.started_at = item.created_at
            _upsert_artifact(session, existing, "transcript", item.transcript, item.folder_path or source_reference, item.external_id, item.created_at, item.updated_at, "meetily", None, item.transcript_segments, summary, run, dry_run)
            _upsert_artifact(session, existing, "summary", item.summary, item.folder_path or source_reference, item.external_id, item.created_at, item.updated_at, "meetily", item.summary_template, [], summary, run, dry_run)
            if item.notes:
                _upsert_artifact(session, existing, "notes", item.notes, item.folder_path or source_reference, item.external_id, item.created_at, item.updated_at, "meetily", None, [], summary, run, dry_run)
        except Exception as error:
            summary.errors.append({"meeting_id": item.external_id, "error": str(error)[:500]})
    if full and not dry_run and not meeting_id:
        for existing in session.scalars(select(Meeting).where(Meeting.source_type == "meetily", Meeting.is_missing.is_(False))).all():
            if existing.external_meeting_id not in seen:
                existing.is_missing = True; existing.last_changed_at = datetime.now(timezone.utc); summary.missing += 1; _change(session, existing, "missing", run.id if run else None)
    summary.status = "completed_dry_run" if dry_run else "completed" if not summary.errors else "completed_with_errors"
    if run:
        run.status = summary.status; run.records_seen = summary.meetings_discovered; run.records_created = summary.meetings_created; run.records_updated = summary.meetings_updated + summary.transcripts_updated + summary.summaries_updated; run.records_skipped = summary.meetings_unchanged; run.records_failed = len(summary.errors); run.finished_at = datetime.now(timezone.utc); run.error_log = summary.errors; run.metadata_ = summary.metadata()
    return summary
