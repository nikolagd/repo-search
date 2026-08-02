from __future__ import annotations

import pytest

from microservices.common import observability


class MetricProbe:
    def __init__(self) -> None:
        self.observed: list[float] = []

    def labels(self, *_args):
        return self

    def inc(self, *_args) -> None:
        pass

    def observe(self, value: float) -> None:
        self.observed.append(value)


def test_record_retrieval_search_defaults_result_count_for_nonempty_scores(monkeypatch) -> None:
    probes = {
        name: MetricProbe()
        for name in (
            "RETRIEVAL_SEARCHES_TOTAL",
            "RETRIEVAL_SEARCH_MODES_TOTAL",
            "RETRIEVAL_AUTHOR_FILTER_COUNT",
            "RETRIEVAL_EMBEDDING_QUERY_COUNT",
            "RETRIEVAL_VECTOR_CANDIDATES",
            "RETRIEVAL_FINAL_RESULTS",
            "RETRIEVAL_ZERO_RESULTS_TOTAL",
            "RETRIEVAL_TOP_SCORE",
            "RETRIEVAL_AVERAGE_SCORE",
            "RETRIEVAL_RESULT_SCORE",
        )
    }
    for name, probe in probes.items():
        monkeypatch.setattr(observability, name, probe)

    observability.record_retrieval_search(
        "search-service",
        "llm",
        embedding_query_count=1,
        vector_candidate_count=2,
        result_scores=[0.8, 0.4],
    )

    assert probes["RETRIEVAL_FINAL_RESULTS"].observed == [2]
    assert probes["RETRIEVAL_TOP_SCORE"].observed == [0.8]
    assert probes["RETRIEVAL_AVERAGE_SCORE"].observed == [pytest.approx(0.6)]
    assert probes["RETRIEVAL_RESULT_SCORE"].observed == [0.8, 0.4]
