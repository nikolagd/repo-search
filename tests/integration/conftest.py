from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest


@pytest.fixture
def pgvector_connection_factory() -> Iterator[Callable[[], Any]]:
    database_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL/pgvector integration test was not requested.")

    import psycopg2
    from psycopg2 import sql

    try:
        administration_connection = psycopg2.connect(database_url, connect_timeout=3)
    except psycopg2.OperationalError as exc:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        pytest.skip(f"TEST_DATABASE_URL is unreachable: {message}")

    schema_name = f"repo_search_test_{uuid4().hex}"
    administration_connection.autocommit = True
    try:
        with administration_connection.cursor() as cursor:
            cursor.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'")
            if cursor.fetchone() is None:
                pytest.skip("The configured PostgreSQL server does not provide the pgvector extension.")

            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            except psycopg2.errors.InsufficientPrivilege:
                pytest.skip("The configured PostgreSQL user cannot enable the pgvector extension.")

            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
    finally:
        administration_connection.close()

    def connect() -> Any:
        return psycopg2.connect(
            database_url,
            connect_timeout=3,
            options=f"-c search_path={schema_name},public",
        )

    try:
        yield connect
    finally:
        cleanup_connection = psycopg2.connect(database_url, connect_timeout=3)
        cleanup_connection.autocommit = True
        try:
            with cleanup_connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name)))
        finally:
            cleanup_connection.close()
