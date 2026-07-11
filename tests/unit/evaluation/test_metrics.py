import math

import pytest

from evaluation.metrics import evaluate_run, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from evaluation.models import Judgment, RetrievedItem


def _result(publication_id: str) -> RetrievedItem:
    return RetrievedItem(publication_id, 1.0)


def test_metrics_support_binary_and_graded_relevance() -> None:
    results = [_result("d1"), _result("d2"), _result("d3")]
    judgments = [Judgment("q1", "d1", 1), Judgment("q1", "d2", 0), Judgment("q1", "d3", 2)]

    assert precision_at_k(results, judgments, 2) == pytest.approx(0.5)
    assert recall_at_k(results, judgments, 2) == pytest.approx(0.5)
    assert reciprocal_rank(results, judgments) == pytest.approx(1.0)
    expected_dcg = 1 + 3 / math.log2(4)
    expected_ideal = 3 + 1 / math.log2(3)
    assert ndcg_at_k(results, judgments, 3) == pytest.approx(expected_dcg / expected_ideal)


def test_empty_and_incomplete_judgments_have_explicit_zero_behavior() -> None:
    results = [_result("unjudged")]
    metrics = evaluate_run(results, [], [1, 5])

    assert metrics["has_relevant_judgments"] is False
    assert metrics["precision@1"] == 0
    assert metrics["recall@5"] == 0
    assert metrics["mrr"] == 0
    assert metrics["ndcg@5"] == 0

    incomplete = [Judgment("q1", "other", 2)]
    assert precision_at_k(results, incomplete, 1) == 0
    assert recall_at_k(results, incomplete, 1) == 0


def test_invalid_k_and_relevance_are_rejected() -> None:
    with pytest.raises(ValueError, match="relevance"):
        Judgment("q1", "d1", 3)
    with pytest.raises(ValueError, match="positive"):
        precision_at_k([], [], 0)


def test_metrics_reject_duplicate_inputs_and_valid_ndcg_is_bounded() -> None:
    duplicate_results = [_result("d1"), _result("d1")]
    duplicate_judgments = [Judgment("q1", "d1", 1), Judgment("q1", "d1", 2)]
    with pytest.raises(ValueError, match="duplicate result"):
        ndcg_at_k(duplicate_results, [Judgment("q1", "d1", 2)], 2)
    with pytest.raises(ValueError, match="duplicate judgment"):
        ndcg_at_k([_result("d1")], duplicate_judgments, 1)

    valid = ndcg_at_k(
        [_result("d2"), _result("d1"), _result("unjudged")],
        [Judgment("q1", "d1", 2), Judgment("q1", "d2", 1)],
        3,
    )
    assert 0 <= valid <= 1
