from efds.db.models import OperationalRecord, OperationalRecordEvidence, OperationalReviewEvent
from efds.retrieval.indexer import RETRIEVAL_SOURCE_TYPES, _unit_id, _unit_key


def test_operational_record_has_distinct_review_and_execution_fields():
    assert {"record_type", "review_status", "execution_status", "review_version"}.issubset(
        OperationalRecord.__table__.columns.keys()
    )
    assert "retrieval_unit_id" in OperationalRecordEvidence.__table__.columns
    assert "changes" in OperationalReviewEvent.__table__.columns


def test_operational_retrieval_types_are_controlled_and_stable():
    expected = {
        "operational_decision",
        "operational_action",
        "operational_commitment",
        "operational_question",
        "operational_status",
    }
    assert expected.issubset(RETRIEVAL_SOURCE_TYPES)
    first = _unit_key("operational_decision", "record-1", "3", 0)
    assert first == _unit_key("operational_decision", "record-1", "3", 0)
    assert _unit_id(first) != _unit_id(_unit_key("operational_decision", "record-1", "4", 0))
