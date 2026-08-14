import importlib.util
from pathlib import Path

from efds.retrieval.service import _semantic_primary_exact, make_context_package
from efds.retrieval.types import RetrievalResult


def _split_module():
    path = Path(__file__).parents[1] / "scripts" / "split_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("split_retrieval_benchmark", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _v2_module():
    path = Path(__file__).parents[1] / "scripts" / "evaluate_retrieval_v2.py"
    spec = importlib.util.spec_from_file_location("evaluate_retrieval_v2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(identifier: str, title: str, *, lexical_rank: int | None = None, semantic_rank: int | None = None) -> RetrievalResult:
    return RetrievalResult(
        retrieval_unit_id=identifier,
        source_type="icu_article",
        source_record_id=identifier,
        title=title,
        snippet=title,
        score=1.0,
        lexical_rank=lexical_rank,
        semantic_rank=semantic_rank,
    )


def test_split_is_deterministic_and_covers_repeated_sources_and_categories():
    module = _split_module()
    rows = [
        {"query": f"q{i}", "source_family": "document" if i < 4 else "icu_article", "category": "identifier" if i < 2 else "finance", "expected_retrieval_unit_ids": [f"{i:032x}"]}
        for i in range(8)
    ]
    first = module.split_rows(rows)
    second = module.split_rows(rows)
    assert first == second
    assert {row["source_family"] for row in first[1]} == {"document", "icu_article"}
    assert {row["category"] for row in first[1]} == {"identifier", "finance"}
    assert not ({row["query"] for row in first[0]} & {row["query"] for row in first[1]})


def test_identifier_rescue_keeps_semantic_primary_for_normal_queries():
    lexical = [_result("lex", "CSP email account", lexical_rank=1)]
    semantic = [_result("sem", "Unrelated semantic result", semantic_rank=1)]
    rescued = _semantic_primary_exact("How do I use the CSP email account?", lexical, semantic)
    assert [item.retrieval_unit_id for item in rescued] == ["lex", "sem"]
    normal = _semantic_primary_exact("How do I get reimbursed?", lexical, semantic)
    assert [item.retrieval_unit_id for item in normal] == ["sem"]


def test_context_package_marks_empty_and_single_result_quality():
    empty = make_context_package("unknown", "admin", [])
    assert empty.retrieval_metadata["retrieval_quality"] == "low"
    limited = make_context_package("one", "admin", [_result("one", "One")])
    assert limited.retrieval_metadata["retrieval_quality"] == "limited"
    assert limited.retrieval_metadata["result_count"] == 1


def test_v2_metrics_support_hit_at_10_and_recall_at_10():
    path = Path(__file__).parents[1] / "scripts" / "evaluate_retrieval_v2.py"
    spec = importlib.util.spec_from_file_location("evaluate_retrieval_v2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{"query": "q", "expected_retrieval_unit_ids": ["primary"], "acceptable_expected_ids": ["alternative"]}]
    ranked = {"q": [f"other-{i}" for i in range(9)] + ["alternative"]}
    metrics = module._metrics(rows, ranked)
    assert metrics["hit_at_5"] == 0.0
    assert metrics["hit_at_10"] == 1.0
    assert metrics["recall_at_10"] == 1.0


def test_v2_coverage_distinguishes_policy_disabled_from_missing_embedding():
    module = _v2_module()
    from types import SimpleNamespace
    from efds.retrieval.embeddings import embedding_input_hash

    settings = SimpleNamespace(embedding_source_types=(), embedding_document_areas=(), embedding_slack_channel_ids=())
    base = {
        "id": "id", "source_type": "document", "title": "Governance", "content": "Useful policy text",
        "source_area": "03_committee", "topic": None, "channel": None, "source_parent_id": None,
        "relative_path": "committee/policy.md", "metadata": {}, "is_current": True,
        "is_stale": False, "is_deleted": False, "review_status": None, "input_hash": None,
    }
    assert module._target_status(base, settings) == "SOURCE_NOT_EMBEDDED_BY_POLICY"
    base["source_area"] = "01_governance"
    assert module._target_status(base, settings) == "SOURCE_NOT_EMBEDDED"
    base["input_hash"] = embedding_input_hash(SimpleNamespace(**base))
    assert module._target_status(base, settings) == "EVALUABLE"


def test_v2_reviewed_file_excludes_empty_source_families():
    payload = __import__("json").loads((Path(__file__).parents[1] / "evaluation" / "retrieval_queries_v2_reviewed.json").read_text(encoding="utf-8"))
    rows = payload["queries"]
    assert not {row.get("source_family") for row in rows} & {"slack_message", "meeting_transcript", "meeting_summary", "operational_decision"}
    assert len({row["query"] for row in rows}) == len(rows)
