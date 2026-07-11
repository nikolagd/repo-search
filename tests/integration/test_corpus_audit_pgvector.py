from __future__ import annotations

import json
import csv
from datetime import datetime, timedelta, timezone

from evaluation.corpus_audit import run_audit
from microservices.common.embedding_provenance import document_source_hash


def _vector_literal() -> str:
    return "[" + ",".join(["0.001"] * 1024) + "]"


def test_corpus_audit_reads_seeded_pgvector_corpus_without_modifying_rows(
    pgvector_connection_factory,
    tmp_path,
) -> None:
    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE repository (
                    id SERIAL PRIMARY KEY, name TEXT NOT NULL, oai_endpoint TEXT NOT NULL UNIQUE,
                    last_harvest TIMESTAMP, refresh_interval INTEGER
                );
                CREATE TABLE author (id SERIAL PRIMARY KEY, full_name TEXT UNIQUE);
                CREATE TABLE publication (
                    id SERIAL PRIMARY KEY, repository_id INTEGER NOT NULL REFERENCES repository(id),
                    title TEXT, abstract TEXT, source_url TEXT, date TIMESTAMP, oai_identifier TEXT UNIQUE,
                    embedding vector(1024), embedding_model TEXT, embedding_dimension INTEGER,
                    embedding_generated_at TIMESTAMPTZ, embedding_source_hash TEXT
                );
                CREATE TABLE publication_author (
                    publication_id INTEGER REFERENCES publication(id), author_id INTEGER REFERENCES author(id),
                    PRIMARY KEY (publication_id, author_id)
                );
                CREATE TABLE admin_job (
                    id SERIAL PRIMARY KEY, job_type TEXT NOT NULL, repository_id INTEGER,
                    status TEXT NOT NULL, started_at TIMESTAMP, finished_at TIMESTAMP,
                    processed_records INTEGER, created_at TIMESTAMP NOT NULL
                );
                """
            )
            cursor.execute(
                """
                INSERT INTO repository (name, oai_endpoint, last_harvest, refresh_interval)
                VALUES ('Synthetic repository', 'https://example.test/oai', %s, 1440) RETURNING id
                """,
                (datetime(2026, 7, 10, 12, 0),),
            )
            repository_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO author (full_name) VALUES ('Synthetic Author') RETURNING id")
            author_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO publication (
                    repository_id, title, abstract, source_url, date, oai_identifier, embedding,
                    embedding_model, embedding_dimension, embedding_generated_at, embedding_source_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, 1024, %s, %s) RETURNING id
                """,
                (
                    repository_id,
                    "Synthetic title",
                    "Synthetic abstract",
                    "https://example.test/1",
                    datetime(2024, 1, 1),
                    "oai:synthetic:1",
                    _vector_literal(),
                    "synthetic-model",
                    datetime(2026, 7, 11, tzinfo=timezone.utc),
                    document_source_hash("Synthetic title", "Synthetic abstract"),
                ),
            )
            publication_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO publication_author (publication_id, author_id) VALUES (%s, %s)",
                (publication_id, author_id),
            )
            cursor.execute(
                """
                INSERT INTO publication (repository_id, title, abstract, source_url, oai_identifier)
                VALUES (%s, '', NULL, '', NULL)
                """,
                (repository_id,),
            )
            older_start = datetime(2026, 7, 9, 10, 0)
            newer_created = datetime(2026, 7, 10, 10, 0)
            cursor.execute(
                """
                INSERT INTO admin_job (
                    job_type, repository_id, status, started_at, finished_at, processed_records, created_at
                ) VALUES ('repository_harvest', %s, 'succeeded', %s, %s, 2, %s),
                         ('repository_harvest', %s, 'queued', NULL, NULL, NULL, %s)
                """,
                (
                    repository_id,
                    older_start,
                    older_start + timedelta(seconds=75),
                    older_start,
                    repository_id,
                    newer_created,
                ),
            )
            cursor.execute(
                "SELECT (SELECT COUNT(*) FROM repository), (SELECT COUNT(*) FROM publication), "
                "(SELECT COUNT(*) FROM author), (SELECT COUNT(*) FROM admin_job)"
            )
            before = cursor.fetchone()
        connection.commit()
    finally:
        connection.close()

    output = run_audit(
        pgvector_connection_factory,
        tmp_path,
        git_commit="integration-test",
        audit_time=datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc),
        model_name="synthetic-model",
    )

    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    assert audit["metadata"]["corpus_size"] == 2
    assert audit["metadata_quality"]["current_embeddings"] == 1
    assert audit["metadata_quality"]["missing_embeddings"] == 1
    assert audit["metadata_quality"]["missing_or_blank_titles"] == 1
    assert audit["duplicates"]["exact_duplicate_oai_identifier_groups"] == 0
    assert audit["metadata"]["transaction_read_only"] == "on"
    assert audit["metadata"]["transaction_isolation"] == "repeatable read"
    assert audit["metadata"]["postgresql_transaction_snapshot"]
    assert audit["metadata"]["external_database_backup_snapshot_identifier"] == "not recorded"
    assert {path.name for path in output.iterdir()} == {
        "audit.json",
        "repositories.csv",
        "metadata_quality.csv",
        "corpus_snapshot.json",
        "summary.md",
    }
    with (output / "repositories.csv").open(encoding="utf-8") as stream:
        repository_row = next(csv.DictReader(stream))
    assert repository_row["latest_harvest_job_status"] == "queued"
    assert repository_row["job_created_at"] == newer_created.isoformat()
    assert repository_row["job_started_at"] == ""
    assert repository_row["job_finished_at"] == ""
    assert repository_row["harvest_duration_seconds"] == ""
    assert repository_row["last_successful_harvest"] == "2026-07-10T12:00:00"
    assert repository_row["processed_records"] == ""
    assert repository_row["metadata_prefix"] == "not recorded"

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT COUNT(*) FROM repository), (SELECT COUNT(*) FROM publication), "
                "(SELECT COUNT(*) FROM author), (SELECT COUNT(*) FROM admin_job)"
            )
            after = cursor.fetchone()
    finally:
        connection.close()
    assert after == before


