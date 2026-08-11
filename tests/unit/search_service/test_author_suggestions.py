from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["OTEL_TRACING_ENABLED"] = "false"

from microservices.common.security import require_api_token
from microservices.search_service import main as search_main


def test_author_suggestion_api_returns_stable_shape_and_empty_results(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_fetch(query: str, limit: int):
        calls.append((query, limit))
        if query == "Petrovci":
            return [{"id": 12, "display_name": "Petar Petrović", "publication_count": 4}]
        return []

    monkeypatch.setattr(search_main, "fetch_author_suggestions", fake_fetch)
    search_main.app.dependency_overrides[require_api_token] = lambda: None
    try:
        client = TestClient(search_main.app)
        response = client.get("/authors/suggestions", params={"q": "Petrovci", "limit": 4})
        no_match = client.get("/authors/suggestions", params={"q": "Nema Rezultata", "limit": 4})
    finally:
        search_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [{"id": 12, "display_name": "Petar Petrović", "publication_count": 4}]
    }
    assert no_match.status_code == 200
    assert no_match.json() == {"suggestions": []}
    assert calls == [("Petrovci", 4), ("Nema Rezultata", 4)]


def test_author_suggestion_api_validates_query_and_limit() -> None:
    search_main.app.dependency_overrides[require_api_token] = lambda: None
    try:
        client = TestClient(search_main.app)
        assert client.get("/authors/suggestions", params={"q": "", "limit": 4}).status_code == 422
        assert client.get("/authors/suggestions", params={"q": "..", "limit": 4}).status_code == 422
        assert client.get("/authors/suggestions", params={"q": "Petar", "limit": 0}).status_code == 422
        assert client.get("/authors/suggestions", params={"q": "Petar", "limit": 21}).status_code == 422
    finally:
        search_main.app.dependency_overrides.clear()
