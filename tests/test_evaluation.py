import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "evaluate_retrieval.py"
    spec = importlib.util.spec_from_file_location("evaluate_retrieval", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metrics_accept_alternative_target_for_all_hit_metrics():
    module = _module()
    rows = [{
        "query": "room booking",
        "expected_retrieval_unit_ids": ["primary"],
        "acceptable_expected_ids": ["duplicate"],
    }]
    metrics = module._metrics(rows, {"room booking": ["duplicate", "other"]})
    assert metrics["hit_at_1"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["recall_at_5"] == 1.0


def test_metrics_recall_counts_multiple_direct_targets():
    module = _module()
    rows = [{
        "query": "funding",
        "expected_retrieval_unit_ids": ["one", "two"],
    }]
    metrics = module._metrics(rows, {"funding": ["two", "other"]})
    assert metrics["hit_at_5"] == 1.0
    assert metrics["recall_at_5"] == 0.5


def test_dataset_loader_supports_metadata_wrapper():
    module = _module()
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as handle:
        handle.write('{"benchmark_version":"test","queries":[{"query":"x","expected_retrieval_unit_ids":["id"]}]}')
        path = Path(handle.name)
    try:
        rows, metadata = module._load_dataset(path)
    finally:
        path.unlink(missing_ok=True)
    assert rows[0]["query"] == "x"
    assert metadata["benchmark_version"] == "test"


def test_benchmark_validation_rejects_missing_or_invalid_targets():
    module = _module()
    errors = module._validate_rows([
        {"query": "missing target", "expected_retrieval_unit_ids": []},
        {"query": "bad uuid", "expected_retrieval_unit_ids": ["not-a-uuid"]},
        {"query": "bad uuid", "expected_retrieval_unit_ids": ["not-a-uuid"]},
    ])
    assert any("at least one expected" in error for error in errors)
    assert any("invalid retrieval-unit UUID" in error for error in errors)
    assert any("duplicate query" in error for error in errors)


def test_reviewed_benchmark_serialization_has_metadata_and_unique_queries():
    module = _module()
    path = Path(__file__).parents[1] / "evaluation" / "retrieval_queries_hard_reviewed.json"
    rows, metadata = module._load_dataset(path)
    assert metadata["benchmark_version"] == "hard_reviewed_v1"
    assert len(rows) == 32
    assert not module._validate_rows(rows)
    assert len({row["query"] for row in rows}) == len(rows)
