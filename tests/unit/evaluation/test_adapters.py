import asyncio

from evaluation.adapters import FullPipelineAdapter, KeywordBaselineAdapter, VectorOnlyAdapter
from evaluation.models import EvaluationQuery


def test_keyword_baseline_ranking_is_deterministic() -> None:
    corpus = [
        {"id": "2", "title": "Open repositories", "abstract": "open science"},
        {"id": "1", "title": "Open science", "abstract": "repositories repositories"},
        {"id": "3", "title": "Unrelated", "abstract": "other"},
    ]
    adapter = KeywordBaselineAdapter(corpus)

    first = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "open repositories"), 10))
    second = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "open repositories"), 10))

    assert [item.publication_id for item in first.results] == ["2", "1"]
    assert [item.score for item in first.results] == [5.0, 4.0]
    assert [(item.publication_id, item.score) for item in first.results] == [
        (item.publication_id, item.score) for item in second.results
    ]


def test_vector_adapter_uses_original_query_and_existing_fetch_shape() -> None:
    calls = []

    async def embed(text):
        calls.append(("embed", text))
        return [0.1, 0.2]

    def fetch(vector, limit, year_from, year_to):
        calls.append(("fetch", vector, limit, year_from, year_to))
        return [(7, "Title", "Abstract", "https://example.test/7", None, 0.25, "Repo", [])]

    run = asyncio.run(VectorOnlyAdapter(embed, fetch).retrieve(EvaluationQuery("q1", "semantic query"), 5))

    assert calls == [("embed", "semantic query"), ("fetch", [0.1, 0.2], 5, None, None)]
    assert run.method == "vector_only"
    assert run.results[0].publication_id == "7"
    assert run.results[0].score == 0.75


def test_vector_adapter_accepts_similarity_dict_without_eager_distance_lookup() -> None:
    run = asyncio.run(
        VectorOnlyAdapter(
            lambda _text: [0.1],
            lambda *_args: [{"id": 4, "cosine_similarity": 0.8, "title": "Result"}],
        ).retrieve(EvaluationQuery("q1", "query"), 1)
    )
    assert run.results[0].publication_id == "4"
    assert run.results[0].score == 0.8


def test_full_pipeline_adapter_preserves_parser_mode_from_mocked_service() -> None:
    async def search(text, limit):
        assert (text, limit) == ("query", 3)
        return {
            "plan": {"parser_mode": "fallback_service_error"},
            "results": [{"id": 9, "score": 0.8, "title": "Result", "abstract": None}],
        }

    run = asyncio.run(FullPipelineAdapter(search).retrieve(EvaluationQuery("q1", "query"), 3))

    assert run.parser_mode == "fallback_service_error"
    assert [item.publication_id for item in run.results] == ["9"]
