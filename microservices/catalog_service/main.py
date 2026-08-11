from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel

from microservices.common.app_logging import emit_app_event
from microservices.common.author_names import AUTHOR_SEARCH_SCHEMA_SQL
from microservices.common.db import get_connection
from microservices.common.health import (
    build_health_response,
    build_liveness_response,
    build_readiness_response,
    check_database,
)
from microservices.common.observability import setup_observability
from microservices.common.schemas import (
    HealthResponse,
    LivenessResponse,
    ReadinessResponse,
    RepositoryResponse,
    RepositoryWriteRequest,
    StatsResponse,
)
from microservices.common.security import require_api_token


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_schema()
    yield


app = FastAPI(title="Repo Search Catalog Service", version="0.1.0", lifespan=lifespan)
setup_observability(app, "catalog-service")


class PublicationUpsertRequest(BaseModel):
    repository_id: int
    oai_identifier: str
    title: str | None = None
    abstract: str | None = None
    date: str | None = None
    source_url: str | None = None
    authors: list[str] = []


class PublicationTombstoneRequest(BaseModel):
    oai_identifier: str
    datestamp: str | None = None
    set_specs: list[str] = []


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def normalize_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None

    normalized = date_str.replace("Z", "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def normalize_repository_payload(name: str, oai_endpoint: str, refresh_interval: int | None) -> dict[str, Any]:
    normalized_name = name.strip()
    normalized_endpoint = oai_endpoint.strip()

    if not normalized_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Repository name cannot be empty.")
    if not normalized_endpoint:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="OAI endpoint cannot be empty.")
    if not normalized_endpoint.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="OAI endpoint must start with http:// or https://.",
        )

    return {
        "name": normalized_name,
        "oai_endpoint": normalized_endpoint,
        "refresh_interval": refresh_interval,
    }


def repository_from_row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "oai_endpoint": row[2],
        "last_harvest": serialize_datetime(row[3]),
        "refresh_interval": row[4],
    }


def ensure_unique_endpoint(conn, oai_endpoint: str, exclude_repo_id: int | None = None) -> None:
    with conn.cursor() as cur:
        if exclude_repo_id is None:
            cur.execute("SELECT id FROM repository WHERE oai_endpoint = %s LIMIT 1", (oai_endpoint,))
        else:
            cur.execute(
                "SELECT id FROM repository WHERE oai_endpoint = %s AND id <> %s LIMIT 1",
                (oai_endpoint, exclude_repo_id),
            )

        if cur.fetchone() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A repository with this OAI endpoint already exists.",
            )


