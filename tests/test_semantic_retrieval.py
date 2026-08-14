from types import SimpleNamespace

import pytest

from efds.retrieval.embeddings import embedding_input, embedding_input_hash, is_embedding_eligible, vector_literal
from efds.retrieval.service import _fuse
from efds.retrieval.types import RetrievalResult


def unit(**overrides):
    values = dict(source_type="knowledge_requirement", title="Reimbursement", content="How to claim expenses", source_area="finance", topic=None, channel=None, is_current=True, is_stale=False, is_deleted=False, review_status="approved")
    values.update(overrides)
    return SimpleNamespace(**values)


def result(identifier, authority="source_record"):
    return RetrievalResult(retrieval_unit_id=identifier, source_type="knowledge_requirement", source_record_id=identifier, title=identifier, snippet=identifier, score=1, is_current=True, authority=authority)


def test_embedding_input_is_deterministic_and_excludes_ids():
    first = embedding_input(unit())
    assert first == embedding_input(unit())
    assert "Reimbursement" in first and "How to claim expenses" in first
    assert embedding_input_hash(unit()) == embedding_input_hash(unit())


def test_default_policy_excludes_sensitive_raw_sources():
    assert is_embedding_eligible(unit())
    assert is_embedding_eligible(unit(review_status="proposed"))
    assert not is_embedding_eligible(unit(source_type="operational_action", review_status="proposed"))
    assert not is_embedding_eligible(unit(source_type="slack_message"))
    assert not is_embedding_eligible(unit(source_type="document"))
    assert not is_embedding_eligible(unit(is_stale=True))


def test_document_policy_is_area_scoped_and_unknown_areas_are_denied():
    settings = SimpleNamespace(embedding_source_types=(), embedding_document_areas=(), embedding_slack_channel_ids=())
    assert is_embedding_eligible(unit(source_type="document", source_area="01_governance"), settings)
    assert not is_embedding_eligible(unit(source_type="document", source_area="03_committee"), settings)
    assert not is_embedding_eligible(unit(source_type="document", source_area="new_area"), settings)
    allowed = SimpleNamespace(embedding_source_types=("document",), embedding_document_areas=("03_committee",), embedding_slack_channel_ids=())
    assert is_embedding_eligible(unit(source_type="document", source_area="03_committee"), allowed)


def test_sensitive_document_metadata_is_denied_before_embedding():
    settings = SimpleNamespace(embedding_source_types=("document",), embedding_document_areas=("01_governance",), embedding_slack_channel_ids=())
    assert not is_embedding_eligible(unit(source_type="document", relative_path="01_governance/.env"), settings)
    assert not is_embedding_eligible(unit(source_type="document", metadata={"relative_path": "secrets/key.pem"}), settings)


def test_slack_requires_explicit_stable_channel_allowlist():
    denied = SimpleNamespace(embedding_source_types=("slack_message",), embedding_document_areas=(), embedding_slack_channel_ids=())
    assert not is_embedding_eligible(unit(source_type="slack_message", source_parent_id="C123"), denied)
    allowed = SimpleNamespace(embedding_source_types=("slack_message",), embedding_document_areas=(), embedding_slack_channel_ids=("C123",))
    assert is_embedding_eligible(unit(source_type="slack_message", source_parent_id="C123"), allowed)


def test_vector_literal_validates_finite_values():
    assert vector_literal([0.1, -0.2]) == "[0.1,-0.2]"
    with pytest.raises(ValueError):
        vector_literal([])


def test_rrf_fusion_combines_rankings_and_keeps_authority_modest():
    fused = _fuse([result("lexical"), result("shared")], [result("semantic"), result("shared", "approved_operational")], 4)
    assert [item.retrieval_unit_id for item in fused][0] == "shared"
    assert fused[0].semantic_rank == 2
    assert fused[0].lexical_rank == 2
    assert fused[0].hybrid_score is not None
