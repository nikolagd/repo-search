from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any

from fastapi import BackgroundTasks, HTTPException, status

from etl.db import get_connection, get_repository
from etl.main import harvest_repository

_repository_jobs: dict[int, dict[str, Any]] = {}
_embedding_job: dict[str, Any] | None = None
_jobs_lock = Lock()


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def get_repository_job(repo_id: int) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _repository_jobs.get(repo_id)
        if job and job.get("status") != "running":
            return None
        return dict(job) if job else None


def get_embedding_job() -> dict[str, Any] | None:
    with _jobs_lock:
        if _embedding_job and _embedding_job.get("status") != "running":
            return None
        return dict(_embedding_job) if _embedding_job else None


def list_admin_repositories() -> list[dict[str, Any]]:
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

    repositories = []
    for row in rows:
        repo_id = row[0]
        repositories.append(
            {
                "id": repo_id,
                "name": row[1],
                "oai_endpoint": row[2],
                "last_harvest": serialize_datetime(row[3]),
                "refresh_interval": row[4],
                "harvest_job": get_repository_job(repo_id),
            }
        )

    return repositories


def get_embedding_status() -> dict[str, Any]:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM publication WHERE embedding IS NULL")
            missing_count = cur.fetchone()[0]
    finally:
        conn.close()

    return {
        "missing_embeddings": missing_count,
        "embedding_job": get_embedding_job(),
    }


def queue_repository_harvest(repo_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    conn = get_connection()

    try:
        repository = get_repository(conn, repo_id)
    finally:
        conn.close()

    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository was not found.",
        )

    with _jobs_lock:
        existing = _repository_jobs.get(repo_id)
        if existing and existing["status"] == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Repository harvest is already running.",
            )

        job = {
            "repository_id": repo_id,
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "processed_records": None,
            "message": "Harvest started.",
        }
        _repository_jobs[repo_id] = job

    background_tasks.add_task(run_repository_harvest, repo_id)
    return dict(job)


def queue_embedding_backfill(background_tasks: BackgroundTasks) -> dict[str, Any]:
    global _embedding_job

    with _jobs_lock:
        if _embedding_job and _embedding_job["status"] == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Embedding backfill is already running.",
            )

        _embedding_job = {
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "processed_records": None,
            "message": "Embedding backfill started.",
        }
        job = dict(_embedding_job)

    background_tasks.add_task(run_embedding_backfill)
    return job


def run_repository_harvest(repo_id: int) -> None:
    conn = None

    try:
        conn = get_connection()
        repository = get_repository(conn, repo_id)

        if repository is None:
            raise RuntimeError("Repository was not found.")

        processed_records = harvest_repository(conn, repository)
        update_repository_job(
            repo_id,
            status="succeeded",
            processed_records=processed_records,
            message=f"Harvest completed. Processed records: {processed_records}.",
        )
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        update_repository_job(
            repo_id,
            status="failed",
            processed_records=None,
            message=str(exc),
        )
    finally:
        if conn is not None:
            conn.close()


def run_embedding_backfill() -> None:
    from embeddings.backfill import embed_missing_publications

    conn = None

    try:
        conn = get_connection()
        embedded_count = embed_missing_publications(conn, show_progress_bar=False)
        update_embedding_job(
            status="succeeded",
            processed_records=embedded_count,
            message=f"Embedding backfill completed. Embedded records: {embedded_count}.",
        )
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        update_embedding_job(
            status="failed",
            processed_records=None,
            message=str(exc),
        )
    finally:
        if conn is not None:
            conn.close()


def update_repository_job(
    repo_id: int,
    status: str,
    processed_records: int | None,
    message: str,
) -> None:
    with _jobs_lock:
        job = _repository_jobs.setdefault(
            repo_id,
            {
                "repository_id": repo_id,
                "started_at": None,
            },
        )
        job.update(
            {
                "status": status,
                "finished_at": datetime.now().isoformat(),
                "processed_records": processed_records,
                "message": message,
            }
        )


def update_embedding_job(
    status: str,
    processed_records: int | None,
    message: str,
) -> None:
    global _embedding_job

    with _jobs_lock:
        if _embedding_job is None:
            _embedding_job = {
                "started_at": None,
            }

        _embedding_job.update(
            {
                "status": status,
                "finished_at": datetime.now().isoformat(),
                "processed_records": processed_records,
                "message": message,
            }
        )
