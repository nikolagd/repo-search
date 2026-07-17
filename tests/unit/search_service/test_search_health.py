from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from microservices.search_service import main as search_main


API_TOKEN = "unit-test-api-token"
AUTH_HEADERS = {"X-API-Key": API_TOKEN}


class DatabaseCursor:
    def __enter__(self) -> "DatabaseCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _statement: str) -> None:
        return None

    def fetchone(self) -> tuple[int]:
        return (1,)


class DatabaseConnection:
    def __init__(self) -> None:
        self.closed = False

    def cursor(self) -> DatabaseCursor:
        return DatabaseCursor()

    def close(self) -> None:
        self.closed = True


def install_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database_available: bool = True,
    embedding_result: int | Exception = 200,
) -> list[str]:
    if database_available:
        monkeypatch.setattr(search_main, "get_connection", DatabaseConnection)
    else:
        monkeypatch.setattr(
            search_main,
            "get_connection",
            lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
        )

    calls: list[str] = []

    async def fake_request(_client: object, method: str, url: str, **_kwargs: object) -> httpx.Response:
        calls.append(url)
        if isinstance(embedding_result, Exception):
            raise embedding_result
        request = httpx.Request(method, url)
        return httpx.Response(embedding_result, request=request)

    monkeypatch.setattr(search_main, "observed_async_request", fake_request)
    return calls


def request_search_health(
    path: str,
    *,
    headers: dict[str, str] | None = AUTH_HEADERS,
) -> httpx.Response:
    client = TestClient(search_main.app)
    try:
        return client.get(path, headers=headers)
    finally:
        client.close()


def test_search_liveness_is_public_and_readiness_is_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)

    live = request_search_health("/live", headers=None)
    ready = request_search_health("/ready", headers=None)

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 401


def test_search_readiness_requires_database_and_embedding_but_not_query_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    calls = install_dependencies(monkeypatch)

    response = request_search_health("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {"database": "ok", "embedding-service": "ok"},
    }
    assert len(calls) == 1
    assert calls[0].endswith("/ready")
    assert "query-service" not in calls[0]


def test_search_readiness_fails_when_database_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    install_dependencies(monkeypatch, database_available=False)

    response = request_search_health("/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"] == {
        "database": "unavailable",
        "embedding-service": "ok",
    }


@pytest.mark.parametrize(
    "embedding_result",
    [503, httpx.ConnectError("embedding transport failed")],
    ids=["non-2xx", "transport-error"],
)
def test_search_readiness_fails_when_embedding_is_unavailable(
    embedding_result: int | Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    install_dependencies(monkeypatch, embedding_result=embedding_result)

    response = request_search_health("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "dependencies": {"database": "ok", "embedding-service": "unavailable"},
    }


def test_search_compatibility_health_stays_200_but_reports_unavailable_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    install_dependencies(monkeypatch, embedding_result=503)

    response = request_search_health("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "database": "ok"}
