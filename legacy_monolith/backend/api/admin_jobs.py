from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import psycopg2
from fastapi import BackgroundTasks, HTTPException, status

from etl.db import get_connection, get_repository
from etl.main import harvest_repository

REPOSITORY_HARVEST_JOB = "repository_harvest"
EMBEDDING_BACKFILL_JOB = "embedding_backfill"
RECENT_JOB_STATUS_MINUTES = int(os.getenv("ADMIN_JOB_STATUS_HISTORY_MINUTES", "60"))


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


def latest_job_where_clause(alias: str = "j") -> str:
    return f"""
        (
            {alias}.status = 'running'
            OR (
                {alias}.acknowledged_at IS NULL
                AND {alias}.started_at >= NOW() - (%s * INTERVAL '1 minute')
            )
        )
    """


def get_latest_repository_job(conn, repo_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, job_type, repository_id, status, started_at, finished_at,
                   processed_records, message
            FROM admin_job j
            WHERE j.job_type = %s
              AND j.repository_id = %s
              AND {latest_job_where_clause("j")}
            ORDER BY
                CASE WHEN j.status = 'running' THEN 0 ELSE 1 END,
                j.started_at DESC
            LIMIT 1
            """,
            (REPOSITORY_HARVEST_JOB, repo_id, RECENT_JOB_STATUS_MINUTES),
        )
        return job_from_row(cur.fetchone())


def get_latest_embedding_job(conn) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, job_type, repository_id, status, started_at, finished_at,
                   processed_records, message
            FROM admin_job j
            WHERE j.job_type = %s
              AND {latest_job_where_clause("j")}
            ORDER BY
                CASE WHEN j.status = 'running' THEN 0 ELSE 1 END,
                j.started_at DESC
            LIMIT 1
            """,
            (EMBEDDING_BACKFILL_JOB, RECENT_JOB_STATUS_MINUTES),
        )
        return job_from_row(cur.fetchone())


def list_admin_repositories() -> list[dict[str, Any]]:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    r.id,
                    r.name,
                    r.oai_endpoint,
                    r.last_harvest,
                    r.refresh_interval,
                    j.id,
                    j.job_type,
                    j.repository_id,
                    j.status,
                    j.started_at,
                    j.finished_at,
                    j.processed_records,
                    j.message
                FROM repository r
                LEFT JOIN LATERAL (
                    SELECT id, job_type, repository_id, status, started_at,
                           finished_at, processed_records, message
                    FROM admin_job j
                    WHERE j.job_type = %s
                      AND j.repository_id = r.id
                      AND {latest_job_where_clause("j")}
                    ORDER BY
                        CASE WHEN j.status = 'running' THEN 0 ELSE 1 END,
                        j.started_at DESC
                    LIMIT 1
                ) j ON TRUE
                ORDER BY r.id
                """,
                (REPOSITORY_HARVEST_JOB, RECENT_JOB_STATUS_MINUTES),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "name": row[1],
            "oai_endpoint": row[2],
            "last_harvest": serialize_datetime(row[3]),
            "refresh_interval": row[4],
            "harvest_job": job_from_row(row[5:13]) if row[5] is not None else None,
        }
        for row in rows
    ]


def get_embedding_status() -> dict[str, Any]:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM publication WHERE is_active = TRUE AND embedding IS NULL"
            )
            missing_count = cur.fetchone()[0]

        embedding_job = get_latest_embedding_job(conn)
    finally:
        conn.close()

    return {
        "missing_embeddings": missing_count,
        "embedding_job": embedding_job,
    }


def create_repository_harvest_job(conn, repo_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO admin_job (job_type, repository_id, status, message)
            VALUES (%s, %s, 'running', 'Harvest started.')
            RETURNING id, job_type, repository_id, status, started_at, finished_at,
                      processed_records, message
            """,
            (REPOSITORY_HARVEST_JOB, repo_id),
        )
        return job_from_row(cur.fetchone())


def create_embedding_backfill_job(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO admin_job (job_type, repository_id, status, message)
            VALUES (%s, NULL, 'running', 'Embedding backfill started.')
            RETURNING id, job_type, repository_id, status, started_at, finished_at,
                      processed_records, message
            """,
            (EMBEDDING_BACKFILL_JOB,),
        )
        return job_from_row(cur.fetchone())


def queue_repository_harvest(repo_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    conn = get_connection()

    try:
        repository = get_repository(conn, repo_id)

        if repository is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository was not found.",
            )

        try:
            job = create_repository_harvest_job(conn, repo_id)
            conn.commit()
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Repository harvest is already running.",
            ) from exc
    finally:
        conn.close()

    background_tasks.add_task(run_repository_harvest, job["id"], repo_id)
    return job


def queue_embedding_backfill(background_tasks: BackgroundTasks) -> dict[str, Any]:
    conn = get_connection()

    try:
        try:
            job = create_embedding_backfill_job(conn)
            conn.commit()
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Embedding backfill is already running.",
            ) from exc
    finally:
        conn.close()

    background_tasks.add_task(run_embedding_backfill, job["id"])
    return job


def run_repository_harvest(job_id: int, repo_id: int) -> None:
    conn = None

    try:
        conn = get_connection()
        repository = get_repository(conn, repo_id)

        if repository is None:
            raise RuntimeError("Repository was not found.")

        processed_records = harvest_repository(conn, repository)
        update_job(
            job_id,
            status="succeeded",
            processed_records=processed_records,
            message=f"Harvest completed. Processed records: {processed_records}.",
        )
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        update_job(
            job_id,
            status="failed",
            processed_records=None,
            message=f"Harvest failed: {exc}",
        )
    finally:
        if conn is not None:
            conn.close()


def run_embedding_backfill(job_id: int) -> None:
    from embeddings.backfill import embed_missing_publications

    conn = None

    try:
        conn = get_connection()
        embedded_count = embed_missing_publications(conn, show_progress_bar=False)
        update_job(
            job_id,
            status="succeeded",
            processed_records=embedded_count,
            message=f"Embedding backfill completed. Embedded records: {embedded_count}.",
        )
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        update_job(
            job_id,
            status="failed",
            processed_records=None,
            message=f"Embedding backfill failed: {exc}",
        )
    finally:
        if conn is not None:
            conn.close()


def update_job(
    job_id: int,
    status: str,
    processed_records: int | None,
    message: str,
) -> None:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET status = %s,
                    finished_at = NOW(),
                    processed_records = %s,
                    message = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (status, processed_records, message, job_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def acknowledge_job(job_id: int) -> dict[str, str]:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET acknowledged_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND status <> 'running'
                RETURNING id
                """,
                (job_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Completed job was not found.",
            )

        conn.commit()
        return {"status": "ok"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fail_interrupted_jobs() -> None:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.admin_job')")
            if cur.fetchone()[0] is None:
                conn.rollback()
                return

            cur.execute(
                """
                UPDATE admin_job
                SET status = 'failed',
                    finished_at = NOW(),
                    message = CASE
                        WHEN job_type = 'repository_harvest'
                            THEN 'Harvest failed: API process stopped before the job finished.'
                        ELSE 'Embedding backfill failed: API process stopped before the job finished.'
                    END,
                    updated_at = NOW()
                WHERE status = 'running'
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
