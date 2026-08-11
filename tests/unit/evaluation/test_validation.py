import json

import pytest

from evaluation.io import load_judgments, load_queries, load_runs, validate_comparison_matrix
from evaluation.models import Judgment, QueryMetadata, QueryRun
from evaluation.reporting import build_report


METHODS = {"keyword", "vector_only", "full_pipeline"}


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _run(query_id="q1", method="keyword", results=None, latency_ms=1.0):
    return {
        "query_id": query_id,
        "method": method,
        "latency_ms": latency_ms,
        "parser_mode": "fallback" if method == "full_pipeline" else None,
        "results": [] if results is None else results,
    }


def _result(rank=1, publication_id="d1", score=1.0):
    return {"rank": rank, "publication_id": publication_id, "score": score}


def test_zero_result_runs_remain_in_equal_macro_query_counts(tmp_path) -> None:
    records = [
        _run(query_id, method, [_result()] if query_id == "q1" else [])
        for query_id in ("q1", "q2")
        for method in sorted(METHODS)
    ]
    runs = load_runs(
        _write(tmp_path / "runs.json", {"runs": records}),
        {"q1", "q2"},
        METHODS,
    )
    validate_comparison_matrix(runs, {"q1", "q2"}, METHODS)
    report = build_report(
        runs,
        [Judgment("q1", "d1", 2)],
        [
            QueryMetadata("q1", "sr", "latin", "category", "topic 1"),
            QueryMetadata("q2", "sr", "latin", "category", "topic 2"),
        ],
        git_commit="abc",
        corpus_size=10,
        k_values=[1],
        embedding_model="model",
        ranking_configuration={},
    )

    assert {row["evaluated_queries"] for row in report["aggregate_metrics"]} == {2}
    assert {row["queries_without_relevant_judgments"] for row in report["aggregate_metrics"]} == {1}
    assert len(report["per_query_metrics"]) == 6


def test_missing_query_method_run_fails_matrix_validation() -> None:
    runs = [QueryRun("q1", method, []) for method in METHODS if method != "keyword"]
    with pytest.raises(ValueError, match="incomplete comparison matrix"):
        validate_comparison_matrix(runs, {"q1"}, METHODS)


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([_result(), _result(rank=2)], "duplicate publication"),
        ([_result(), _result(rank=1, publication_id="d2")], "duplicate ranks"),
        ([_result(), _result(rank=3, publication_id="d2")], "contiguous"),
        ([_result(rank=0)], "contiguous"),
    ],
)
def test_invalid_result_identity_or_ranks_fail(tmp_path, results, message) -> None:
    path = _write(tmp_path / "runs.json", {"runs": [_run(results=results)]})
    with pytest.raises(ValueError, match=message):
        load_runs(path, {"q1"}, METHODS)


def test_duplicate_or_conflicting_judgments_fail(tmp_path) -> None:
    path = _write(
        tmp_path / "judgments.json",
        {
            "judgments": [
                {"query_id": "q1", "publication_id": "d1", "relevance": 1},
                {"query_id": "q1", "publication_id": "d1", "relevance": 2},
            ]
        },
    )
    with pytest.raises(ValueError, match="duplicate judgment"):
        load_judgments(path, {"q1"})


@pytest.mark.parametrize("relevance", [True, 1.0, "1"])
def test_judgment_loader_rejects_non_integer_grades(tmp_path, relevance) -> None:
    path = _write(
        tmp_path / "judgments.json",
        {"judgments": [{"query_id": "q1", "publication_id": "d1", "relevance": relevance}]},
    )
    with pytest.raises(ValueError, match="integer relevance|relevance"):
        load_judgments(path, {"q1"})


def test_duplicate_and_unknown_query_references_fail(tmp_path) -> None:
    queries = _write(
        tmp_path / "queries.json",
        {"queries": [{"query_id": "q1", "text": "one"}, {"query_id": "q1", "text": "two"}]},
    )
    with pytest.raises(ValueError, match="duplicate query"):
        load_queries(queries)

    judgments = _write(
        tmp_path / "judgments.json",
        {"judgments": [{"query_id": "unknown", "publication_id": "d1", "relevance": 1}]},
    )
    with pytest.raises(ValueError, match="unknown query"):
        load_judgments(judgments, {"q1"})

    unknown_run = _write(tmp_path / "runs.json", {"runs": [_run(query_id="unknown")]})
    with pytest.raises(ValueError, match="unknown query"):
        load_runs(unknown_run, {"q1"}, METHODS)


@pytest.mark.parametrize(
    "query_row",
    [
        {"query_id": 1, "text": "query"},
        {"query_id": " ", "text": "query"},
        {"query_id": "q1", "text": 1},
        {"query_id": "q1", "text": "   "},
        {"query_id": "q1", "text": "query", "extra": True},
    ],
)
def test_malformed_queries_fail_before_collection(tmp_path, query_row) -> None:
    path = _write(tmp_path / "malformed-queries.json", {"queries": [query_row]})
    with pytest.raises(ValueError, match="non-empty strings"):
        load_queries(path)


def test_duplicate_run_unknown_method_and_non_finite_score_fail(tmp_path) -> None:
    duplicate = _write(tmp_path / "duplicate.json", {"runs": [_run(), _run()]})
    with pytest.raises(ValueError, match="duplicate run"):
        load_runs(duplicate)

    unknown_method = _write(tmp_path / "method.json", {"runs": [_run(method="other")]})
    with pytest.raises(ValueError, match="unknown method"):
        load_runs(unknown_method, {"q1"}, METHODS)

    score = _write(tmp_path / "score.json", {"runs": [_run(results=[_result(score=float("inf"))])]})
    with pytest.raises(ValueError, match="scores must be finite"):
        load_runs(score)


@pytest.mark.parametrize("latency", [-1, float("inf"), float("nan")])
def test_invalid_latency_fails(tmp_path, latency) -> None:
    path = _write(tmp_path / "runs.json", {"runs": [_run(latency_ms=latency)]})
    with pytest.raises(ValueError, match="latency"):
        load_runs(path)
