from __future__ import annotations

from typing import Any

from microservices.common import db


def test_get_connection_uses_bounded_default_connect_timeout(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_connect(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.delenv("DB_CONNECT_TIMEOUT", raising=False)
    monkeypatch.setattr(db.psycopg2, "connect", fake_connect)

    assert db.get_connection() is sentinel
    assert captured["connect_timeout"] == 5


def test_get_connection_accepts_explicit_connect_timeout(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "9")
    monkeypatch.setattr(db.psycopg2, "connect", fake_connect)

    db.get_connection()

    assert captured["connect_timeout"] == 9
