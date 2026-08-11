from __future__ import annotations

import json
from typing import Any

import pytest

from microservices.query_service import llm_parser


def test_parse_json_response_accepts_whitespace_around_json_object() -> None:
    assert llm_parser.parse_json_response('  \n {"embedding_queries": ["AI"]} \t') == {
        "embedding_queries": ["AI"]
    }


@pytest.mark.parametrize(
    "response_text",
    [
        "",
        "not JSON",
        '```json\n{"embedding_queries": ["AI"]}\n```',
        '{"embedding_queries": ["AI"]',
    ],
)
def test_parse_json_response_rejects_invalid_llm_text(response_text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        llm_parser.parse_json_response(response_text)


def test_parse_query_llm_returns_mocked_plan_without_http(
    monkeypatch: pytest.MonkeyPatch,
    valid_query_plan: dict,
) -> None:
    prompts: list[str] = []

    def call(prompt: str) -> dict:
        prompts.append(prompt)
        return valid_query_plan

    monkeypatch.setattr(llm_parser, "call_llm_json", call)

    assert llm_parser.parse_query_llm("graph neural networks after 2020") == valid_query_plan
    assert len(prompts) == 1
    assert "graph neural networks after 2020" in prompts[0]
    assert "Return ONLY valid JSON" in prompts[0]
    assert '"author_names": string[]' in prompts[0]
    assert '"author_match": "any" | "all"' in prompts[0]
    assert 'Set author_match to "any"' in prompts[0]
    assert 'Set author_match to "all" only' in prompts[0]
    assert "never in embedding_queries" in prompts[0]


def test_parse_query_llm_returns_none_and_records_failure_for_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []

    def invalid_response(_prompt: str) -> dict:
        raise json.JSONDecodeError("invalid response", "not-json", 0)

    monkeypatch.setattr(llm_parser, "call_llm_json", invalid_response)
    monkeypatch.setattr(
        llm_parser,
        "emit_app_event",
        lambda event, service, **fields: events.append((event, service, fields)),
    )

    assert llm_parser.parse_query_llm("AI") is None
    assert len(events) == 1
    event, service, fields = events[0]
    assert event == "query.parser_failed"
    assert service == "query-service"
    assert fields["fallback_used"] is True
    assert fields["query_length"] == 2
    assert "invalid response" in fields["error"]


def test_repair_query_plan_submits_reason_and_bad_plan_without_http(
    monkeypatch: pytest.MonkeyPatch,
    valid_query_plan: dict,
) -> None:
    prompts: list[str] = []

    def call(prompt: str) -> dict:
        prompts.append(prompt)
        return valid_query_plan

    monkeypatch.setattr(llm_parser, "call_llm_json", call)

    repaired = llm_parser.repair_query_plan(
        "AI after 2020",
        {"embedding_queries": []},
        "embedding_queries must be a non-empty list.",
    )

    assert repaired == valid_query_plan
    assert len(prompts) == 1
    assert "AI after 2020" in prompts[0]
    assert "embedding_queries must be a non-empty list." in prompts[0]
    assert '"embedding_queries": []' in prompts[0]
    assert '"author_names": string[]' in prompts[0]
    assert '"author_match": "any" | "all"' in prompts[0]
    assert 'author_match must be exactly "any" or "all"' in prompts[0]
    assert "embedding_queries may be empty only when author_names is non-empty" in prompts[0]


def test_repair_query_plan_returns_none_and_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []

    def failed_repair(_prompt: str) -> dict:
        raise RuntimeError("repair unavailable")

    monkeypatch.setattr(llm_parser, "call_llm_json", failed_repair)
    monkeypatch.setattr(
        llm_parser,
        "emit_app_event",
        lambda event, service, **fields: events.append((event, service, fields)),
    )

    repaired = llm_parser.repair_query_plan(
        "AI",
        {"embedding_queries": []},
        "missing embedding query",
    )

    assert repaired is None
    assert len(events) == 1
    event, service, fields = events[0]
    assert event == "query.parser_failed"
    assert service == "query-service"
    assert fields["fallback_used"] is True
    assert fields["repair_reason"] == "missing embedding query"
    assert "repair unavailable" in fields["error"]
