from efds.retrieval.chunking import chunk_text
from efds.retrieval.indexer import _unit_id, _unit_key


def test_chunking_is_bounded_and_deterministic():
    text = "\n\n".join(["Heading", "word " * 900])
    first = chunk_text(text)
    assert first == chunk_text(text)
    assert first
    assert all(len(value) <= 2400 for value in first)


def test_retrieval_identity_includes_source_version_and_chunk():
    first = _unit_key("document", "doc-1", "version-a", 0)
    second = _unit_key("document", "doc-1", "version-b", 0)
    assert first != second
    assert _unit_id(first) == _unit_id(first)
    assert _unit_id(first) != _unit_id(second)


def test_partial_rebuild_never_retires_other_source_families():
    from unittest.mock import Mock, patch
    from efds.db.models import RetrievalUnit
    from efds.retrieval.indexer import rebuild_retrieval_index
    slack = RetrievalUnit(stable_key="slack:old", source_type="slack_message", is_current=True)
    article = RetrievalUnit(stable_key="icu:keep", source_type="icu_article", is_current=True)
    session = Mock()
    session.scalars.return_value.all.return_value = [slack, article]
    with patch("efds.retrieval.indexer.build_retrieval_units", return_value=[]):
        result = rebuild_retrieval_index(session, source="slack_message")
    assert result["retired"] == 1
    assert not slack.is_current
    assert article.is_current
