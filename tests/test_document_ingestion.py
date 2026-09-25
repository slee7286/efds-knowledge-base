import os
from pathlib import Path
from uuid import uuid4

import pytest

from efds.ingestion.documents import iter_supported_files


def test_recursive_file_walker_ignores_hidden_and_temp_files(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    hidden = tmp_path / ".hidden"
    nested.mkdir()
    hidden.mkdir()
    (tmp_path / "good.txt").write_text("good", encoding="utf-8")
    (nested / "good.md").write_text("good", encoding="utf-8")
    (tmp_path / "~$temporary.docx").write_bytes(b"ignored")
    (hidden / "hidden.txt").write_text("ignored", encoding="utf-8")
    files = {path.name for path in iter_supported_files(tmp_path)}
    assert files == {"good.txt", "good.md"}


@pytest.mark.integration
def test_duplicate_document_behavior_is_opt_in(tmp_path: Path) -> None:
    """Run only when a developer explicitly provides a migrated test database."""

    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("DATABASE_URL"):
        pytest.skip("Set RUN_DB_TESTS=1 with a migrated test database to run integration tests")

    from efds.db.session import session_scope
    from efds.ingestion.documents import ingest_folder

    # Dedupe is keyed on a content hash checked against a persistent database,
    # so the payload must be unique per run. A fixed literal makes the *first*
    # ingest of every later run dedupe against the previous run's row, and the
    # assertion below fails on a database this test has already touched.
    (tmp_path / "duplicate.txt").write_text(f"duplicate probe {uuid4()}", encoding="utf-8")
    with session_scope() as session:
        first = ingest_folder(session, tmp_path)
    with session_scope() as session:
        second = ingest_folder(session, tmp_path)
    assert first.records_created == 1
    assert second.records_skipped == 1

