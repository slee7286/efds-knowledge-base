from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from efds.integrations import icu


def _write_article(root: Path, *, article_id: str = "123", title: str = "Budget", content: str = "Body") -> Path:
    directory = root / "articles" / "category" / "folder" / "budget"
    directory.mkdir(parents=True)
    (directory / "article.html").write_text(f"<h1>{title}</h1><p>{content}</p>", encoding="utf-8")
    (directory / "article.md").write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    metadata = {
        "id": article_id,
        "title": title,
        "source_url": f"https://imperialcollegeunion.freshdesk.com/support/solutions/articles/{article_id}-budget",
        "category": "Category",
        "folder": "Folder",
        "modified_at": "Tue, 11 Aug, 2026 at 10:00 AM",
        "crawled_at": "2026-08-11T09:00:00+00:00",
        "article_text": content,
        "content_hash": icu.sha256_bytes(content.encode("utf-8")),
        "raw_html_path": "articles\\category\\folder\\budget\\article.html",
        "markdown_path": "articles\\category\\folder\\budget\\article.md",
    }
    metadata_path = directory / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata_path


def test_existing_data_format_loads_without_crawler_network(tmp_path: Path) -> None:
    metadata_path = _write_article(tmp_path)
    article = icu.load_article(metadata_path, tmp_path)
    assert article.external_id == "123"
    assert article.title == "Budget"
    assert article.content_hash == icu.sha256_bytes(b"Body")
    assert article.source_updated_at == datetime(2026, 8, 11, 10, tzinfo=timezone.utc)
    assert list(icu.iter_metadata_paths(tmp_path)) == [metadata_path]


def test_identity_is_external_id_and_changed_content_is_an_update() -> None:
    source = SimpleNamespace(
        external_id="123",
        title="Budget",
        url="https://example.test/123",
        category="Category",
        folder="Folder",
        raw_html="<p>new</p>",
        markdown="new",
        content_hash="new-hash",
        source_updated_at=None,
        crawled_at=None,
        metadata={},
        metadata_path=Path("metadata.json"),
    )
    existing = SimpleNamespace(
        title="Budget",
        category="Category",
        folder="Folder",
        content_hash="old-hash",
        source_updated_at=None,
        metadata_={},
    )
    decision = icu.compare_article(existing, source)
    assert decision.change_type == "updated"
    assert decision.fields_changed == ("content",)


def test_changed_title_is_reported_without_changing_article_identity() -> None:
    source = SimpleNamespace(
        external_id="123",
        title="Updated Budget",
        url="https://example.test/123",
        category="Category",
        folder="Folder",
        content_hash="same-hash",
        source_updated_at=None,
    )
    existing = SimpleNamespace(
        title="Budget",
        category="Category",
        folder="Folder",
        content_hash="same-hash",
        source_updated_at=None,
        metadata_={},
    )
    decision = icu.compare_article(existing, source)
    assert decision.change_type == "updated"
    assert decision.fields_changed == ("title",)


def test_unchanged_article_is_skipped_but_last_checked_changes(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
    metadata_path = _write_article(tmp_path)
    article = icu.load_article(metadata_path, tmp_path)
    existing = SimpleNamespace(
        id=uuid4(),
        source_type=icu.SOURCE_TYPE,
        external_id=article.external_id,
        title=article.title,
        url=article.url,
        category=article.category,
        folder=article.folder,
        raw_html=article.raw_html,
        markdown=article.markdown,
        content_hash=article.content_hash,
        source_updated_at=article.source_updated_at,
        crawled_at=article.crawled_at,
        last_checked_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        last_changed_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        metadata_={},
    )

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, value: object) -> None:
            self.added.append(value)

    session = FakeSession()
    monkeypatch.setattr(icu, "_find_existing", lambda _session, _article: existing)
    outcome = icu._apply_article(session, article, tmp_path, None, now=now, dry_run=False)
    assert outcome == "skipped"
    assert existing.last_checked_at == now
    assert existing.last_changed_at == datetime(2026, 8, 9, tzinfo=timezone.utc)
    assert session.added == []


def test_malformed_metadata_isolated_to_one_article(tmp_path: Path) -> None:
    _write_article(tmp_path)
    bad = tmp_path / "articles" / "bad" / "metadata.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not-json", encoding="utf-8")
    good, broken = [], []
    for path in icu.iter_metadata_paths(tmp_path):
        try:
            good.append(icu.load_article(path, tmp_path))
        except ValueError:
            broken.append(path)
    assert len(good) == 1
    assert broken == [bad]
