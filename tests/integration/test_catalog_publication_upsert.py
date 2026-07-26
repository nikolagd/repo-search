from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import psycopg2
import pytest

from legacy_monolith.backend.etl.db import insert_publication as legacy_insert_publication
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


def test_tombstone_deactivation_is_idempotent_audited_and_repository_scoped(catalog_database) -> None:
    connection_factory, repository_id = catalog_database
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO repository (name, oai_endpoint) VALUES (%s, %s) RETURNING id",
                ("Other repository", "https://other.example.test/oai"),
            )
            other_repository_id = cursor.fetchone()[0]
        connection.commit()
    finally:
        connection.close()

    for repo_id, title in ((repository_id, "Local copy"), (other_repository_id, "Other copy")):
        catalog.upsert_publication(
            catalog.PublicationUpsertRequest(
                repository_id=repo_id,
                oai_identifier="oai:shared:1",
                title=title,
                authors=[],
            )
        )

    request = catalog.PublicationTombstoneRequest(
        oai_identifier="oai:shared:1",
        datestamp="2026-07-25T10:11:12Z",
        set_specs=["publications"],
    )
    first = catalog.observe_publication_tombstone(repository_id, request)
    repeated = catalog.observe_publication_tombstone(repository_id, request)

    assert first["status"] == "deactivated"
    assert first["publication_id"] is not None
    assert first["observation_count"] == 1
    assert repeated == {
        "status": "already_inactive",
        "publication_id": first["publication_id"],
        "observation_count": 2,
    }

    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT repository_id, is_active
                FROM publication
                WHERE oai_identifier = %s
                ORDER BY repository_id
                """,
                ("oai:shared:1",),
            )
            assert cursor.fetchall() == [
                (repository_id, False),
                (other_repository_id, True),
            ]
            cursor.execute(
                """
                SELECT oai_datestamp, set_specs, observation_count, cleared_at
                FROM publication_tombstone
                WHERE repository_id = %s AND oai_identifier = %s
                """,
                (repository_id, "oai:shared:1"),
            )
            assert cursor.fetchone() == (
                "2026-07-25T10:11:12Z",
                ["publications"],
                2,
                None,
            )
    finally:
        connection.close()

    assert [item["repository_id"] for item in catalog.publications()] == [other_repository_id]
    assert {item["is_active"] for item in catalog.publications(include_inactive=True)} == {False, True}
    assert catalog.stats().publications == 1


def test_unknown_tombstone_does_not_create_publication_and_is_idempotently_audited(catalog_database) -> None:
    connection_factory, repository_id = catalog_database
    request = catalog.PublicationTombstoneRequest(
        oai_identifier="oai:unknown:1",
        datestamp="2026-07-25",
    )

    assert catalog.observe_publication_tombstone(repository_id, request)["status"] == "unknown"
    repeated = catalog.observe_publication_tombstone(repository_id, request)
    assert repeated["status"] == "unknown"
    assert repeated["publication_id"] is None
    assert repeated["observation_count"] == 2

    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM publication WHERE oai_identifier = %s", ("oai:unknown:1",))
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                """
                SELECT observation_count, cleared_at
                FROM publication_tombstone
                WHERE repository_id = %s AND oai_identifier = %s
                """,
                (repository_id, "oai:unknown:1"),
            )
            assert cursor.fetchone() == (2, None)
    finally:
        connection.close()


def test_normal_upsert_reactivates_and_invalidates_old_embedding_provenance(catalog_database) -> None:
    connection_factory, repository_id = catalog_database
    initial = catalog.PublicationUpsertRequest(
        repository_id=repository_id,
        oai_identifier="oai:reactivate:1",
        title="Old title",
        abstract="Old abstract",
        authors=[],
    )
    publication_id = catalog.upsert_publication(initial)["id"]

    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE publication
                SET embedding = %s::vector,
                    embedding_model = 'old-model',
                    embedding_model_revision = 'old-revision',
                    embedding_template_version = 'old-template',
                    embedding_dimension = 1024,
                    embedding_generated_at = NOW(),
                    embedding_source_hash = %s
                WHERE id = %s
                """,
                ("[" + ",".join(["0"] * 1024) + "]", "a" * 64, publication_id),
            )
        connection.commit()
    finally:
        connection.close()

    catalog.observe_publication_tombstone(
        repository_id,
        catalog.PublicationTombstoneRequest(
            oai_identifier=initial.oai_identifier,
            datestamp="2026-07-25T10:11:12Z",
        ),
    )
    result = catalog.upsert_publication(
        initial.model_copy(update={"title": "Returned title", "abstract": "Returned abstract"})
    )

    assert result == {"id": publication_id, "reactivated": True}
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT is_active, title, abstract, embedding, embedding_model,
                       embedding_model_revision, embedding_template_version,
                       embedding_dimension, embedding_generated_at, embedding_source_hash
                FROM publication
                WHERE id = %s
                """,
                (publication_id,),
            )
            assert cursor.fetchone() == (
                True,
                "Returned title",
                "Returned abstract",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            cursor.execute(
                """
                SELECT oai_datestamp, observation_count, cleared_at IS NOT NULL
                FROM publication_tombstone
                WHERE repository_id = %s AND oai_identifier = %s
                """,
                (repository_id, initial.oai_identifier),
            )
            assert cursor.fetchone() == ("2026-07-25T10:11:12Z", 1, True)
    finally:
        connection.close()


def test_legacy_upsert_retains_embedding_only_for_unchanged_active_input(catalog_database) -> None:
    connection_factory, repository_id = catalog_database
    record = {
        "oai_identifier": "oai:legacy:embedding-contract",
        "title": "Stable title",
        "abstract": "Stable abstract",
        "date": "2024-01-02",
        "source_url": "https://example.test/initial",
        "authors": [],
    }
    connection = connection_factory()
    try:
        legacy_insert_publication(connection, repository_id, record)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE publication
                SET embedding = %s::vector
                WHERE repository_id = %s AND oai_identifier = %s
                RETURNING id
                """,
                ("[" + ",".join(["0.001"] * 1024) + "]", repository_id, record["oai_identifier"]),
            )
            publication_id = cursor.fetchone()[0]
        connection.commit()

        unchanged_input = {**record, "source_url": "https://example.test/updated"}
        legacy_insert_publication(connection, repository_id, unchanged_input)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT embedding IS NOT NULL, is_active FROM publication WHERE id = %s",
                (publication_id,),
            )
            assert cursor.fetchone() == (True, True)

        changed_title = {**unchanged_input, "title": "Changed title"}
        legacy_insert_publication(connection, repository_id, changed_title)
        with connection.cursor() as cursor:
            cursor.execute("SELECT embedding IS NULL FROM publication WHERE id = %s", (publication_id,))
            assert cursor.fetchone() == (True,)
            cursor.execute(
                "UPDATE publication SET embedding = %s::vector WHERE id = %s",
                ("[" + ",".join(["0.002"] * 1024) + "]", publication_id),
            )
        connection.commit()

        changed_abstract = {**changed_title, "abstract": "Changed abstract"}
        legacy_insert_publication(connection, repository_id, changed_abstract)
        with connection.cursor() as cursor:
            cursor.execute("SELECT embedding IS NULL FROM publication WHERE id = %s", (publication_id,))
            assert cursor.fetchone() == (True,)
            cursor.execute(
                "UPDATE publication SET embedding = %s::vector, is_active = FALSE WHERE id = %s",
                ("[" + ",".join(["0.003"] * 1024) + "]", publication_id),
            )
        connection.commit()

        legacy_insert_publication(connection, repository_id, changed_abstract)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT embedding IS NULL, is_active FROM publication WHERE id = %s",
                (publication_id,),
            )
            assert cursor.fetchone() == (True, True)
    finally:
        connection.close()


