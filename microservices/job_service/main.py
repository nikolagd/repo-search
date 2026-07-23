from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel

from microservices.common.db import get_connection
from microservices.common.health import (
    build_health_response,
    build_liveness_response,
    build_readiness_response,
    check_database,
)
from microservices.common.observability import (
    set_job_oldest_queued_age,
    set_job_oldest_running_age,
    set_job_queue_depth,
    set_jobs_by_status,
    setup_observability,
)
from microservices.common.schemas import HealthResponse, LivenessResponse, ReadinessResponse
from microservices.common.security import require_api_token


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_schema()
    yield


app = FastAPI(title="Repo Search Job Service", version="0.1.0", lifespan=lifespan)
setup_observability(app, "job-service")

REPOSITORY_HARVEST_JOB = "repository_harvest"
EMBEDDING_BACKFILL_JOB = "embedding_backfill"
JOB_TYPES = (REPOSITORY_HARVEST_JOB, EMBEDDING_BACKFILL_JOB)
JOB_STATUSES = ("queued", "running", "succeeded", "failed")


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
        "attempt_count": row[8] if len(row) > 8 else 0,
        "heartbeat_at": serialize_datetime(row[9]) if len(row) > 9 else None,
        "received_records": row[10] if len(row) > 10 else None,
        "parsed_records": row[11] if len(row) > 11 else None,
        "skipped_records": row[12] if len(row) > 12 else None,
        "deleted_records": row[13] if len(row) > 13 else None,
        "pages_processed": row[14] if len(row) > 14 else None,
    }


def refresh_job_metrics() -> None:
    try:
        conn = get_connection()
    except Exception:
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_type, status, COUNT(*)
                FROM admin_job
                GROUP BY job_type, status
                """
            )
            status_counts = {(job_type, status): count for job_type, status, count in cur.fetchall()}

            cur.execute(
                """
                SELECT job_type, EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))
                FROM admin_job
                WHERE status = 'queued'
                GROUP BY job_type
                """
            )
            oldest_queued = dict(cur.fetchall())

            cur.execute(
                """
                SELECT job_type, EXTRACT(EPOCH FROM (NOW() - MIN(started_at)))
                FROM admin_job
                WHERE status = 'running'
                GROUP BY job_type
                """
            )
            oldest_running = dict(cur.fetchall())
    except Exception:
        return
    finally:
        conn.close()

    for job_type in JOB_TYPES:
        for job_status in JOB_STATUSES:
            count = int(status_counts.get((job_type, job_status), 0))
            set_jobs_by_status("job-service", job_type, job_status, count)
            if job_status == "queued":
                set_job_queue_depth("job-service", job_type, count)

        set_job_oldest_queued_age("job-service", job_type, float(oldest_queued.get(job_type, 0) or 0))
        set_job_oldest_running_age("job-service", job_type, float(oldest_running.get(job_type, 0) or 0))


app.state.collect_metrics = refresh_job_metrics


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
                    received_records INTEGER,
                    parsed_records INTEGER,
                    skipped_records INTEGER,
                    deleted_records INTEGER,
                    pages_processed INTEGER,
                    message TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    heartbeat_at TIMESTAMP WITHOUT TIME ZONE,
                    lease_token TEXT,
                    acknowledged_at TIMESTAMP WITHOUT TIME ZONE,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    CONSTRAINT chk_admin_job_type
                        CHECK (job_type IN ('repository_harvest', 'embedding_backfill')),
                    CONSTRAINT chk_admin_job_status
                        CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
                );

                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITHOUT TIME ZONE;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS lease_token TEXT;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS received_records INTEGER;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS parsed_records INTEGER;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS skipped_records INTEGER;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS deleted_records INTEGER;
                ALTER TABLE admin_job
                    ADD COLUMN IF NOT EXISTS pages_processed INTEGER;

                UPDATE admin_job
                SET attempt_count = 0
                WHERE attempt_count IS NULL;

                ALTER TABLE admin_job
                    ALTER COLUMN attempt_count SET DEFAULT 0;
                ALTER TABLE admin_job
                    ALTER COLUMN attempt_count SET NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS uq_queued_running_repository_harvest
                    ON admin_job (repository_id)
                    WHERE job_type = 'repository_harvest' AND status IN ('queued', 'running');

                CREATE UNIQUE INDEX IF NOT EXISTS uq_queued_running_embedding_backfill
                    ON admin_job (job_type)
                    WHERE job_type = 'embedding_backfill' AND status IN ('queued', 'running');

                CREATE INDEX IF NOT EXISTS idx_admin_job_recent
                    ON admin_job (job_type, repository_id, started_at DESC);

                CREATE INDEX IF NOT EXISTS idx_admin_job_stale_running
                    ON admin_job (heartbeat_at)
                    WHERE status = 'running';
                """
            )
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


@app.get("/jobs", dependencies=[Depends(require_api_token)])
def jobs(
    job_type: str | None = None,
    repository_id: int | None = None,
    include_acknowledged: bool = False,
) -> list[dict[str, Any]]:
    refresh_job_metrics()
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
               processed_records, message, attempt_count, heartbeat_at,
               received_records, parsed_records, skipped_records,
               deleted_records, pages_processed
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
                          processed_records, message, attempt_count, heartbeat_at,
                          received_records, parsed_records, skipped_records,
                          deleted_records, pages_processed
                """,
                (job_type, repository_id, message),
            )
            job = job_from_row(cur.fetchone())
        conn.commit()
        refresh_job_metrics()
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
        refresh_job_metrics()
        return {"status": "ok"}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()
