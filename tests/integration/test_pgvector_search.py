from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

os.environ["OTEL_TRACING_ENABLED"] = "false"


VECTOR_DIMENSIONS = 1024


def _vector_literal(*, first: float, second: float) -> str:
    values = [first, second, *([0.0] * (VECTOR_DIMENSIONS - 2))]
    return "[" + ",".join(str(value) for value in values) + "]"


def test_fetch_vector_results_uses_pgvector_and_year_filters(
    pgvector_connection_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from microservices.catalog_service import main as catalog_main
    from microservices.search_service import main as search_main

    monkeypatch.setattr(catalog_main, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(search_main, "get_connection", pgvector_connection_factory)
    catalog_main.ensure_schema()

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO repository (name, oai_endpoint)
                VALUES (%s, %s)
                RETURNING id
                """,
                ("Integration repository", "https://example.test/oai"),
            )
            repository_id = cursor.fetchone()[0]

            publication_ids: dict[str, int] = {}
            publications = [
                (
                    "near",
                    "Nearest publication",
                    "A matching vector inside the requested years.",
                    datetime(2021, 5, 1),
                    _vector_literal(first=1.0, second=0.0),
                ),
                (
                    "far",
                    "Farther publication",
                    "An orthogonal vector inside the requested years.",
                    datetime(2020, 6, 1),
                    _vector_literal(first=0.0, second=1.0),
                ),
                (
                    "outside",
                    "Excluded by year",
                    "A nearest vector outside the requested years.",
                    datetime(2010, 1, 1),
                    _vector_literal(first=1.0, second=0.0),
                ),
            ]
            for key, title, abstract, published_at, embedding in publications:
                cursor.execute(
                    """
                    INSERT INTO publication (
                        repository_id, title, abstract, source_url, date, oai_identifier, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    RETURNING id
                    """,
                    (
                        repository_id,
                        title,
                        abstract,
                        f"https://example.test/{key}",
                        published_at,
                        f"oai:test:{key}",
                        embedding,
                    ),
                )
                publication_ids[key] = cursor.fetchone()[0]

            for author_name in ("Zed Author", "Alice Author"):
                cursor.execute("INSERT INTO author (full_name) VALUES (%s) RETURNING id", (author_name,))
                author_id = cursor.fetchone()[0]
                cursor.execute(
                    "INSERT INTO publication_author (publication_id, author_id) VALUES (%s, %s)",
                    (publication_ids["near"], author_id),
                )
        connection.commit()
    finally:
        connection.close()

    query_vector = [1.0, 0.0, *([0.0] * (VECTOR_DIMENSIONS - 2))]
    rows = search_main.fetch_vector_results(query_vector, limit=10, year_from=2020, year_to=2022)

    assert [row[0] for row in rows] == [publication_ids["near"], publication_ids["far"]]
    assert rows[0][1] == "Nearest publication"
    assert rows[0][5] == pytest.approx(0.0, abs=1e-6)
    assert rows[0][6] == "Integration repository"
    assert rows[0][7] == ["Alice Author", "Zed Author"]
    assert rows[1][5] == pytest.approx(1.0, abs=1e-6)
    assert publication_ids["outside"] not in {row[0] for row in rows}
