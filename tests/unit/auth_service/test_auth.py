from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from microservices.auth_service import auth
from microservices.auth_service import main as auth_main
from microservices.common.security import require_api_token


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret-that-is-at-least-thirty-two-bytes-long")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    auth_main.app.dependency_overrides[require_api_token] = lambda: None
    with TestClient(auth_main.app) as client:
        yield client
    auth_main.app.dependency_overrides.clear()


def test_administrator_login_sets_session_and_csrf_cookies(auth_client, monkeypatch) -> None:
    monkeypatch.setattr(
        auth_main,
        "authenticate_admin_user",
        lambda username, password: {"id": 7, "username": "admin"}
        if (username, password) == ("Admin", "correct-password")
        else None,
    )

    response = auth_client.post(
        "/auth/login",
        json={"username": "Admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.json()["admin"] == {"id": 7, "username": "admin"}
    assert auth.ADMIN_COOKIE_NAME in response.cookies
    assert auth.CSRF_COOKIE_NAME in response.cookies


def test_administrator_login_rejects_invalid_credentials(auth_client, monkeypatch) -> None:
    monkeypatch.setattr(auth_main, "authenticate_admin_user", lambda _username, _password: None)

    response = auth_client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_valid_session_is_accepted_and_refreshes_csrf_cookie(auth_client) -> None:
    token, _ = auth.build_access_token({"id": 3, "username": "admin"})

    response = auth_client.get(
        "/auth/me",
        headers={"cookie": f"{auth.ADMIN_COOKIE_NAME}={token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"id": 3, "username": "admin"}
    assert auth.CSRF_COOKIE_NAME in response.cookies


@pytest.mark.parametrize("token", ["not-a-jwt", None])
def test_invalid_or_missing_session_is_rejected(auth_client, token: str | None) -> None:
    headers = {"cookie": f"{auth.ADMIN_COOKIE_NAME}={token}"} if token else {}

    response = auth_client.get("/auth/me", headers=headers)

    assert response.status_code == 401


def test_expired_session_is_rejected(auth_client) -> None:
    secret = auth.get_jwt_secret()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "admin",
            "uid": 3,
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        secret,
        algorithm=auth.JWT_ALGORITHM,
    )

    response = auth_client.get(
        "/auth/me",
        headers={"cookie": f"{auth.ADMIN_COOKIE_NAME}={token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired admin token."


def test_logout_clears_session_and_csrf_cookies(auth_client) -> None:
    csrf_token = "matching-csrf-token"

    response = auth_client.post(
        "/auth/logout",
        headers={
            auth.CSRF_HEADER_NAME: csrf_token,
            "cookie": (
                f"{auth.ADMIN_COOKIE_NAME}=session; "
                f"{auth.CSRF_COOKIE_NAME}={csrf_token}"
            ),
        },
    )

    assert response.status_code == 200
    set_cookie = response.headers.get_list("set-cookie")
    assert any(value.startswith(f"{auth.ADMIN_COOKIE_NAME}=") and "Max-Age=0" in value for value in set_cookie)
    assert any(value.startswith(f"{auth.CSRF_COOKIE_NAME}=") and "Max-Age=0" in value for value in set_cookie)


def test_public_administrator_registration_endpoint_is_unavailable(auth_client) -> None:
    response = auth_client.post(
        "/auth/register",
        json={"username": " FirstAdmin ", "password": "bootstrap-password"},
    )

    assert response.status_code == 404