def test_oai_tombstone_migration_upgrades_existing_schema(
    pgvector_connection_factory: Callable[[], Any],
) -> None:
    migrations_dir = Path("legacy_monolith/backend/migrations")
    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            for name in (
                "001_initial_schema.sql",
                "002_admin_jobs.sql",
                "003_admin_job_acknowledged.sql",
                "004_admin_job_harvest_statistics.sql",
            ):
                cursor.execute((migrations_dir / name).read_text(encoding="utf-8"))
            cursor.execute(
                "INSERT INTO repository (name, oai_endpoint) VALUES ('Existing', 'https://existing.test') RETURNING id"
            )
            repository_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO publication (repository_id, oai_identifier, title)
                VALUES (%s, 'oai:migration:1', 'Existing publication')
                """,
                (repository_id,),
            )
            cursor.execute((migrations_dir / "005_oai_tombstone_handling.sql").read_text(encoding="utf-8"))
            cursor.execute(
                "SELECT is_active FROM publication WHERE repository_id = %s AND oai_identifier = 'oai:migration:1'",
                (repository_id,),
            )
            assert cursor.fetchone() == (True,)
            cursor.execute("SELECT to_regclass('publication_tombstone')")
            assert cursor.fetchone()[0] == "publication_tombstone"
            cursor.execute(
                "INSERT INTO repository (name, oai_endpoint) VALUES ('Other', 'https://other.test') RETURNING id"
            )
            other_repository_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO publication (repository_id, oai_identifier, title)
                VALUES (%s, 'oai:migration:1', 'Repository-scoped duplicate')
                """,
                (other_repository_id,),
            )
            cursor.execute(
                """
                SELECT deactivated_records, unknown_tombstones,
                       already_inactive_tombstones, invalid_tombstones
                FROM admin_job
                LIMIT 0
                """
            )
        connection.commit()
    finally:
        connection.close()
