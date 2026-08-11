from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
    password = "bootstrap-password"
    monkeypatch.setattr(auth, "get_connection", postgres_connection_factory)
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret-that-is-at-least-thirty-two-bytes-long")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    auth_main.app.dependency_overrides[require_api_token] = lambda: None

    created = auth.bootstrap_admin_user(" FirstAdmin ", password)
    assert created["username"] == "firstadmin"

    with pytest.raises(auth.AdminAlreadyExistsError):
        auth.bootstrap_admin_user("otheradmin", "another-password")

    database = postgres_connection_factory()
    try:
        with database.cursor() as cursor:
            cursor.execute("SELECT username, password_hash FROM admin_user")
            username, stored_hash = cursor.fetchone()
    finally:
        database.close()

    assert username == "firstadmin"
    assert stored_hash != password
    assert stored_hash.startswith("pbkdf2_sha256$")
    assert auth.verify_password(password, stored_hash)

    try:
        with TestClient(auth_main.app) as client:
            public_registration = client.post(
                "/auth/register",
                json={"username": "otheradmin", "password": "another-password"},
            )
            assert public_registration.status_code == 404

            invalid = client.post(
                "/auth/login",
                json={"username": "firstadmin", "password": "wrong-password"},
            )
            assert invalid.status_code == 401

            logged_in = client.post(
                "/auth/login",
                json={"username": "FIRSTADMIN", "password": password},
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


def test_concurrent_bootstrap_attempts_create_only_one_administrator(
    postgres_connection_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_connection", postgres_connection_factory)
    barrier = Barrier(2)

    def attempt(username: str) -> tuple[str, dict[str, Any] | None]:
        barrier.wait(timeout=10)
        try:
            return "created", auth.bootstrap_admin_user(username, "bootstrap-password")
        except auth.AdminAlreadyExistsError:
            return "exists", None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt, "first-admin"),
            executor.submit(attempt, "second-admin"),
        ]
        outcomes = [future.result(timeout=30) for future in futures]

    assert [status for status, _result in outcomes].count("created") == 1
    assert [status for status, _result in outcomes].count("exists") == 1

    database = postgres_connection_factory()
    try:
        with database.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), MIN(username) FROM admin_user")
            count, username = cursor.fetchone()
    finally:
        database.close()

    assert count == 1
    assert username in {"first-admin", "second-admin"}
