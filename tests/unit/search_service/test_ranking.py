from __future__ import annotations

import asyncio
import os
from contextlib import nullcontext
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

os.environ["OTEL_TRACING_ENABLED"] = "false"

from microservices.common.schemas import SearchRequest
from microservices.search_service import main as search_main


def _candidate_row(
    publication_id: int,
    *,
    title: str,
    abstract: str,
    cosine_distance: float,
    published_at: datetime | None = None,
    repository: str = "Test repository",
    authors: tuple[str, ...] = ("Test Author",),
) -> tuple[Any, ...]:
    return (
        publication_id,
        title,
        abstract,
        f"https://example.test/publications/{publication_id}",
        published_at or datetime(2022, 6, 15, 10, 30),
        cosine_distance,
        repository,
        authors,
    )


def _isolate_search_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_main, "observe_search_request", lambda *_args, **_kwargs: nullcontext(None))
    monkeypatch.setattr(search_main, "observe_retrieval_stage", lambda *_args, **_kwargs: nullcontext(None))
    monkeypatch.setattr(search_main, "record_retrieval_parser_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search_main, "emit_app_event", lambda *_args, **_kwargs: None)


def test_phrase_boost_is_case_insensitive_and_prefers_title_match() -> None:
    boost = search_main.phrase_boost(
        "Neural Retrieval and Open Science",
        "Neural retrieval with FAIR metadata",
        ["NEURAL RETRIEVAL", "fair metadata", "not present"],
        title_boost=0.04,
        abstract_boost=0.01,
    )

    assert boost == pytest.approx(0.05)
    assert search_main.phrase_boost(None, None, ["anything"], 0.04, 0.01) == 0.0


def test_explicit_author_precedence_deduplicates_reversed_parser_name() -> None:
    assert search_main.merge_author_names(
        ["Ime Prezime"], ["Prezime, Ime", "Drugi Autor"]
    ) == ["Ime Prezime", "Drugi Autor"]
    assert search_main.merge_author_names([], ["P. P.", "P. Petrović"]) == ["P. Petrović"]


def test_search_merges_candidates_and_ranks_with_all_boosts(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_search_boundaries(monkeypatch)
    monkeypatch.setattr(search_main, "CANDIDATE_MULTIPLIER", 3)
    monkeypatch.setattr(search_main, "TOPIC_TITLE_BOOST", 0.04)
    monkeypatch.setattr(search_main, "TOPIC_ABSTRACT_BOOST", 0.01)
    monkeypatch.setattr(search_main, "RANKING_PHRASE_BOOST", 0.02)
    monkeypatch.setattr(search_main, "QUERY_COVERAGE_BOOST", 0.003)

    parsed_plan = {
        "embedding_queries": ["primary query", "secondary query"],
        "topic_phrases": ["neural retrieval"],
        "ranking_phrases": ["priority"],
        "year_from": 2018,
        "year_to": 2024,
        "interpreted_query": "Neural retrieval with priority metadata",
        "used_fallback": False,
        "parser_mode": "llm",
    }
    parse_mock = AsyncMock(return_value=parsed_plan)
    embed_mock = AsyncMock(side_effect=[[1.0], [2.0]])
    monkeypatch.setattr(search_main, "parse_search_query", parse_mock)
    monkeypatch.setattr(search_main, "embed_query", embed_mock)

    rows_by_vector = {
        (1.0,): [
            _candidate_row(
                1,
                title="Neural Retrieval Systems",
                abstract="A priority metadata study",
                cosine_distance=0.20,
            ),
            _candidate_row(
                2,
                title="Repository Search",
                abstract="Neural retrieval for priority studies",
                cosine_distance=0.05,
            ),
        ],
        (2.0,): [
            _candidate_row(
                2,
                title="Repository Search",
                abstract="Neural retrieval for priority studies",
                cosine_distance=0.10,
            ),
            _candidate_row(
                1,
                title="Neural Retrieval Systems",
                abstract="A priority metadata study",
                cosine_distance=0.02,
            ),
            _candidate_row(
                3,
                title="Unrelated Publication",
                abstract="No matching phrases",
                cosine_distance=0.20,
            ),
        ],
    }
    fetch_calls: list[tuple[tuple[float, ...], int, int | None, int | None]] = []

    def fake_fetch(
        query_vector: list[float],
        limit: int,
        year_from: int | None,
        year_to: int | None,
    ) -> list[tuple[Any, ...]]:
        key = tuple(query_vector)
        fetch_calls.append((key, limit, year_from, year_to))
        return rows_by_vector[key]

    recorded_search: dict[str, Any] = {}

    def capture_retrieval_search(
        service_name: str,
        parser_mode: str,
        embedding_query_count: int,
        vector_candidate_count: int,
        result_scores: list[float],
        **kwargs: Any,
    ) -> None:
        recorded_search.update(
            service_name=service_name,
            parser_mode=parser_mode,
            embedding_query_count=embedding_query_count,
            vector_candidate_count=vector_candidate_count,
            result_scores=result_scores,
            **kwargs,
        )

    monkeypatch.setattr(search_main, "fetch_vector_results", fake_fetch)
    monkeypatch.setattr(search_main, "record_retrieval_search", capture_retrieval_search)

    response = asyncio.run(search_main.search(SearchRequest(query="  neural retrieval  ", limit=3)))

    assert response["query"] == "neural retrieval"
    assert response["total"] == 3
    assert [result["id"] for result in response["results"]] == [1, 2, 3]
    assert fetch_calls == [
        ((1.0,), 9, 2018, 2024),
        ((2.0,), 9, 2018, 2024),
    ]

    first = response["results"][0]
    assert first["matched_query"] == "secondary query"
    assert first["matched_queries"] == ["primary query", "secondary query"]
    assert first["best_rank"] == 2
    assert first["cosine_distance"] == pytest.approx(0.02)
    assert first["cosine_similarity"] == pytest.approx(0.98)
    assert first["topic_boost"] == pytest.approx(0.04)
    assert first["ranking_boost"] == pytest.approx(0.02)
    assert first["coverage_boost"] == pytest.approx(0.006)
    assert first["score"] == pytest.approx(1.046)
    assert first["date"] == "2022-06-15T10:30:00"

    second = response["results"][1]
    assert second["matched_query"] == "primary query"
    assert second["matched_queries"] == ["primary query", "secondary query"]
    assert second["best_rank"] == 2
    assert second["cosine_similarity"] == pytest.approx(0.95)
    assert second["topic_boost"] == pytest.approx(0.01)
    assert second["ranking_boost"] == pytest.approx(0.02)
    assert second["coverage_boost"] == pytest.approx(0.006)
    assert second["score"] == pytest.approx(0.986)

    third = response["results"][2]
    assert third["score"] == pytest.approx(0.803)
    assert recorded_search == {
        "service_name": "search-service",
        "parser_mode": "llm",
        "embedding_query_count": 2,
        "vector_candidate_count": 5,
        "result_scores": [1.046, 0.986, 0.803],
        "result_count": 3,
        "search_mode": "semantic",
        "author_filter_count": 0,
    }
    parse_mock.assert_awaited_once_with("neural retrieval")
    assert embed_mock.await_count == 2


def test_explicit_author_only_search_never_parses_or_embeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_search_boundaries(monkeypatch)
    parse_mock = AsyncMock(side_effect=AssertionError("query parser must not be called"))
    embed_mock = AsyncMock(side_effect=AssertionError("embedding must not be called"))
    monkeypatch.setattr(search_main, "parse_search_query", parse_mock)
    monkeypatch.setattr(search_main, "embed_query", embed_mock)
    monkeypatch.setattr(
        search_main,
        "fetch_author_results",
        lambda limit, year_from, year_to, authors: [
            _candidate_row(
                11,
                title="Author publication",
                abstract="Structured retrieval",
                cosine_distance=0.0,
                authors=("Ime Prezime",),
            )
        ],
    )
    recorded: dict[str, Any] = {}

    def record(*_args, **kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(search_main, "record_retrieval_search", record)

    response = asyncio.run(search_main.search(SearchRequest(author_names=["Ime Prezime"], limit=5)))

    assert response["search_mode"] == "author"
    assert response["plan"]["author_names"] == ["Ime Prezime"]
    assert response["plan"]["embedding_queries"] == []
    assert response["results"][0]["score"] is None
    assert response["results"][0]["cosine_similarity"] is None
    assert response["results"][0]["matched_query"] is None
    assert recorded == {"result_count": 1, "search_mode": "author", "author_filter_count": 1}
    parse_mock.assert_not_awaited()
    embed_mock.assert_not_awaited()


def test_selected_author_id_search_never_parses_or_embeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_search_boundaries(monkeypatch)
    parse_mock = AsyncMock(side_effect=AssertionError("query parser must not be called"))
    embed_mock = AsyncMock(side_effect=AssertionError("embedding must not be called"))
    monkeypatch.setattr(search_main, "parse_search_query", parse_mock)
    monkeypatch.setattr(search_main, "embed_query", embed_mock)
    calls = []

    def fetch(limit, year_from, year_to, author_names, author_ids):
        calls.append((limit, year_from, year_to, author_names, author_ids))
        return []

    monkeypatch.setattr(search_main, "fetch_author_results", fetch)
    monkeypatch.setattr(search_main, "record_retrieval_search", lambda *_args, **_kwargs: None)

    response = asyncio.run(search_main.search(SearchRequest(author_ids=[42], limit=5)))

    assert response["search_mode"] == "author"
    assert response["plan"]["author_ids"] == [42]
    assert calls == [(5, None, None, [], [42])]
    parse_mock.assert_not_awaited()
    embed_mock.assert_not_awaited()


def test_parser_author_only_search_applies_year_without_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_search_boundaries(monkeypatch)
    monkeypatch.setattr(
        search_main,
        "parse_search_query",
        AsyncMock(
            return_value={
                "embedding_queries": [],
                "author_names": ["Ime Prezime"],
                "topic_phrases": [],
                "ranking_phrases": [],
                "year_from": 2021,
                "year_to": 2024,
                "interpreted_query": "author years",
                "used_fallback": False,
                "parser_mode": "llm",
            }
        ),
    )
    embed_mock = AsyncMock(side_effect=AssertionError("embedding must not be called"))
    monkeypatch.setattr(search_main, "embed_query", embed_mock)
    calls: list[tuple[int, int | None, int | None, list[str]]] = []

    def fetch(limit, year_from, year_to, authors):
        calls.append((limit, year_from, year_to, authors))
        return []

    monkeypatch.setattr(search_main, "fetch_author_results", fetch)
    monkeypatch.setattr(search_main, "record_retrieval_search", lambda *_args, **_kwargs: None)

    response = asyncio.run(search_main.search(SearchRequest(query="radovi autora Ime Prezime posle 2020")))

    assert response["search_mode"] == "author"
    assert calls == [(10, 2021, 2024, ["Ime Prezime"])]
    embed_mock.assert_not_awaited()


def test_hybrid_search_embeds_only_topic_and_merges_explicit_authors_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_search_boundaries(monkeypatch)
    monkeypatch.setattr(
        search_main,
        "parse_search_query",
        AsyncMock(
            return_value={
                "embedding_queries": ["Ime Prezime digitalna transformacija"],
                "author_names": ["ime prezime", "Drugi Autor"],
                "topic_phrases": ["Ime Prezime", "digitalna transformacija"],
                "ranking_phrases": [],
                "year_from": 2021,
                "year_to": None,
                "interpreted_query": "hybrid",
                "used_fallback": False,
                "parser_mode": "llm",
            }
        ),
    )
    embed_mock = AsyncMock(return_value=[1.0])
    monkeypatch.setattr(search_main, "embed_query", embed_mock)
    calls: list[tuple[int | None, int | None, list[str]]] = []

    def fetch(_vector, _limit, year_from, year_to, authors):
        calls.append((year_from, year_to, authors))
        return []

    monkeypatch.setattr(search_main, "fetch_vector_results", fetch)
    monkeypatch.setattr(search_main, "record_retrieval_search", lambda *_args, **_kwargs: None)

    response = asyncio.run(
        search_main.search(
            SearchRequest(query="digitalna transformacija posle 2020", author_names=["Ime Prezime"])
        )
    )

    assert response["search_mode"] == "hybrid"
    assert response["plan"]["author_names"] == ["Ime Prezime", "Drugi Autor"]
    assert response["plan"]["embedding_queries"] == ["digitalna transformacija"]
    assert response["plan"]["topic_phrases"] == ["digitalna transformacija"]
    embed_mock.assert_awaited_once_with("digitalna transformacija")
    assert calls == [(2021, None, ["Ime Prezime", "Drugi Autor"])]


def test_search_caps_coverage_boost_for_many_matching_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_search_boundaries(monkeypatch)
    monkeypatch.setattr(search_main, "TOPIC_TITLE_BOOST", 0.04)
    monkeypatch.setattr(search_main, "TOPIC_ABSTRACT_BOOST", 0.01)
    monkeypatch.setattr(search_main, "RANKING_PHRASE_BOOST", 0.02)
    monkeypatch.setattr(search_main, "QUERY_COVERAGE_BOOST", 0.003)

    embedding_queries = [f"query {index}" for index in range(1, 7)]
    monkeypatch.setattr(
        search_main,
        "parse_search_query",
        AsyncMock(
            return_value={
                "embedding_queries": embedding_queries,
                "topic_phrases": [],
                "ranking_phrases": [],
                "year_from": None,
                "year_to": None,
                "interpreted_query": "coverage cap",
                "used_fallback": False,
                "parser_mode": "llm",
            }
        ),
    )
    monkeypatch.setattr(search_main, "embed_query", AsyncMock(side_effect=[[float(i)] for i in range(6)]))
    monkeypatch.setattr(
        search_main,
        "fetch_vector_results",
        lambda *_args, **_kwargs: [
            _candidate_row(
                7,
                title="Coverage only",
                abstract="No phrase boosts",
                cosine_distance=0.25,
            )
        ],
    )
    monkeypatch.setattr(search_main, "record_retrieval_search", lambda *_args, **_kwargs: None)

    response = asyncio.run(search_main.search(SearchRequest(query="coverage", limit=1)))

    result = response["results"][0]
    assert result["matched_queries"] == embedding_queries
    assert result["coverage_boost"] == pytest.approx(0.015)
    assert result["score"] == pytest.approx(0.765)