def lock_publication_identity(cur, repository_id: int, oai_identifier: str) -> None:
    """Serialize tombstone and upsert transitions for one repository-scoped identity."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{repository_id}:{oai_identifier}",),
    )


def create_catalog_repository(name: str, oai_endpoint: str, refresh_interval: int | None) -> dict[str, Any]:
    payload = normalize_repository_payload(name, oai_endpoint, refresh_interval)
    conn = get_connection()
    try:
        ensure_unique_endpoint(conn, payload["oai_endpoint"])
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repository (name, oai_endpoint, refresh_interval)
                VALUES (%s, %s, %s)
                RETURNING id, name, oai_endpoint, last_harvest, refresh_interval
                """,
                (payload["name"], payload["oai_endpoint"], payload["refresh_interval"]),
            )
            repository = repository_from_row(cur.fetchone())
        conn.commit()
        return repository
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_catalog_repository(repo_id: int, name: str, oai_endpoint: str, refresh_interval: int | None) -> dict[str, Any]:
    payload = normalize_repository_payload(name, oai_endpoint, refresh_interval)
    conn = get_connection()
    try:
        ensure_unique_endpoint(conn, payload["oai_endpoint"], exclude_repo_id=repo_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE repository
                SET name = %s, oai_endpoint = %s, refresh_interval = %s
                WHERE id = %s
                RETURNING id, name, oai_endpoint, last_harvest, refresh_interval
                """,
                (payload["name"], payload["oai_endpoint"], payload["refresh_interval"], repo_id),
            )
            repository = repository_from_row(cur.fetchone())

        if repository is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository was not found.")

        conn.commit()
        return repository
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def ensure_schema() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

                CREATE TABLE IF NOT EXISTS repository (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    oai_endpoint TEXT NOT NULL UNIQUE,
                    last_harvest TIMESTAMP WITHOUT TIME ZONE,
                    refresh_interval INTEGER
                );

                CREATE TABLE IF NOT EXISTS author (
                    id SERIAL PRIMARY KEY,
                    full_name TEXT UNIQUE
                );

                CREATE TABLE IF NOT EXISTS publication (
                    id SERIAL PRIMARY KEY,
                    repository_id INTEGER NOT NULL REFERENCES repository(id),
                    title TEXT,
                    abstract TEXT,
                    source_url TEXT,
                    date TIMESTAMP WITHOUT TIME ZONE,
                    oai_identifier TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    embedding vector(1024),
                    embedding_model TEXT,
                    embedding_model_revision TEXT,
                    embedding_template_version TEXT,
                    embedding_dimension INTEGER,
                    embedding_generated_at TIMESTAMPTZ,
                    embedding_source_hash TEXT
                );

                ALTER TABLE publication
                    ADD COLUMN IF NOT EXISTS embedding vector(1024),
                    ADD COLUMN IF NOT EXISTS embedding_model TEXT,
                    ADD COLUMN IF NOT EXISTS embedding_model_revision TEXT,
                    ADD COLUMN IF NOT EXISTS embedding_template_version TEXT,
                    ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER,
                    ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS embedding_source_hash TEXT,
                    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

                ALTER TABLE publication
                    DROP CONSTRAINT IF EXISTS publication_oai_identifier_key;
                DROP INDEX IF EXISTS uq_publication_oai_identifier;
                CREATE UNIQUE INDEX IF NOT EXISTS uq_publication_repository_oai_identifier
                    ON publication (repository_id, oai_identifier);

                CREATE TABLE IF NOT EXISTS publication_tombstone (
                    repository_id INTEGER NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
                    oai_identifier TEXT NOT NULL,
                    oai_datestamp TEXT,
                    set_specs TEXT[] NOT NULL DEFAULT '{}',
                    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    observation_count INTEGER NOT NULL DEFAULT 1,
                    cleared_at TIMESTAMPTZ,
                    PRIMARY KEY (repository_id, oai_identifier)
                );

                CREATE TABLE IF NOT EXISTS publication_author (
                    publication_id INTEGER NOT NULL REFERENCES publication(id) ON DELETE CASCADE,
                    author_id INTEGER NOT NULL REFERENCES author(id),
                    PRIMARY KEY (publication_id, author_id)
                );

                CREATE INDEX IF NOT EXISTS idx_catalog_publication_repository
                    ON publication (repository_id);
                CREATE INDEX IF NOT EXISTS idx_catalog_publication_date
                    ON publication (date);
                CREATE INDEX IF NOT EXISTS idx_catalog_publication_embedding
                    ON publication USING ivfflat (embedding vector_cosine_ops);
                """
            )
            cur.execute(AUTHOR_SEARCH_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def readiness_dependencies() -> dict[str, str]:
    return {"database": check_database(get_connection)}


@app.get("/live", response_model=LivenessResponse)
def live() -> LivenessResponse:
    return build_liveness_response()


@app.get("/ready", response_model=ReadinessResponse, dependencies=[Depends(require_api_token)])
def ready(response: Response) -> ReadinessResponse:
    return build_readiness_response(response, readiness_dependencies())


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    dependencies = readiness_dependencies()
    return build_health_response(dependencies["database"], dependencies)


@app.get("/repositories", response_model=list[RepositoryResponse], dependencies=[Depends(require_api_token)])
def repositories() -> list[RepositoryResponse]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, oai_endpoint, last_harvest, refresh_interval
                FROM repository
                ORDER BY id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        RepositoryResponse(
            id=row[0],
            name=row[1],
            oai_endpoint=row[2],
            last_harvest=serialize_datetime(row[3]),
            refresh_interval=row[4],
        )
        for row in rows
    ]


@app.get("/repositories/{repo_id}", dependencies=[Depends(require_api_token)])
def repository(repo_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, oai_endpoint, last_harvest, refresh_interval
                FROM repository
                WHERE id = %s
                """,
                (repo_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository was not found.")

    return {
        "id": row[0],
        "name": row[1],
        "oai_endpoint": row[2],
        "last_harvest": serialize_datetime(row[3]),
        "refresh_interval": row[4],
    }


@app.get("/stats", response_model=StatsResponse, dependencies=[Depends(require_api_token)])
def stats() -> StatsResponse:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM repository")
            repositories_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*), COUNT(*) FILTER (WHERE embedding IS NOT NULL), MAX(date)
                FROM publication
                WHERE is_active = TRUE
                """
            )
            publications_count, publications_with_embeddings, last_harvest = cur.fetchone()
    finally:
        conn.close()

    return StatsResponse(
        repositories=repositories_count,
        publications=publications_count,
        publications_with_embeddings=publications_with_embeddings,
        last_harvest=serialize_datetime(last_harvest),
    )


