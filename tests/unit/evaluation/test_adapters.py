import asyncio

import pytest

from evaluation.adapters import (
    BM25BaselineAdapter,
    FullPipelineAdapter,
    KeywordBaselineAdapter,
    VectorOnlyAdapter,
    tokenize,
)
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


def test_tokenization_uses_nfkc_casefold_for_serbian_latin_and_cyrillic() -> None:
    assert tokenize("\uff36E\u0160TA\u010cKA \u041f\u0420\u0418\u041c\u0415\u041d\u0410") == [
        "ve\u0161ta\u010dka",
        "\u043f\u0440\u0438\u043c\u0435\u043d\u0430",
    ]


def test_bm25_ranking_and_publication_id_tie_break_are_deterministic() -> None:
    corpus = [
        {"id": "2", "title": "Ista tema", "abstract": None},
        {"id": "1", "title": "ISTA TEMA", "abstract": None},
        {"id": "3", "title": "Drugo", "abstract": None},
    ]
    adapter = BM25BaselineAdapter(corpus)

    first = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "ista tema"), 10))
    second = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "ista tema"), 10))

    assert [item.publication_id for item in first.results] == ["1", "2"]
    assert [item.score for item in first.results] == pytest.approx(
        [item.score for item in second.results]
    )


def test_bm25_document_frequency_and_length_normalization_affect_ranking() -> None:
    corpus = [
        {"id": "rare", "title": None, "abstract": "common rare"},
        {"id": "short", "title": None, "abstract": "common"},
        {"id": "long", "title": None, "abstract": "common " + "noise " * 30},
        {"id": "other", "title": None, "abstract": "common other"},
    ]
    adapter = BM25BaselineAdapter(corpus)

    mixed = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "common rare"), 10))
    common = asyncio.run(adapter.retrieve(EvaluationQuery("q2", "common"), 10))

    assert mixed.results[0].publication_id == "rare"
    assert [item.publication_id for item in common.results].index("short") < [
        item.publication_id for item in common.results
    ].index("long")


def test_common_terms_do_not_dominate_bm25_like_legacy_frequency_baseline() -> None:
    corpus = [
        {"id": "relevant", "title": None, "abstract": "i u za retka"},
        {"id": "long", "title": None, "abstract": "i u za " * 40},
        {"id": "other-1", "title": None, "abstract": "i u za drugo"},
        {"id": "other-2", "title": None, "abstract": "i u za trece"},
    ]
    query = EvaluationQuery("q1", "i u za retka")

    legacy = asyncio.run(KeywordBaselineAdapter(corpus).retrieve(query, 10))
    bm25 = asyncio.run(BM25BaselineAdapter(corpus).retrieve(query, 10))

    assert legacy.results[0].publication_id == "long"
    assert bm25.results[0].publication_id == "relevant"


def test_bm25_title_score_has_documented_two_times_boost() -> None:
    corpus = [
        {"id": "title", "title": "repozitorijum", "abstract": None},
        {"id": "abstract", "title": None, "abstract": "repozitorijum"},
    ]
    run = asyncio.run(BM25BaselineAdapter(corpus).retrieve(EvaluationQuery("q1", "repozitorijum"), 10))

    assert [item.publication_id for item in run.results] == ["title", "abstract"]
    assert run.results[0].score == pytest.approx(2 * run.results[1].score)


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
