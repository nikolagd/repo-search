from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from evaluation.models import Judgment, RetrievedItem


def _relevance_map(judgments: Iterable[Judgment]) -> dict[str, int]:
    items = list(judgments)
    identifiers = [str(item.publication_id) for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate judgment publication IDs")
    return {str(item.publication_id): item.relevance for item in items}


def _validate_results(results: list[RetrievedItem]) -> None:
    identifiers = [item.publication_id for item in results]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate result publication IDs")
    if any(not math.isfinite(float(item.score)) for item in results):
        raise ValueError("result scores must be finite")


def precision_at_k(results: list[RetrievedItem], judgments: Iterable[Judgment], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    _validate_results(results)
    relevance = _relevance_map(judgments)
    return sum(relevance.get(item.publication_id, 0) > 0 for item in results[:k]) / k


def recall_at_k(results: list[RetrievedItem], judgments: Iterable[Judgment], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    _validate_results(results)
    relevance = _relevance_map(judgments)
    relevant = {publication_id for publication_id, grade in relevance.items() if grade > 0}
    if not relevant:
        return 0.0
    retrieved = {item.publication_id for item in results[:k]}
    return len(relevant & retrieved) / len(relevant)


def reciprocal_rank(
    results: list[RetrievedItem],
    judgments: Iterable[Judgment],
    k: int | None = None,
) -> float:
    if k is not None and k <= 0:
        raise ValueError("k must be positive")
    _validate_results(results)
    relevance = _relevance_map(judgments)
    ranked_results = results if k is None else results[:k]
    for rank, item in enumerate(ranked_results, start=1):
        if relevance.get(item.publication_id, 0) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(results: list[RetrievedItem], judgments: Iterable[Judgment], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    _validate_results(results)
    relevance = _relevance_map(judgments)

    def dcg(grades: list[int]) -> float:
        return sum(((2**grade) - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))

    actual = dcg([relevance.get(item.publication_id, 0) for item in results[:k]])
    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    return min(actual / ideal, 1.0) if ideal else 0.0


def evaluate_run(
    results: list[RetrievedItem],
    judgments: list[Judgment],
    k_values: Iterable[int],
) -> dict[str, Any]:
    relevant_count = sum(item.relevance > 0 for item in judgments)
    metrics: dict[str, Any] = {
        "judged_documents": len(judgments),
        "relevant_documents": relevant_count,
        "has_relevant_judgments": relevant_count > 0,
        "mrr": reciprocal_rank(results, judgments),
    }
    for k in sorted(set(k_values)):
        metrics[f"mrr@{k}"] = reciprocal_rank(results, judgments, k)
        metrics[f"precision@{k}"] = precision_at_k(results, judgments, k)
        metrics[f"recall@{k}"] = recall_at_k(results, judgments, k)
        metrics[f"ndcg@{k}"] = ndcg_at_k(results, judgments, k)
    return metrics
