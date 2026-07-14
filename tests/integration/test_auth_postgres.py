from __future__ import annotations

from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from microservices.auth_service import auth
from microservices.auth_service import main as auth_main
from microservices.common.security import require_api_token


def test_real_auth_database_bootstrap_login_session_and_logout(
    postgres_connection_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_connection", postgres_connection_factory)
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret-that-is-at-least-thirty-two-bytes-long")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    auth_main.app.dependency_overrides[require_api_token] = lambda: None

    try:
        with TestClient(auth_main.app) as client:
            registered = client.post(
                "/auth/register",
                json={"username": " FirstAdmin ", "password": "bootstrap-password"},
            )
            assert registered.status_code == 200
            assert registered.json()["admin"]["username"] == "firstadmin"

            registration_closed = client.post(
                "/auth/register",
                json={"username": "otheradmin", "password": "bootstrap-password"},
            )
            assert registration_closed.status_code == 403

            invalid = client.post(
                "/auth/login",
                json={"username": "firstadmin", "password": "wrong-password"},
            )
            assert invalid.status_code == 401

            logged_in = client.post(
                "/auth/login",
                json={"username": "FIRSTADMIN", "password": "bootstrap-password"},
            )
            assert logged_in.status_code == 200
            session_token = logged_in.cookies[auth.ADMIN_COOKIE_NAME]
            csrf_token = logged_in.cookies[auth.CSRF_COOKIE_NAME]

            session = client.get(
                "/auth/me",
                headers={"cookie": f"{auth.ADMIN_COOKIE_NAME}={session_token}"},
            )
            assert session.status_code == 200
            assert session.json()["username"] == "firstadmin"

            logged_out = client.post(
                "/auth/logout",
                headers={
                    auth.CSRF_HEADER_NAME: csrf_token,
                    "cookie": (
                        f"{auth.ADMIN_COOKIE_NAME}={session_token}; "
                        f"{auth.CSRF_COOKIE_NAME}={csrf_token}"
                    ),
                },
            )
            assert logged_out.status_code == 200
            assert any(
                value.startswith(f"{auth.ADMIN_COOKIE_NAME}=") and "Max-Age=0" in value
                for value in logged_out.headers.get_list("set-cookie")
            )
    finally:
        auth_main.app.dependency_overrides.clear()
