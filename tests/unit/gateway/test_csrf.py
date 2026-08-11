from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from microservices.common.security import require_api_token
from microservices.gateway import main as gateway


def make_request(method: str, *, csrf_cookie: str | None = None, csrf_header: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if csrf_cookie is not None:
        headers.append((b"cookie", f"{gateway.CSRF_COOKIE_NAME}={csrf_cookie}".encode()))
    if csrf_header is not None:
        headers.append((gateway.CSRF_HEADER_NAME.lower().encode(), csrf_header.encode()))
    return Request({"type": "http", "method": method, "path": "/api/admin/repositories", "headers": headers})


@pytest.fixture
def accepted_auth(monkeypatch: pytest.MonkeyPatch):
    calls = []

    async def fake_request(_client, method, url, **kwargs):
        calls.append((method, url, kwargs))
        request = httpx.Request(method, url)
        return httpx.Response(200, json={"id": 1, "username": "admin"}, request=request)

    monkeypatch.setattr(gateway, "observed_async_request", fake_request)
    return calls


def test_state_changing_admin_request_rejects_missing_csrf(accepted_auth) -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(gateway.require_admin_request(make_request("POST")))

    assert error.value.status_code == 403
    assert accepted_auth == []


def test_state_changing_admin_request_rejects_mismatched_csrf(accepted_auth) -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            gateway.require_admin_request(
                make_request("POST", csrf_cookie="cookie-token", csrf_header="header-token")
            )
        )

    assert error.value.status_code == 403
    assert accepted_auth == []


def test_state_changing_admin_request_accepts_matching_csrf(accepted_auth) -> None:
    asyncio.run(
        gateway.require_admin_request(
            make_request("POST", csrf_cookie="same-token", csrf_header="same-token")
        )
    )

    assert len(accepted_auth) == 1
    assert accepted_auth[0][0] == "GET"
    assert accepted_auth[0][1].endswith("/auth/me")


def test_read_only_admin_request_does_not_require_csrf(accepted_auth) -> None:
    asyncio.run(gateway.require_admin_request(make_request("GET")))

    assert len(accepted_auth) == 1


def test_public_read_only_route_is_unaffected_by_admin_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy(_request, _base_url, path):
        return JSONResponse({"path": path})

    monkeypatch.setattr(gateway, "proxy_request", fake_proxy)
    gateway.app.dependency_overrides[require_api_token] = lambda: None
    try:
        with TestClient(gateway.app) as client:
            response = client.get("/api/repositories")
    finally:
        gateway.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"path": "/repositories"}


def test_author_suggestion_route_proxies_to_search_service(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy(request, base_url, path):
        return JSONResponse({"base_url": base_url, "path": path, "query": request.url.query})

    monkeypatch.setattr(gateway, "proxy_request", fake_proxy)
    gateway.app.dependency_overrides[require_api_token] = lambda: None
    try:
        with TestClient(gateway.app) as client:
            response = client.get("/api/authors/suggestions?q=Petrovci&limit=5")
    finally:
        gateway.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "base_url": gateway.SEARCH_SERVICE_URL,
        "path": "/authors/suggestions",
        "query": "q=Petrovci&limit=5",
    }


def test_search_route_preserves_optional_author_match_override(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy(request, base_url, path):
        return JSONResponse(
            {
                "base_url": base_url,
                "path": path,
                "body": json.loads((await request.body()).decode("utf-8")),
            }
        )

    monkeypatch.setattr(gateway, "proxy_request", fake_proxy)
    gateway.app.dependency_overrides[require_api_token] = lambda: None
    try:
        with TestClient(gateway.app) as client:
            response = client.post(
                "/api/search",
                json={
                    "query": "papers by Jane Doe and John Smith",
                    "author_match": "all",
                },
            )
    finally:
        gateway.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "base_url": gateway.SEARCH_SERVICE_URL,
        "path": "/search",
        "body": {
            "query": "papers by Jane Doe and John Smith",
            "author_match": "all",
        },
    }
