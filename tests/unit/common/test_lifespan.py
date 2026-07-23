from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from microservices.catalog_service import main as catalog_main
from microservices.job_service import main as job_main
from microservices.query_service import main as query_main
from microservices.search_service import main as search_main


@pytest.mark.parametrize("module", [catalog_main, job_main])
def test_database_schema_is_initialized_through_lifespan(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(module, "ensure_schema", lambda: calls.append("schema"))

    with TestClient(module.app):
        assert calls == ["schema"]


def test_query_lifespan_warms_up_and_registers_model_info(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(query_main, "warm_up_llm", lambda: calls.append(("warmup", None)))
    monkeypatch.setattr(
        query_main,
        "set_retrieval_model_info",
        lambda service, component, info: calls.append(("model_info", (service, component, info))),
    )

    with TestClient(query_main.app):
        pass

    assert calls[0] == ("warmup", None)
    assert calls[1][0] == "model_info"
    assert calls[1][1] == (
        "query-service",
        "query_parser",
        {
            "llm_provider": query_main.LLM_PROVIDER,
            "llm_model": query_main.LLM_MODEL,
            "llm_timeout_seconds": query_main.LLM_TIMEOUT,
            "llm_warmup_enabled": query_main.LLM_WARMUP_ENABLED,
        },
    )


def test_search_lifespan_initializes_observability_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(search_main, "ensure_schema", lambda: calls.append("schema"))
    monkeypatch.setattr(
        search_main,
        "set_retrieval_model_info",
        lambda *_args: calls.append("ranking_config"),
    )
    sentinel: Callable[[], None] = lambda: None
    monkeypatch.setattr(search_main, "refresh_retrieval_index_metrics", sentinel)

    with TestClient(search_main.app):
        assert calls == ["schema", "ranking_config"]
        assert search_main.app.state.collect_metrics is sentinel


def test_lifespan_propagates_startup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise RuntimeError("schema initialization failed")

    monkeypatch.setattr(catalog_main, "ensure_schema", fail)

    with pytest.raises(RuntimeError, match="schema initialization failed"), TestClient(catalog_main.app):
        pass
