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