def test_audit_uses_one_repeatable_read_snapshot_during_concurrent_commit(
    pgvector_connection_factory,
    tmp_path,
) -> None:
    setup = pgvector_connection_factory()
    try:
        with setup.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE repository (
                    id SERIAL PRIMARY KEY, name TEXT NOT NULL, oai_endpoint TEXT NOT NULL UNIQUE,
                    last_harvest TIMESTAMP, refresh_interval INTEGER
                );
                CREATE TABLE author (id SERIAL PRIMARY KEY, full_name TEXT UNIQUE);
                CREATE TABLE publication (
                    id SERIAL PRIMARY KEY, repository_id INTEGER NOT NULL REFERENCES repository(id),
                    title TEXT, abstract TEXT, source_url TEXT, date TIMESTAMP, oai_identifier TEXT UNIQUE,
                    embedding vector(1024), embedding_model TEXT, embedding_dimension INTEGER,
                    embedding_generated_at TIMESTAMPTZ, embedding_source_hash TEXT
                );
                CREATE TABLE publication_author (
                    publication_id INTEGER REFERENCES publication(id), author_id INTEGER REFERENCES author(id),
                    PRIMARY KEY (publication_id, author_id)
                );
                CREATE TABLE admin_job (
                    id SERIAL PRIMARY KEY, job_type TEXT NOT NULL, repository_id INTEGER,
                    status TEXT NOT NULL, started_at TIMESTAMP, finished_at TIMESTAMP,
                    processed_records INTEGER, created_at TIMESTAMP NOT NULL
                );
                INSERT INTO repository (name, oai_endpoint) VALUES ('Initial', 'https://initial.test/oai');
                INSERT INTO publication (repository_id, title, oai_identifier)
                VALUES (1, 'Initial publication', 'oai:initial:1');
                """
            )
        setup.commit()
    finally:
        setup.close()

    audit_connection = pgvector_connection_factory()
    executed_connection_ids = []
    session_arguments = {}
    concurrent_committed = False

    class CursorWrapper:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, statement, parameters=None):
            nonlocal concurrent_committed
            executed_connection_ids.append(id(audit_connection))
            result = self.cursor.execute(statement, parameters)
            if "pg_current_snapshot" in statement and not concurrent_committed:
                concurrent = pgvector_connection_factory()
                try:
                    with concurrent.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO repository (name, oai_endpoint) VALUES ('Concurrent', 'https://concurrent.test/oai') RETURNING id"
                        )
                        repository_id = cursor.fetchone()[0]
                        cursor.execute(
                            "INSERT INTO publication (repository_id, title, oai_identifier) VALUES (%s, 'Concurrent publication', 'oai:concurrent:1')",
                            (repository_id,),
                        )
                    concurrent.commit()
                finally:
                    concurrent.close()
                concurrent_committed = True
            return result

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class ConnectionWrapper:
        def cursor(self):
            return CursorWrapper(audit_connection.cursor())

        def set_session(self, **kwargs):
            session_arguments.update(kwargs)
            return audit_connection.set_session(**kwargs)

        def rollback(self):
            return audit_connection.rollback()

        def close(self):
            return audit_connection.close()

    output = run_audit(
        ConnectionWrapper,
        tmp_path,
        git_commit="snapshot-test",
        audit_time=datetime(2026, 7, 11, 13, 0, tzinfo=timezone.utc),
        model_name="synthetic-model",
    )
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))

    assert session_arguments == {"readonly": True, "isolation_level": "REPEATABLE READ"}
    assert audit["metadata"]["transaction_read_only"] == "on"
    assert audit["metadata"]["transaction_isolation"] == "repeatable read"
    assert audit["metadata"]["postgresql_transaction_snapshot"]
    assert audit["metadata"]["corpus_size"] == 1
    assert audit["metadata"]["repository_count"] == 1
    assert len(set(executed_connection_ids)) == 1

    verification = pgvector_connection_factory()
    try:
        with verification.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM publication")
            assert cursor.fetchone()[0] == 2
    finally:
        verification.close()
