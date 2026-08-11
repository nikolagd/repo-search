from __future__ import annotations

import pytest

from microservices.query_service.parser import extract_year_constraints, parse_query_fallback


@pytest.mark.parametrize(
    ("query", "clean_query", "year_from", "year_to"),
    [
        ("Find papers about AI after 2020", "find papers about ai", 2021, None),
        ("AI since 2019", "ai", 2019, None),
        ("AI before 2020", "ai", None, 2019),
        ("AI until 2020", "ai", None, 2020),
        ("Radovi o AI nakon 2020", "radovi o ai", 2021, None),
        ("Radovi o AI od 2019", "radovi o ai", 2019, None),
        ("Radovi o AI pre 2020", "radovi o ai", None, 2019),
        ("Radovi o AI do 2020", "radovi o ai", None, 2020),
    ],
)
def test_extract_year_constraints_uses_explicit_temporal_phrases(
    query: str,
    clean_query: str,
    year_from: int | None,
    year_to: int | None,
) -> None:
    assert extract_year_constraints(query) == {
        "clean_query": clean_query,
        "year_from": year_from,
        "year_to": year_to,
    }


def test_fallback_parser_builds_deterministic_plan_without_external_services() -> None:
    plan = parse_query_fallback("Find papers about graph neural networks after 2020")

    assert plan == {
        "embedding_queries": ["graph neural networks"],
        "semantic_query": "graph neural networks",
        "author_names": [],
        "search_mode": "semantic",
        "topic_phrases": [],
        "year_from": 2021,
        "year_to": None,
        "ranking_phrases": [],
        "interpreted_query": (
            "LLM parsing was unavailable, so I searched using: graph neural networks"
        ),
        "used_fallback": True,
        "parser_mode": "fallback",
    }


@pytest.mark.parametrize(
    "query",
    [
        "Find papers about AI after 2020 before 2010",
        "Radovi o AI nakon 2020 pre 2010",
    ],
)
def test_fallback_parser_swaps_reversed_year_boundaries(query: str) -> None:
    plan = parse_query_fallback(query)

    assert plan["embedding_queries"] == ["ai"]
    assert plan["semantic_query"] == "ai"
    assert plan["year_from"] == 2009
    assert plan["year_to"] == 2021


def test_fallback_parser_preserves_original_query_when_only_a_filler_remains() -> None:
    plan = parse_query_fallback("Find papers about")

    assert plan["embedding_queries"] == ["Find papers about"]
    assert plan["semantic_query"] == "Find papers about"
    assert plan["used_fallback"] is True


@pytest.mark.parametrize(
    ("query", "authors", "embedding_queries", "search_mode"),
    [
        ("autor: Ime Prezime", ["Ime Prezime"], [], "author"),
        ("autor: P. Petrović", ["P. Petrović"], [], "author"),
        ("radovi autora Ime Prezime", ["Ime Prezime"], [], "author"),
        ("publikacije autora Ime Prezime", ["Ime Prezime"], [], "author"),
        ("papers by Jane Doe", ["Jane Doe"], [], "author"),
        (
            "radovi autora Ime Prezime o digitalnoj transformaciji posle 2020",
            ["Ime Prezime"],
            ["digitalnoj transformaciji"],
            "hybrid",
        ),
        ("papers by Jane Doe about graph retrieval", ["Jane Doe"], ["graph retrieval"], "hybrid"),
    ],
)
def test_fallback_parser_extracts_only_explicit_author_forms(
    query: str,
    authors: list[str],
    embedding_queries: list[str],
    search_mode: str,
) -> None:
    plan = parse_query_fallback(query)

    assert plan["author_names"] == authors
    assert plan["embedding_queries"] == embedding_queries
    assert plan["search_mode"] == search_mode
    assert all(author.casefold() not in " ".join(embedding_queries).casefold() for author in authors)


def test_fallback_parser_does_not_guess_capitalized_phrases_are_authors() -> None:
    plan = parse_query_fallback("Digital Transformation in Higher Education")

    assert plan["author_names"] == []
    assert plan["embedding_queries"] == ["digital transformation in higher education"]
