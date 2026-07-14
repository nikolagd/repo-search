from __future__ import annotations

from typing import Any, Callable

import psycopg2
import pytest

from microservices.catalog_service import main as catalog


@pytest.fixture
def catalog_database(pgvector_connection_factory: Callable[[], Any], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog, "get_connection", pgvector_connection_factory)
    catalog.ensure_schema()
    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO repository (name, oai_endpoint) VALUES (%s, %s) RETURNING id",
                ("Synthetic repository", "https://oai.example.test"),
            )
            repository_id = cursor.fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return pgvector_connection_factory, repository_id


def test_publication_upsert_updates_metadata_and_author_links_without_duplicates(catalog_database) -> None:
    connection_factory, repository_id = catalog_database
    first = catalog.PublicationUpsertRequest(
        repository_id=repository_id,
        oai_identifier="oai:synthetic:1",
        title="Initial title",
        abstract="Initial abstract",
        date="2024-01-02",
        source_url="https://example.test/initial",
        authors=["Alice Author", "Bob Author", "Alice Author", "  "],
    )
    publication_id = catalog.upsert_publication(first)["id"]

    updated = first.model_copy(
        update={
            "title": "Updated title",
            "abstract": "Updated abstract",
            "source_url": "https://example.test/updated",
            "authors": ["Bob Author", "Carol Author", "Bob Author"],
        }
    )
    assert catalog.upsert_publication(updated)["id"] == publication_id
    assert catalog.upsert_publication(updated)["id"] == publication_id

    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*), MIN(title), MIN(abstract), MIN(source_url) FROM publication WHERE oai_identifier = %s",
                (first.oai_identifier,),
            )
            assert cursor.fetchone() == (
                1,
                "Updated title",
                "Updated abstract",
                "https://example.test/updated",
            )
            cursor.execute(
                """
                SELECT a.full_name, COUNT(*)
                FROM publication_author pa
                JOIN author a ON a.id = pa.author_id
                WHERE pa.publication_id = %s
                GROUP BY a.full_name
                ORDER BY a.full_name
                """,
                (publication_id,),
            )
            assert cursor.fetchall() == [("Bob Author", 1), ("Carol Author", 1)]
    finally:
        connection.close()

def test_publication_upsert_rolls_back_when_repository_foreign_key_fails(catalog_database) -> None:
    connection_factory, _repository_id = catalog_database
    request = catalog.PublicationUpsertRequest(
        repository_id=999999,
        oai_identifier="oai:synthetic:rollback",
        title="Must not persist",
        authors=["Rollback Author"],
    )

    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        catalog.upsert_publication(request)

    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM publication WHERE oai_identifier = %s", (request.oai_identifier,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT COUNT(*) FROM author WHERE full_name = %s", ("Rollback Author",))
            assert cursor.fetchone()[0] == 0
    finally:
        connection.close()
