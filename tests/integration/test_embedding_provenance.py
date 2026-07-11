from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from microservices.common.embedding_provenance import document_source_hash


VECTOR_DIMENSIONS = 1024


def _vector() -> list[float]:
    return [1.0, *([0.0] * (VECTOR_DIMENSIONS - 1))]


def _seed_publication(connection_factory, *, title: str = "Title", abstract: str = "Abstract") -> int:
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO repository (name, oai_endpoint) VALUES (%s, %s) RETURNING id",
                ("Repository", "https://example.test/oai"),
            )
            repository_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO publication (repository_id, title, abstract, oai_identifier)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (repository_id, title, abstract, "oai:test:1"),
            )
            publication_id = cursor.fetchone()[0]
        connection.commit()
        return publication_id
    finally:
        connection.close()


def test_legacy_publication_schema_is_upgraded_idempotently(
    pgvector_connection_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from microservices.catalog_service import main as catalog_main

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE repository (id SERIAL PRIMARY KEY, name TEXT, oai_endpoint TEXT UNIQUE)")
            cursor.execute(
                """
                CREATE TABLE publication (
                    id SERIAL PRIMARY KEY,
                    repository_id INTEGER NOT NULL REFERENCES repository(id),
                    title TEXT, abstract TEXT, source_url TEXT, date TIMESTAMP,
                    oai_identifier TEXT UNIQUE, embedding vector(1024)
                )
                """
            )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(catalog_main, "get_connection", pgvector_connection_factory)
    catalog_main.ensure_schema()
    catalog_main.ensure_schema()

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'publication'
                """
            )
            columns = {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()
    assert {
        "embedding_model",
        "embedding_dimension",
        "embedding_generated_at",
        "embedding_source_hash",
    } <= columns


def test_provenance_is_persisted_atomically_and_status_tracks_content_changes(
    pgvector_connection_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from microservices.catalog_service import main as catalog_main
    from microservices.search_service import main as search_main

    monkeypatch.setattr(catalog_main, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(search_main, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(search_main, "embedding_model_name", lambda: "model-a")
    catalog_main.ensure_schema()
    publication_id = _seed_publication(pgvector_connection_factory)
    generated_at = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    request = search_main.PublicationEmbeddingRequest(
        embedding=_vector(),
        embedding_model="model-a",
        embedding_dimension=1024,
        embedding_generated_at=generated_at,
        embedding_source_hash=document_source_hash("Title", "Abstract"),
    )

    assert search_main.upsert_publication_embedding(publication_id, request) == {"status": "ok"}
    assert search_main.embedding_status() == {
        "indexed_publications": 1,
        "publications_with_embeddings": 1,
        "current_embeddings": 1,
        "missing_embeddings": 0,
        "stale_embeddings": 0,
    }

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT embedding_model, embedding_dimension, embedding_generated_at, embedding_source_hash FROM publication"
            )
            row = cursor.fetchone()
            cursor.execute("UPDATE publication SET source_url = %s", ("https://changed.test",))
        connection.commit()
    finally:
        connection.close()
    assert row == ("model-a", 1024, generated_at, document_source_hash("Title", "Abstract"))
    assert search_main.embedding_status()["current_embeddings"] == 1

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE publication SET title = %s", ("Changed title",))
        connection.commit()
    finally:
        connection.close()
    status = search_main.embedding_status()
    assert status["current_embeddings"] == 0
    assert status["stale_embeddings"] == 1


def test_legacy_vector_and_model_change_are_stale(
    pgvector_connection_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from microservices.catalog_service import main as catalog_main
    from microservices.search_service import main as search_main

    monkeypatch.setattr(catalog_main, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(search_main, "get_connection", pgvector_connection_factory)
    catalog_main.ensure_schema()
    publication_id = _seed_publication(pgvector_connection_factory)
    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE publication SET embedding = %s WHERE id = %s", (_vector(), publication_id))
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(search_main, "embedding_model_name", lambda: "model-a")
    assert search_main.embedding_status()["stale_embeddings"] == 1

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE publication SET embedding_model=%s, embedding_dimension=%s,
                    embedding_generated_at=%s, embedding_source_hash=%s WHERE id=%s
                """,
                ("model-a", 1024, datetime.now(timezone.utc), document_source_hash("Title", "Abstract"), publication_id),
            )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(search_main, "embedding_model_name", lambda: "model-b")
    assert search_main.embedding_status()["stale_embeddings"] == 1
