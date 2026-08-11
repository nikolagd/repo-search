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


def test_author_search_schema_safely_backfills_existing_author_rows(
    pgvector_connection_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from microservices.catalog_service import main as catalog_main

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE author (id SERIAL PRIMARY KEY, full_name TEXT UNIQUE)")
            cursor.execute("INSERT INTO author (full_name) VALUES (%s)", ("Ђорђе Шарић",))
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(catalog_main, "get_connection", pgvector_connection_factory)
    catalog_main.ensure_schema()

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT search_name FROM author")
            assert cursor.fetchone()[0] == "djordje saric"
            cursor.execute(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = 'idx_author_search_name_trgm'
                """
            )
            assert "gin" in cursor.fetchone()[0].lower()
    finally:
        connection.close()


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
                    "inactive",
                    "Inactive nearest publication",
                    "A matching vector that must not be searchable.",
                    datetime(2021, 5, 2),
                    _vector_literal(first=1.0, second=0.0),
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

            cursor.execute(
                "UPDATE publication SET is_active = FALSE WHERE id = %s",
                (publication_ids["inactive"],),
            )

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
    assert publication_ids["inactive"] not in {row[0] for row in rows}

    reactivated = catalog_main.upsert_publication(
        catalog_main.PublicationUpsertRequest(
            repository_id=repository_id,
            oai_identifier="oai:test:inactive",
            title="Inactive nearest publication",
            abstract="A matching vector that must not be searchable.",
            date="2021-05-02",
            source_url="https://example.test/inactive",
            authors=[],
        )
    )
    assert reactivated == {"id": publication_ids["inactive"], "reactivated": True}
    search_main.upsert_publication_embedding(
        publication_ids["inactive"],
        search_main.PublicationEmbeddingRequest(embedding=query_vector),
    )

    reactivated_rows = search_main.fetch_vector_results(
        query_vector,
        limit=10,
        year_from=2020,
        year_to=2022,
    )
    assert publication_ids["inactive"] in {row[0] for row in reactivated_rows}


def test_structured_author_search_uses_relational_filters_and_deterministic_order(
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
                "INSERT INTO repository (name, oai_endpoint) VALUES (%s, %s) RETURNING id",
                ("Author repository", "https://authors.example.test/oai"),
            )
            repository_id = cursor.fetchone()[0]
            author_ids: dict[str, int] = {}
            for name in (
                "Prezime, Ime",
                "Drugi Autor",
                "Đorđe Šarić",
                "Петар Петровић",
                "Павле Петровић",
                "Petar Petrovic",
                "Petrović X",
                "Deleted Petrovic",
            ):
                cursor.execute("INSERT INTO author (full_name) VALUES (%s) RETURNING id", (name,))
                author_ids[name] = cursor.fetchone()[0]

            publications = [
                ("reversed", "Alpha", datetime(2024, 1, 1), True, ["Prezime, Ime"], 1.0, 0.0),
                ("accent", "Accent", datetime(2023, 1, 1), True, ["Đorđe Šarić"], 0.0, 1.0),
                (
                    "multiple",
                    "Beta",
                    datetime(2022, 1, 1),
                    True,
                    ["Prezime, Ime", "Drugi Autor"],
                    0.9,
                    0.1,
                ),
                ("null-date", "Gamma", None, True, ["Prezime, Ime"], 0.8, 0.2),
                ("inactive", "Hidden", datetime(2025, 1, 1), False, ["Prezime, Ime"], 1.0, 0.0),
                (
                    "cyrillic-author",
                    "Cyrillic author",
                    datetime(2024, 2, 1),
                    True,
                    ["Петар Петровић", "Drugi Autor"],
                    0.7,
                    0.3,
                ),
                (
                    "other-initial",
                    "Other initial",
                    datetime(2024, 1, 15),
                    True,
                    ["Павле Петровић"],
                    0.6,
                    0.4,
                ),
                (
                    "visible-variant",
                    "Visible repository variant",
                    datetime(2023, 5, 1),
                    True,
                    ["Petar Petrovic"],
                    0.5,
                    0.5,
                ),
                (
                    "deleted-author",
                    "Deleted author publication",
                    datetime(2025, 2, 1),
                    False,
                    ["Deleted Petrovic"],
                    1.0,
                    0.0,
                ),
                (
                    "initial-reuse-guard",
                    "Initial must not reuse surname",
                    datetime(2025, 1, 10),
                    True,
                    ["Petrović X"],
                    0.4,
                    0.6,
                ),
            ]
            publication_ids: dict[str, int] = {}
            for key, title, published_at, active, authors, first, second in publications:
                cursor.execute(
                    """
                    INSERT INTO publication (
                        repository_id, title, abstract, source_url, date, oai_identifier, is_active, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    RETURNING id
                    """,
                    (
                        repository_id,
                        title,
                        "Author search integration fixture",
                        f"https://authors.example.test/{key}",
                        published_at,
                        f"oai:author:{key}",
                        active,
                        _vector_literal(first=first, second=second),
                    ),
                )
                publication_id = cursor.fetchone()[0]
                publication_ids[key] = publication_id
                for author in authors:
                    cursor.execute(
                        "INSERT INTO publication_author (publication_id, author_id) VALUES (%s, %s)",
                        (publication_id, author_ids[author]),
                    )
        connection.commit()
    finally:
        connection.close()

    exact = search_main.fetch_author_results(10, None, None, ["Ime Prezime"])
    assert [row[0] for row in exact] == [
        publication_ids["reversed"], publication_ids["multiple"], publication_ids["null-date"]
    ]
    assert exact[0][6] == "Author repository"
    assert exact[1][7] == ["Drugi Autor", "Prezime, Ime"]
    assert all(row[5] is None for row in exact)

    punctuation_case = search_main.fetch_author_results(10, None, None, ["PREZIME. IME"])
    assert [row[0] for row in punctuation_case] == [row[0] for row in exact]
    surname = search_main.fetch_author_results(10, None, None, ["Prezime"])
    assert [row[0] for row in surname] == [row[0] for row in exact]
    accent = search_main.fetch_author_results(10, None, None, ["Dorde Saric"])
    assert [row[0] for row in accent] == [publication_ids["accent"]]
    cyrillic_equivalent = search_main.fetch_author_results(10, None, None, ["Ђорђе Шарић"])
    assert [row[0] for row in cyrillic_equivalent] == [publication_ids["accent"]]
    latin_equivalent = search_main.fetch_author_results(10, None, None, ["Petar Petrovic"])
    assert [row[0] for row in latin_equivalent] == [
        publication_ids["cyrillic-author"],
        publication_ids["visible-variant"],
    ]
    initials = search_main.fetch_author_results(10, None, None, ["P. Petrović"])
    assert [row[0] for row in initials] == [
        publication_ids["cyrillic-author"],
        publication_ids["other-initial"],
        publication_ids["visible-variant"],
    ]
    assert publication_ids["initial-reuse-guard"] not in {row[0] for row in initials}
    reversed_initial = search_main.fetch_author_results(10, None, None, ["Petrović P."])
    assert [row[0] for row in reversed_initial] == [row[0] for row in initials]
    short_token_is_not_a_wildcard = search_main.fetch_author_results(10, None, None, ["Pe Petrović"])
    assert [row[0] for row in short_token_is_not_a_wildcard] == []
    multiple = search_main.fetch_author_results(
        10, None, None, ["Ime Prezime", "Drugi Autor"], author_match="all"
    )
    assert [row[0] for row in multiple] == [publication_ids["multiple"]]
    any_names = search_main.fetch_author_results(
        10, None, None, ["Ime Prezime", "Drugi Autor"], author_match="any"
    )
    assert {row[0] for row in any_names} == {
        publication_ids["reversed"],
        publication_ids["multiple"],
        publication_ids["null-date"],
        publication_ids["cyrillic-author"],
    }
    cyrillic_multiple = search_main.fetch_author_results(
        10, None, None, ["Petar Petrovic", "Drugi Autor"], author_match="all"
    )
    assert [row[0] for row in cyrillic_multiple] == [publication_ids["cyrillic-author"]]
    year_filtered = search_main.fetch_author_results(10, 2024, 2024, ["Ime Prezime"])
    assert [row[0] for row in year_filtered] == [publication_ids["reversed"]]
    assert publication_ids["inactive"] not in {row[0] for row in exact}

    one_any = search_main.fetch_author_results(
        10, None, None, ["Ime Prezime"], author_match="any"
    )
    one_all = search_main.fetch_author_results(
        10, None, None, ["Ime Prezime"], author_match="all"
    )
    assert [row[0] for row in one_any] == [row[0] for row in one_all]

    id_any = search_main.fetch_author_results(
        10,
        None,
        None,
        [],
        [author_ids["Prezime, Ime"], author_ids["Drugi Autor"]],
        "any",
    )
    id_all = search_main.fetch_author_results(
        10,
        None,
        None,
        [],
        [author_ids["Prezime, Ime"], author_ids["Drugi Autor"]],
        "all",
    )
    assert {row[0] for row in id_any} == {
        publication_ids["reversed"],
        publication_ids["multiple"],
        publication_ids["null-date"],
        publication_ids["cyrillic-author"],
    }
    assert [row[0] for row in id_all] == [publication_ids["multiple"]]

    mixed_any = search_main.fetch_author_results(
        10, None, None, ["Ime Prezime"], [author_ids["Petar Petrovic"]], "any"
    )
    mixed_all = search_main.fetch_author_results(
        10, None, None, ["Ime Prezime"], [author_ids["Petar Petrovic"]], "all"
    )
    assert {row[0] for row in mixed_any} == {
        publication_ids["reversed"],
        publication_ids["multiple"],
        publication_ids["null-date"],
        publication_ids["visible-variant"],
    }
    assert mixed_all == []

    query_vector = [1.0, 0.0, *([0.0] * (VECTOR_DIMENSIONS - 2))]
    hybrid = search_main.fetch_vector_results(query_vector, 10, None, None, ["Drugi Autor"])
    assert [row[0] for row in hybrid] == [
        publication_ids["multiple"],
        publication_ids["cyrillic-author"],
    ]
    hybrid_all = search_main.fetch_vector_results(
        query_vector,
        10,
        None,
        None,
        ["Ime Prezime", "Drugi Autor"],
        author_match="all",
    )
    hybrid_any = search_main.fetch_vector_results(
        query_vector,
        10,
        None,
        None,
        ["Ime Prezime", "Drugi Autor"],
        author_match="any",
    )
    assert [row[0] for row in hybrid_all] == [publication_ids["multiple"]]
    assert {row[0] for row in hybrid_any} == {
        publication_ids["reversed"],
        publication_ids["multiple"],
        publication_ids["null-date"],
        publication_ids["cyrillic-author"],
    }

    selected = search_main.fetch_author_results(
        10, None, None, [], [author_ids["Petar Petrovic"]]
    )
    assert [row[0] for row in selected] == [publication_ids["visible-variant"]]

    suggestions = search_main.fetch_author_suggestions("Petar Petrovci", 10)
    assert [(item["id"], item["display_name"], item["publication_count"]) for item in suggestions] == [
        (author_ids["Petar Petrovic"], "Petar Petrovic", 1),
        (author_ids["Петар Петровић"], "Петар Петровић", 1),
        (author_ids["Павле Петровић"], "Павле Петровић", 1),
        (author_ids["Petrović X"], "Petrović X", 1),
    ]
    assert search_main.fetch_author_suggestions("Petar Petrovci", 1) == [suggestions[0]]
    assert all(item["id"] != author_ids["Deleted Petrovic"] for item in suggestions)