@app.post("/repositories", response_model=RepositoryResponse, dependencies=[Depends(require_api_token)])
def create_repository(request: RepositoryWriteRequest) -> RepositoryResponse:
    return RepositoryResponse(**create_catalog_repository(
        name=request.name,
        oai_endpoint=request.oai_endpoint,
        refresh_interval=request.refresh_interval,
    ))


@app.put("/repositories/{repo_id}", response_model=RepositoryResponse, dependencies=[Depends(require_api_token)])
def update_repository(repo_id: int, request: RepositoryWriteRequest) -> RepositoryResponse:
    return RepositoryResponse(**update_catalog_repository(
        repo_id=repo_id,
        name=request.name,
        oai_endpoint=request.oai_endpoint,
        refresh_interval=request.refresh_interval,
    ))


@app.get("/publications", dependencies=[Depends(require_api_token)])
def publications(include_inactive: bool = False) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.repository_id, p.title, p.abstract, p.source_url, p.date,
                       p.oai_identifier, r.name,
                       COALESCE(
                           ARRAY_AGG(a.full_name ORDER BY a.full_name)
                               FILTER (WHERE a.full_name IS NOT NULL),
                           '{}'
                       ) AS authors,
                       p.embedding IS NOT NULL,
                       p.embedding_model,
                       p.embedding_model_revision,
                       p.embedding_template_version,
                       p.embedding_dimension,
                       p.embedding_generated_at,
                       p.embedding_source_hash,
                       p.is_active,
                       t.oai_datestamp,
                       t.cleared_at
                FROM publication p
                LEFT JOIN repository r ON r.id = p.repository_id
                LEFT JOIN publication_author pa ON pa.publication_id = p.id
                LEFT JOIN author a ON a.id = pa.author_id
                LEFT JOIN publication_tombstone t
                    ON t.repository_id = p.repository_id
                   AND t.oai_identifier = p.oai_identifier
                WHERE (%s OR p.is_active = TRUE)
                GROUP BY p.id, r.name, t.oai_datestamp, t.cleared_at
                ORDER BY p.id
                """,
                (include_inactive,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "repository_id": row[1],
            "title": row[2],
            "abstract": row[3],
            "source_url": row[4],
            "date": serialize_datetime(row[5]),
            "oai_identifier": row[6],
            "repository_name": row[7],
            "authors": list(row[8] or []),
            "has_embedding": row[9],
            "embedding_model": row[10],
            "embedding_model_revision": row[11],
            "embedding_template_version": row[12],
            "embedding_dimension": row[13],
            "embedding_generated_at": serialize_datetime(row[14]),
            "embedding_source_hash": row[15],
            "is_active": row[16],
            "tombstone_datestamp": row[17],
            "tombstone_cleared_at": serialize_datetime(row[18]),
        }
        for row in rows
    ]


@app.post("/repositories/{repo_id}/tombstones", dependencies=[Depends(require_api_token)])
def observe_publication_tombstone(repo_id: int, request: PublicationTombstoneRequest) -> dict[str, Any]:
    oai_identifier = request.oai_identifier.strip()
    if not oai_identifier:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="OAI identifier cannot be empty.",
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM repository WHERE id = %s", (repo_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository was not found.")

            lock_publication_identity(cur, repo_id, oai_identifier)
            cur.execute(
                """
                SELECT id, is_active
                FROM publication
                WHERE repository_id = %s AND oai_identifier = %s
                FOR UPDATE
                """,
                (repo_id, oai_identifier),
            )
            publication_row = cur.fetchone()

            cur.execute(
                """
                INSERT INTO publication_tombstone (
                    repository_id, oai_identifier, oai_datestamp, set_specs
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (repository_id, oai_identifier) DO UPDATE SET
                    oai_datestamp = COALESCE(
                        EXCLUDED.oai_datestamp,
                        publication_tombstone.oai_datestamp
                    ),
                    set_specs = CASE
                        WHEN cardinality(EXCLUDED.set_specs) > 0 THEN EXCLUDED.set_specs
                        ELSE publication_tombstone.set_specs
                    END,
                    last_observed_at = NOW(),
                    observation_count = publication_tombstone.observation_count + 1,
                    cleared_at = NULL
                RETURNING observation_count
                """,
                (repo_id, oai_identifier, request.datestamp, request.set_specs),
            )
            observation_count = cur.fetchone()[0]

            if publication_row is not None and publication_row[1]:
                cur.execute(
                    "UPDATE publication SET is_active = FALSE WHERE id = %s",
                    (publication_row[0],),
                )
                outcome = "deactivated"
                publication_id = publication_row[0]
            elif publication_row is not None:
                outcome = "already_inactive"
                publication_id = publication_row[0]
            else:
                outcome = "unknown"
                publication_id = None

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    emit_app_event(
        "catalog.publication_tombstone_observed",
        "catalog-service",
        repository_id=repo_id,
        publication_id=publication_id,
        oai_identifier=oai_identifier,
        oai_datestamp=request.datestamp,
        outcome=outcome,
        observation_count=observation_count,
    )
    return {
        "status": outcome,
        "publication_id": publication_id,
        "observation_count": observation_count,
    }


@app.post("/publications", dependencies=[Depends(require_api_token)])
def upsert_publication(request: PublicationUpsertRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            lock_publication_identity(cur, request.repository_id, request.oai_identifier)
            cur.execute(
                """
                SELECT id, is_active
                FROM publication
                WHERE repository_id = %s AND oai_identifier = %s
                FOR UPDATE
                """,
                (request.repository_id, request.oai_identifier),
            )
            existing = cur.fetchone()
            reactivated = existing is not None and not existing[1]
            cur.execute(
                """
                INSERT INTO publication (
                    repository_id, oai_identifier, title, abstract, date, source_url, is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (repository_id, oai_identifier) DO UPDATE SET
                    title = EXCLUDED.title,
                    abstract = EXCLUDED.abstract,
                    date = EXCLUDED.date,
                    source_url = EXCLUDED.source_url,
                    is_active = TRUE,
                    embedding = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding END,
                    embedding_model = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding_model END,
                    embedding_model_revision = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding_model_revision END,
                    embedding_template_version = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding_template_version END,
                    embedding_dimension = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding_dimension END,
                    embedding_generated_at = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding_generated_at END,
                    embedding_source_hash = CASE
                        WHEN publication.title IS DISTINCT FROM EXCLUDED.title
                          OR publication.abstract IS DISTINCT FROM EXCLUDED.abstract
                          OR publication.is_active = FALSE
                        THEN NULL ELSE publication.embedding_source_hash END
                RETURNING id
                """,
                (
                    request.repository_id,
                    request.oai_identifier,
                    request.title,
                    request.abstract,
                    normalize_date(request.date),
                    request.source_url,
                ),
            )
            publication_id = cur.fetchone()[0]

            cur.execute(
                """
                UPDATE publication_tombstone
                SET cleared_at = NOW()
                WHERE repository_id = %s
                  AND oai_identifier = %s
                  AND cleared_at IS NULL
                """,
                (request.repository_id, request.oai_identifier),
            )

            cur.execute("DELETE FROM publication_author WHERE publication_id = %s", (publication_id,))

            for full_name in request.authors:
                normalized_name = full_name.strip()
                if not normalized_name:
                    continue

                cur.execute(
                    """
                    INSERT INTO author (full_name)
                    VALUES (%s)
                    ON CONFLICT (full_name) DO UPDATE SET full_name = EXCLUDED.full_name
                    RETURNING id
                    """,
                    (normalized_name,),
                )
                author_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO publication_author (publication_id, author_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (publication_id, author_id),
                )

        conn.commit()
        if reactivated:
            emit_app_event(
                "catalog.publication_reactivated",
                "catalog-service",
                repository_id=request.repository_id,
                publication_id=publication_id,
                oai_identifier=request.oai_identifier,
            )
        return {"id": publication_id, "reactivated": reactivated}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/repositories/{repo_id}/last-harvest", dependencies=[Depends(require_api_token)])
def update_last_harvest(repo_id: int) -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE repository SET last_harvest = NOW() WHERE id = %s", (repo_id,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()
