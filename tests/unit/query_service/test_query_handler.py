from __future__ import annotations

from typing import Any, Callable

import pytest

from microservices.query_service import query_handler


FIXED_CURRENT_YEAR = 2026


@pytest.fixture(autouse=True)
def fixed_current_year(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_handler, "CURRENT_YEAR", FIXED_CURRENT_YEAR)


def fail_if_called(name: str) -> Callable[..., Any]:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail(f"{name} should not have been called")

    return fail


def test_clean_string_list_normalizes_supported_values() -> None:
    assert query_handler.clean_string_list("  one query  ") == ["one query"]
    assert query_handler.clean_string_list(
        ["  one query  ", "", None, 42, "one query", "One Query"]
    ) == ["one query", "One Query"]
    assert query_handler.clean_string_list(("not", "a", "list")) == []


def test_normalize_plan_builds_canonical_query_service_response() -> None:
    raw = {
        "embedding_queries": ["  primary topic  ", "", None, "primary topic", "secondary topic"],
        "semantic_query": "ignored raw semantic query",
        "topic_phrases": "  main phrase  ",
        "year_from": 2019,
        "year_to": None,
        "ranking_phrases": ["  important term  ", 7, "", "important term"],
        "interpreted_query": "  normalized interpretation  ",
        "used_fallback": True,
        "unknown_field": "discarded",
    }

    plan, reason = query_handler.normalize_plan(raw, "primary topic")

    assert reason is None
    assert plan == {
        "embedding_queries": ["primary topic", "secondary topic"],
        "semantic_query": "primary topic",
        "author_names": [],
        "author_match": "any",
        "search_mode": "semantic",
        "topic_phrases": ["main phrase"],
        "year_from": 2019,
        "year_to": None,
        "ranking_phrases": ["important term"],
        "interpreted_query": "normalized interpretation",
        "used_fallback": False,
        "parser_mode": "llm",
    }


def test_normalize_plan_supplies_default_interpretation() -> None:
    plan, reason = query_handler.normalize_plan(
        {"embedding_queries": ["information retrieval"], "interpreted_query": "  "},
        "information retrieval",
    )

    assert reason is None
    assert plan is not None
    assert plan["interpreted_query"] == "Searching for: information retrieval"


@pytest.mark.parametrize(
    ("raw", "expected_reason"),
    [
        (None, "LLM response is not a JSON object."),
        ([], "LLM response is not a JSON object."),
        ({"embedding_queries": []}, "embedding_queries may be empty only when author_names is non-empty."),
        ({"embedding_queries": [42]}, "embedding_queries may be empty only when author_names is non-empty."),
        (
            {"embedding_queries": ["AI"], "year_from": "2020"},
            "year_from must be an integer or null.",
        ),
        (
            {"embedding_queries": ["AI"], "year_to": 2020.0},
            "year_to must be an integer or null.",
        ),
        (
            {"embedding_queries": ["AI"], "year_from": 1799},
            "Extracted year is outside the allowed range.",
        ),
        (
            {"embedding_queries": ["AI"], "year_to": FIXED_CURRENT_YEAR + 2},
            "Extracted year is outside the allowed range.",
        ),
        (
            {"embedding_queries": ["AI"], "author_match": "both"},
            'author_match must be either "any" or "all".',
        ),
    ],
)
def test_normalize_plan_rejects_invalid_llm_responses(raw: object, expected_reason: str) -> None:
    plan, reason = query_handler.normalize_plan(raw, "AI")  # type: ignore[arg-type]

    assert plan is None
    assert reason == expected_reason


def test_normalize_plan_swaps_reversed_llm_year_boundaries() -> None:
    plan, reason = query_handler.normalize_plan(
        {"embedding_queries": ["AI"], "year_from": 2024, "year_to": 2020},
        "AI",
    )

    assert reason is None
    assert plan is not None
    assert (plan["year_from"], plan["year_to"]) == (2020, 2024)


def test_explicit_years_override_llm_values_and_are_swapped_on_normalized_path() -> None:
    plan, reason = query_handler.normalize_plan(
        {"embedding_queries": ["AI"], "year_from": 1900, "year_to": 1950},
        "AI after 2020 before 2010",
    )

    assert reason is None
    assert plan is not None
    assert (plan["year_from"], plan["year_to"]) == (2009, 2021)


def test_single_explicit_boundary_clears_the_other_llm_boundary() -> None:
    plan, reason = query_handler.normalize_plan(
        {"embedding_queries": ["AI"], "year_from": 1900, "year_to": 1950},
        "AI after 2020",
    )

    assert reason is None
    assert plan is not None
    assert (plan["year_from"], plan["year_to"]) == (2021, None)


