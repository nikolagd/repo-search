from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from microservices.gateway import main as gateway


API_TOKEN = "unit-test-api-token"
AUTH_HEADERS = {"X-API-Key": API_TOKEN}


def install_upstream_results(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: dict[str, int | Exception] | None = None,
) -> list[tuple[str, str]]:
    outcomes = outcomes or {}
    calls: list[tuple[str, str]] = []

    async def fake_request(_client: object, method: str, url: str, **kwargs: object) -> httpx.Response:
        service_name = str(kwargs["upstream_service"])
        calls.append((service_name, url))
        outcome = outcomes.get(service_name, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=httpx.Request(method, url))

    monkeypatch.setattr(gateway, "observed_async_request", fake_request)
    return calls


def request_gateway(path: str, headers: dict[str, str] | None = AUTH_HEADERS) -> httpx.Response:
    client = TestClient(gateway.app)
    try:
        return client.get(path, headers=headers)
    finally:
        client.close()


def test_gateway_liveness_is_public_and_readiness_is_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)

    live = request_gateway("/api/live", headers=None)
    ready = request_gateway("/api/ready", headers=None)

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 401


def test_gateway_readiness_requires_all_public_application_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    calls = install_upstream_results(monkeypatch)

    response = request_gateway("/api/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {
            "auth-service": "ok",
            "catalog-service": "ok",
            "search-service": "ok",
            "job-service": "ok",
        },
    }
    assert {service_name for service_name, _url in calls} == {
        "auth-service",
        "catalog-service",
        "search-service",
        "job-service",
    }
    assert all(url.endswith("/ready") for _service_name, url in calls)


@pytest.mark.parametrize(
    "outcome",
    [503, httpx.ConnectError("search transport failed")],
    ids=["non-2xx", "transport-error"],
)
def test_gateway_readiness_reports_non_2xx_and_transport_failures(
    outcome: int | Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    install_upstream_results(monkeypatch, {"search-service": outcome})

    response = request_gateway("/api/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "dependencies": {
            "auth-service": "ok",
            "catalog-service": "ok",
            "search-service": "unavailable",
            "job-service": "ok",
        },
    }


def test_gateway_compatibility_health_stays_200_and_reports_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    install_upstream_results(
        monkeypatch,
        {"auth-service": httpx.ConnectError("auth transport failed")},
    )

    response = request_gateway("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "database": "unavailable"}
