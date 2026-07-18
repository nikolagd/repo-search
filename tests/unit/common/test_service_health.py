from __future__ import annotations

from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from microservices.auth_service import main as auth_main
from microservices.catalog_service import main as catalog_main
from microservices.job_service import main as job_main
from microservices.query_service import main as query_main


API_TOKEN = "unit-test-api-token"
AUTH_HEADERS = {"X-API-Key": API_TOKEN}
DATABASE_SERVICES = [auth_main, catalog_main, job_main]


class DatabaseCursor:
    def __init__(self, *, fail_query: bool = False) -> None:
        self.fail_query = fail_query

    def __enter__(self) -> "DatabaseCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _statement: str) -> None:
        if self.fail_query:
            raise RuntimeError("database query failed")

    def fetchone(self) -> tuple[int]:
        return (1,)


class DatabaseConnection:
    def __init__(self, *, fail_query: bool = False) -> None:
        self.cursor_instance = DatabaseCursor(fail_query=fail_query)
        self.closed = False

    def cursor(self) -> DatabaseCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


def client_for(module: ModuleType) -> TestClient:
    return TestClient(module.app)


@pytest.mark.parametrize("service", DATABASE_SERVICES, ids=lambda module: module.app.title)
def test_database_service_liveness_is_public(service: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    client = client_for(service)
    try:
        response = client.get("/live")
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("service", DATABASE_SERVICES, ids=lambda module: module.app.title)
def test_database_service_readiness_is_protected(service: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    client = client_for(service)
    try:
        response = client.get("/ready")
    finally:
        client.close()

    assert response.status_code == 401


@pytest.mark.parametrize("failure", ["connect", "query"])
@pytest.mark.parametrize("service", DATABASE_SERVICES, ids=lambda module: module.app.title)
def test_database_service_readiness_and_health_report_failures(
    service: ModuleType,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    connections: list[DatabaseConnection] = []

    def connection_factory() -> DatabaseConnection:
        if failure == "connect":
            raise RuntimeError("database connection failed")
        connection = DatabaseConnection(fail_query=True)
        connections.append(connection)
        return connection

    monkeypatch.setattr(service, "get_connection", connection_factory)
    client = client_for(service)
    try:
        ready = client.get("/ready", headers=AUTH_HEADERS)
        health = client.get("/health", headers=AUTH_HEADERS)
    finally:
        client.close()

    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unavailable",
        "dependencies": {"database": "unavailable"},
    }
    assert health.status_code == 200
    assert health.json() == {"status": "unavailable", "database": "unavailable"}
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("service", DATABASE_SERVICES, ids=lambda module: module.app.title)
def test_database_service_readiness_succeeds_after_database_query(
    service: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    connections: list[DatabaseConnection] = []

    def connection_factory() -> DatabaseConnection:
        connection = DatabaseConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(service, "get_connection", connection_factory)
    client = client_for(service)
    try:
        response = client.get("/ready", headers=AUTH_HEADERS)
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "dependencies": {"database": "ok"}}
    assert connections and all(connection.closed for connection in connections)


def test_query_service_is_ready_without_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)

    def fail_if_warmed() -> None:
        raise AssertionError("readiness must not contact Ollama")

    monkeypatch.setattr(query_main, "warm_up_llm", fail_if_warmed)
    client = client_for(query_main)
    try:
        live = client.get("/live")
        protected = client.get("/ready")
        ready = client.get("/ready", headers=AUTH_HEADERS)
        health = client.get("/health", headers=AUTH_HEADERS)
    finally:
        client.close()

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert protected.status_code == 401
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "dependencies": {}}
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "not-used"}