def test_parse_query_returns_valid_initial_plan_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    valid_query_plan: dict,
) -> None:
    monkeypatch.setattr(query_handler, "parse_query_llm", lambda _query: valid_query_plan)
    monkeypatch.setattr(query_handler, "repair_query_plan", fail_if_called("repair_query_plan"))
    monkeypatch.setattr(query_handler, "parse_query_fallback", fail_if_called("parse_query_fallback"))

    plan = query_handler.parse_query("information retrieval")

    assert plan["parser_mode"] == "llm"
    assert plan["used_fallback"] is False
    assert plan["semantic_query"] == "information retrieval"


def test_parse_query_repairs_invalid_non_null_plan_once(
    monkeypatch: pytest.MonkeyPatch,
    valid_query_plan: dict,
) -> None:
    bad_plan = {"embedding_queries": []}
    repair_calls: list[tuple[str, dict, str]] = []

    monkeypatch.setattr(query_handler, "parse_query_llm", lambda _query: bad_plan)

    def repair(query: str, raw: dict, reason: str) -> dict:
        repair_calls.append((query, raw, reason))
        return valid_query_plan

    monkeypatch.setattr(query_handler, "repair_query_plan", repair)
    monkeypatch.setattr(query_handler, "parse_query_fallback", fail_if_called("parse_query_fallback"))

    plan = query_handler.parse_query("information retrieval")

    assert repair_calls == [
        (
            "information retrieval",
            bad_plan,
            "embedding_queries may be empty only when author_names is non-empty.",
        )
    ]
    assert plan["parser_mode"] == "llm_repaired"
    assert plan["used_fallback"] is False


def test_parse_query_skips_repair_when_llm_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(query_handler, "parse_query_llm", lambda _query: None)
    monkeypatch.setattr(query_handler, "repair_query_plan", fail_if_called("repair_query_plan"))

    plan = query_handler.parse_query("Find papers about AI after 2020")

    assert plan["parser_mode"] == "fallback"
    assert plan["used_fallback"] is True
    assert plan["semantic_query"] == "ai"


def test_normalize_plan_allows_author_only_and_derives_mode() -> None:
    plan, reason = query_handler.normalize_plan(
        {"embedding_queries": [], "author_names": ["  Ime Prezime  "]},
        "radovi autora Ime Prezime",
    )

    assert reason is None
    assert plan is not None
    assert plan["embedding_queries"] == []
    assert plan["semantic_query"] == ""
    assert plan["author_names"] == ["Ime Prezime"]
    assert plan["search_mode"] == "author"
    assert plan["author_match"] == "any"


def test_normalize_plan_preserves_valid_all_author_match() -> None:
    plan, reason = query_handler.normalize_plan(
        {
            "embedding_queries": [],
            "author_names": ["Ime Prezime", "Drugi Autor"],
            "author_match": "all",
        },
        "zajednicki radovi autora Ime Prezime i Drugi Autor",
    )

    assert reason is None
    assert plan is not None
    assert plan["author_match"] == "all"


def test_invalid_author_match_is_repaired_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
    valid_query_plan: dict,
) -> None:
    bad_plan = {"embedding_queries": ["AI"], "author_match": "both"}
    repaired = {**valid_query_plan, "author_match": "all"}
    repair_calls: list[str] = []
    monkeypatch.setattr(query_handler, "parse_query_llm", lambda _query: bad_plan)
    monkeypatch.setattr(
        query_handler,
        "repair_query_plan",
        lambda _query, _raw, reason: repair_calls.append(reason) or repaired,
    )

    plan = query_handler.parse_query("papers coauthored by Jane Doe and John Smith")

    assert plan["author_match"] == "all"
    assert repair_calls == ['author_match must be either "any" or "all".']


def test_normalize_plan_removes_authors_from_all_topical_fields() -> None:
    plan, reason = query_handler.normalize_plan(
        {
            "embedding_queries": ["radovi autora Ime Prezime o digitalnoj transformaciji"],
            "author_names": ["Ime Prezime"],
            "topic_phrases": ["Ime Prezime", "digitalna transformacija"],
            "ranking_phrases": ["Prezime, Ime", "visoko obrazovanje"],
        },
        "radovi autora Ime Prezime o digitalnoj transformaciji",
    )

    assert reason is None
    assert plan is not None
    assert plan["embedding_queries"] == ["digitalnoj transformaciji"]
    assert plan["topic_phrases"] == ["digitalna transformacija"]
    assert plan["ranking_phrases"] == ["visoko obrazovanje"]
    assert plan["search_mode"] == "hybrid"


def test_parse_query_falls_back_when_repaired_plan_is_still_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(query_handler, "parse_query_llm", lambda _query: {"embedding_queries": []})
    monkeypatch.setattr(
        query_handler,
        "repair_query_plan",
        lambda _query, _raw, _reason: {"embedding_queries": []},
    )

    plan = query_handler.parse_query("Find papers about AI after 2020")

    assert plan["parser_mode"] == "fallback"
    assert plan["used_fallback"] is True
    assert plan["semantic_query"] == "ai"
