from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from microservices.common.db import get_connection
from microservices.common.schemas import HealthResponse
from microservices.common.security import require_api_token

app = FastAPI(title="Repo Search Job Service", version="0.1.0")

REPOSITORY_HARVEST_JOB = "repository_harvest"
EMBEDDING_BACKFILL_JOB = "embedding_backfill"


class HarvestJobRequest(BaseModel):
    repository_id: int


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def job_from_row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    return {
        "id": row[0],
        "job_type": row[1],
        "repository_id": row[2],
        "status": row[3],
        "started_at": serialize_datetime(row[4]),
        "finished_at": serialize_datetime(row[5]),
        "processed_records": row[6],
        "message": row[7],
    }


def ensure_schema() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_job (
                    id SERIAL PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    repository_id INTEGER,
                    status TEXT NOT NULL,
                    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMP WITHOUT TIME ZONE,
                    processed_records INTEGER,
                    message TEXT NOT NULL,
                    acknowledged_at TIMESTAMP WITHOUT TIME ZONE,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    CONSTRAINT chk_admin_job_type
                        CHECK (job_type IN ('repository_harvest', 'embedding_backfill')),
                    CONSTRAINT chk_admin_job_status
                        CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_queued_running_repository_harvest
                    ON admin_job (repository_id)
                    WHERE job_type = 'repository_harvest' AND status IN ('queued', 'running');

                CREATE UNIQUE INDEX IF NOT EXISTS uq_queued_running_embedding_backfill
                    ON admin_job (job_type)
                    WHERE job_type = 'embedding_backfill' AND status IN ('queued', 'running');

                CREATE INDEX IF NOT EXISTS idx_admin_job_recent
                    ON admin_job (job_type, repository_id, started_at DESC);
                """
            )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        database = "ok"
    except Exception:
        database = "unavailable"
    finally:
        conn.close()

    return HealthResponse(status="ok", database=database)


@app.get("/jobs", dependencies=[Depends(require_api_token)])
def jobs(
    job_type: str | None = None,
    repository_id: int | None = None,
    include_acknowledged: bool = False,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []

    if job_type is not None:
        clauses.append("job_type = %s")
        params.append(job_type)

    if repository_id is not None:
        clauses.append("repository_id = %s")
        params.append(repository_id)

    if not include_acknowledged:
        clauses.append("(status IN ('queued', 'running') OR acknowledged_at IS NULL)")

    sql = """
        SELECT id, job_type, repository_id, status, started_at, finished_at,
               processed_records, message
        FROM admin_job
    """

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += """
        ORDER BY
            CASE WHEN status IN ('queued', 'running') THEN 0 ELSE 1 END,
            started_at DESC
        LIMIT 100
    """

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [job_from_row(row) for row in rows]


@app.post("/jobs/harvest", dependencies=[Depends(require_api_token)])
def create_harvest_job(request: HarvestJobRequest) -> dict[str, Any]:
    return create_job(REPOSITORY_HARVEST_JOB, request.repository_id, "Harvest queued.")


@app.post("/jobs/embedding-backfill", dependencies=[Depends(require_api_token)])
def create_embedding_backfill_job() -> dict[str, Any]:
    return create_job(EMBEDDING_BACKFILL_JOB, None, "Embedding backfill queued.")


def create_job(job_type: str, repository_id: int | None, message: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_job (job_type, repository_id, status, message)
                VALUES (%s, %s, 'queued', %s)
                RETURNING id, job_type, repository_id, status, started_at, finished_at,
                          processed_records, message
                """,
                (job_type, repository_id, message),
            )
            job = job_from_row(cur.fetchone())
        conn.commit()
        return job
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A matching job is already queued or running.",
        ) from exc
    finally:
        conn.close()


@app.post("/jobs/{job_id}/acknowledge", dependencies=[Depends(require_api_token)])
def acknowledge_job(job_id: int) -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET acknowledged_at = NOW(), updated_at = NOW()
                WHERE id = %s AND status <> 'running'
                RETURNING id
                """,
                (job_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Completed job was not found.")

        conn.commit()
        return {"status": "ok"}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()
