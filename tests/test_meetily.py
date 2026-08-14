import json
import sqlite3

from efds.integrations.meetily import MeetilyReader, parse_transcript_segments, read_export_directory


def test_structured_transcript_preserves_order_timestamps_and_speaker():
    segments = parse_transcript_segments(json.dumps({"segments": [
        {"start": 1.5, "end": 3.0, "speaker": "A", "text": "Welcome"},
        {"start_ms": 4000, "text": "Next point"},
    ]}))
    assert [(item.sequence, item.start_ms, item.end_ms, item.speaker) for item in segments] == [(0, 1500, 3000, "A"), (1, 4000, None, None)]


def test_timestamped_text_does_not_fabricate_speaker():
    segments = parse_transcript_segments("00:12 Welcome\n00:20 A real speaker: Update")
    assert segments[0].start_ms == 12000
    assert segments[0].speaker is None
    assert segments[1].speaker == "A real speaker"


def test_reader_uses_sqlite_read_only_schema(tmp_path):
    path = tmp_path / "meeting.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
      CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, folder_path TEXT);
      CREATE TABLE transcripts (id TEXT PRIMARY KEY, meeting_id TEXT, transcript TEXT, timestamp TEXT, summary TEXT, action_items TEXT, key_points TEXT, audio_start_time REAL, audio_end_time REAL, duration REAL, speaker TEXT);
      CREATE TABLE transcript_chunks (meeting_id TEXT PRIMARY KEY, meeting_name TEXT, transcript_text TEXT, model TEXT, model_name TEXT, chunk_size INTEGER, overlap INTEGER, created_at TEXT);
      CREATE TABLE summary_processes (meeting_id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT, error TEXT, result TEXT, start_time TEXT, end_time TEXT, chunk_count INTEGER, processing_time REAL, metadata TEXT, result_backup TEXT, result_backup_timestamp TEXT);
      CREATE TABLE meeting_notes (meeting_id TEXT PRIMARY KEY, notes_markdown TEXT, notes_json TEXT, created_at TEXT, updated_at TEXT);
      INSERT INTO meetings VALUES ('m1', 'Committee meeting', '2026-08-01T10:00:00Z', '2026-08-01T10:00:00Z', NULL);
      INSERT INTO transcripts VALUES ('t1', 'm1', '00:01 Hello', '2026-08-01T10:00:00Z', NULL, NULL, NULL, 1, 2, 2, NULL);
      INSERT INTO transcript_chunks VALUES ('m1', 'Committee meeting', '00:01 Hello', 'whisper', 'base', 10, 1, '2026-08-01T10:00:00Z');
      INSERT INTO summary_processes VALUES ('m1', 'completed', '2026-08-01T10:00:00Z', '2026-08-01T10:01:00Z', NULL, '{"summary":"Discussed plans"}', NULL, NULL, 1, 1, NULL, NULL, NULL);
      INSERT INTO meeting_notes VALUES ('m1', '# Notes', NULL, '2026-08-01T10:00:00Z', '2026-08-01T10:01:00Z');
    """)
    connection.commit()
    connection.close()

    meetings = MeetilyReader(path).meetings()
    assert len(meetings) == 1
    assert meetings[0].external_id == "m1"
    assert meetings[0].transcript_segments[0].start_ms == 1000
    assert '"summary": "Discussed plans"' in (meetings[0].summary or "")
    assert meetings[0].notes == "# Notes"


def test_malformed_export_manifest_does_not_abort_other_meetings(tmp_path):
    broken = tmp_path / "broken"
    valid = tmp_path / "valid"
    broken.mkdir(); valid.mkdir()
    (broken / "meeting.json").write_text("not json", encoding="utf-8")
    (broken / "transcript.txt").write_text("00:01 Broken transcript", encoding="utf-8")
    (valid / "meeting.json").write_text(json.dumps({"id": "valid", "title": "Valid"}), encoding="utf-8")
    (valid / "summary.md").write_text("Summary", encoding="utf-8")
    meetings = read_export_directory(tmp_path)
    assert {meeting.external_id for meeting in meetings} == {"broken", "valid"}
    assert meetings[0].metadata.get("manifest_error")
