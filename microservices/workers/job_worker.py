from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from microservices.common.app_logging import emit_app_event
from microservices.common.config import service_url
from microservices.common.embedding_provenance import embedding_is_current
from microservices.common.db import get_connection
from microservices.common.http import observed_sync_request
from microservices.common.observability import (
    record_harvest_records,
    record_job_duration,
    record_job_event,
    start_worker_observability,
    trace_span,
)
from microservices.common.security import internal_headers
from microservices.workers.oai_client import OAINoRecordsMatch, choose_metadata_prefix, fetch_page, get_granularity
from microservices.workers.parser import parse_oai_xml
from microservices.workers.time_utils import format_oai_from_date, utc_now_naive

CATALOG_SERVICE_URL = service_url("CATALOG_SERVICE_URL", "http://catalog-service:8000")
EMBEDDING_SERVICE_URL = service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
SEARCH_SERVICE_URL = service_url("SEARCH_SERVICE_URL", "http://search-service:8000")
POLL_INTERVAL_SECONDS = int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "15"))
STALE_JOB_TIMEOUT_SECONDS = float(os.getenv("WORKER_STALE_JOB_TIMEOUT_SECONDS", "120"))
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
HEARTBEAT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
OUTPUT_DIR = Path(os.getenv("HARVEST_OUTPUT_DIR", "/app/data"))
SERVICE_NAME = "job-worker"

if HEARTBEAT_INTERVAL_SECONDS <= 0:
    raise ValueError("WORKER_HEARTBEAT_INTERVAL_SECONDS must be greater than zero.")
if STALE_JOB_TIMEOUT_SECONDS <= HEARTBEAT_INTERVAL_SECONDS:
    raise ValueError("WORKER_STALE_JOB_TIMEOUT_SECONDS must be greater than the heartbeat interval.")
if MAX_ATTEMPTS <= 0:
    raise ValueError("WORKER_MAX_ATTEMPTS must be greater than zero.")


class NonRetryableJobError(RuntimeError):
    pass


def request_json(method: str, url: str, **kwargs) -> Any:
    headers = kwargs.pop("headers", {})
    headers.update(internal_headers())
    response = observed_sync_request(method, url, service_name=SERVICE_NAME, headers=headers, timeout=120, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def claim_next_job(max_attempts: int = MAX_ATTEMPTS) -> dict[str, Any] | None:
    lease_token = uuid4().hex
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET status = 'running',
                    started_at = NOW(),
                    finished_at = NULL,
                    heartbeat_at = NOW(),
                    lease_token = %s,
                    attempt_count = attempt_count + 1,
                    message = 'Job started.',
                    updated_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM admin_job
                    WHERE status = 'queued' AND attempt_count < %s
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, job_type, repository_id, attempt_count, lease_token
                """,
                (lease_token, max_attempts),
            )
            row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        return {
            "id": row[0],
            "job_type": row[1],
            "repository_id": row[2],
            "attempt_count": row[3],
            "lease_token": row[4],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def refresh_job_heartbeat(job_id: int, lease_token: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET heartbeat_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'running'
                  AND lease_token = %s
                RETURNING id
                """,
                (job_id, lease_token),
            )
            updated = cur.fetchone() is not None
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_job(
    job_id: int,
    lease_token: str,
    status: str,
    processed_records: int | None,
    message: str,
) -> bool:
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
                    heartbeat_at = NULL,
                    lease_token = NULL,
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'running'
                  AND lease_token = %s
                RETURNING id
                """,
                (status, processed_records, message, job_id, lease_token),
            )
            updated = cur.fetchone() is not None
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def failure_outcome(attempt_count: int, retryable: bool, max_attempts: int = MAX_ATTEMPTS) -> str:
    return "queued" if retryable and attempt_count < max_attempts else "failed"


def fail_or_requeue_job(
    job: dict[str, Any],
    error: str,
    *,
    retryable: bool,
    max_attempts: int = MAX_ATTEMPTS,
) -> str | None:
    outcome = failure_outcome(job["attempt_count"], retryable, max_attempts)
    if outcome == "queued":
        message = (
            f"Job failed on attempt {job['attempt_count']}/{max_attempts} and was requeued: {error}"
        )
    elif retryable:
        message = f"Job failed after final attempt {job['attempt_count']}/{max_attempts}: {error}"
    else:
        message = f"Job failed without retry: {error}"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET status = %s,
                    finished_at = CASE WHEN %s = 'failed' THEN NOW() ELSE NULL END,
                    message = %s,
                    heartbeat_at = NULL,
                    lease_token = NULL,
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'running'
                  AND lease_token = %s
                RETURNING status
                """,
                (outcome, outcome, message, job["id"], job["lease_token"]),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0] if row is not None else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recover_stale_jobs(
    stale_timeout_seconds: float = STALE_JOB_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    batch_size: int = 100,
) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH stale_jobs AS (
                    SELECT id
                    FROM admin_job
                    WHERE status = 'running'
                      AND COALESCE(heartbeat_at, started_at, created_at)
                          < NOW() - (%s * INTERVAL '1 second')
                    ORDER BY COALESCE(heartbeat_at, started_at, created_at), id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE admin_job AS job
                SET status = CASE
                        WHEN job.attempt_count < %s THEN 'queued'
                        ELSE 'failed'
                    END,
                    finished_at = CASE
                        WHEN job.attempt_count < %s THEN NULL
                        ELSE NOW()
                    END,
                    message = CASE
                        WHEN job.attempt_count < %s
                            THEN format(
                                'Recovered stale lease after attempt %%s/%%s; job requeued.',
                                job.attempt_count,
                                %s
                            )
                        ELSE format(
                            'Stale lease reached maximum attempts %%s/%%s; job failed.',
                            job.attempt_count,
                            %s
                        )
                    END,
                    heartbeat_at = NULL,
                    lease_token = NULL,
                    updated_at = NOW()
                FROM stale_jobs
                WHERE job.id = stale_jobs.id
                RETURNING job.id, job.status, job.attempt_count, job.message
                """,
                (
                    stale_timeout_seconds,
                    batch_size,
                    max_attempts,
                    max_attempts,
                    max_attempts,
                    max_attempts,
                    max_attempts,
                ),
            )
            rows = cur.fetchall()
        conn.commit()
        return [
            {"id": row[0], "status": row[1], "attempt_count": row[2], "message": row[3]}
            for row in rows
        ]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class JobHeartbeat:
    def __init__(
        self,
        job: dict[str, Any],
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        shutdown_timeout_seconds: float = HEARTBEAT_SHUTDOWN_TIMEOUT_SECONDS,
    ):
        self.job = job
        self.interval_seconds = interval_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._stop = Event()
        self._lease_lost = Event()
        self._thread = Thread(target=self._run, name=f"job-heartbeat-{job['id']}", daemon=True)

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                if not refresh_job_heartbeat(self.job["id"], self.job["lease_token"]):
                    self._lease_lost.set()
                    return
            except Exception as exc:
                print(f"Job heartbeat failed: {exc}", file=sys.stderr, flush=True)

    def __enter__(self) -> JobHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=self.shutdown_timeout_seconds)
        if self._thread.is_alive():
            print(
                f"Job heartbeat shutdown timed out for job {self.job['id']}.",
                file=sys.stderr,
                flush=True,
            )


def sync_publication_to_search(publication: dict[str, Any]) -> None:
    with trace_span(
        "job.sync_publication_embedding",
        {
            "repo_search.publication.id": publication.get("id"),
            "repo_search.repository.id": publication.get("repository_id"),
        },
    ):
        embedding_response = request_json(
            "POST",
            f"{EMBEDDING_SERVICE_URL}/embed/document",
            json={
                "title": publication.get("title"),
                "abstract": publication.get("abstract"),
            },
        )

        request_json(
            "POST",
            f"{SEARCH_SERVICE_URL}/publications/{publication['id']}/embedding",
            json=embedding_response,
        )


def harvest_repository(job: dict[str, Any]) -> int:
    repository_id = job["repository_id"]
    repository = request_json("GET", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_processed = 0
    page_num = 1
    base_url = repository["oai_endpoint"]
    last_harvest = repository.get("last_harvest")
    harvest_started_at = utc_now_naive()
    granularity = get_granularity(base_url=base_url)
    metadata_prefix = choose_metadata_prefix(base_url=base_url)
    from_date = format_oai_from_date(datetime.fromisoformat(last_harvest), granularity) if last_harvest else None

    with trace_span(
        "job.harvest_repository",
        {
            "repo_search.job.id": job["id"],
            "repo_search.repository.id": repository_id,
            "repo_search.oai.metadata_prefix": metadata_prefix,
        },
    ) as span:
        try:
            xml_text = fetch_page(
                from_date=from_date,
                metadata_prefix=metadata_prefix,
                base_url=base_url,
            )
        except OAINoRecordsMatch:
            request_json("POST", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/last-harvest")
            if span is not None:
                span.set_attribute("repo_search.records_processed", 0)
            return 0

        while True:
            output_file = OUTPUT_DIR / f"repo_{repository_id}_page_{page_num}.xml"
            output_file.write_text(xml_text, encoding="utf-8")

            records, token = parse_oai_xml(xml_text, metadata_prefix)

            for record in records:
                publication_id = request_json(
                    "POST",
                    f"{CATALOG_SERVICE_URL}/publications",
                    json={
                        "repository_id": repository_id,
                        "oai_identifier": record["oai_identifier"],
                        "title": record["title"],
                        "abstract": record["abstract"],
                        "date": record["date"],
                        "source_url": record["source_url"],
                        "authors": record["authors"],
                    },
                )["id"]
                sync_publication_to_search(
                    {
                        "id": publication_id,
                        "repository_id": repository_id,
                        "repository_name": repository["name"],
                        "title": record["title"],
                        "abstract": record["abstract"],
                        "source_url": record["source_url"],
                        "date": record["date"],
                        "oai_identifier": record["oai_identifier"],
                        "authors": record["authors"],
                    }
                )
                total_processed += 1

            if not token:
                break

            xml_text = fetch_page(token, base_url=base_url)
            page_num += 1

        if span is not None:
            span.set_attribute("repo_search.records_processed", total_processed)
            span.set_attribute("repo_search.oai.pages", page_num)

    request_json("POST", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/last-harvest")
    record_harvest_records(SERVICE_NAME, repository.get("name"), "succeeded", total_processed)
    return total_processed


def backfill_embeddings() -> int:
    with trace_span("job.backfill_embeddings") as span:
        publications = request_json("GET", f"{CATALOG_SERVICE_URL}/publications")
        model_status = request_json("GET", f"{EMBEDDING_SERVICE_URL}/model/status")
        model_name = model_status["embedding_model"]
        dimension = model_status["embedding_dimension"]
        processed = 0

        for publication in publications:
            if embedding_is_current(publication, model_name=model_name, dimension=dimension):
                continue
            sync_publication_to_search(publication)
            processed += 1

        if span is not None:
            span.set_attribute("repo_search.records_processed", processed)
        return processed


def execute_job(job: dict[str, Any]) -> tuple[int, str, str]:
    if job["job_type"] == "repository_harvest":
        if job.get("repository_id") is None:
            raise NonRetryableJobError("Repository harvest job is missing repository_id.")
        processed = harvest_repository(job)
        return (
            processed,
            "job.harvest_completed",
            f"Harvest completed. Processed records: {processed}.",
        )

    if job["job_type"] == "embedding_backfill":
        processed = backfill_embeddings()
        return (
            processed,
            "job.embedding_backfill_completed",
            f"Embedding backfill completed. Embedded records: {processed}.",
        )

    raise NonRetryableJobError(f"Unsupported job type: {job['job_type']}")


def run_job(job: dict[str, Any]) -> None:
    started_at = time.perf_counter()
    status = "failed"
    record_job_event(SERVICE_NAME, job["job_type"], "started")
    with trace_span(
        "job.run",
        {
            "repo_search.job.id": job["id"],
            "repo_search.job.type": job["job_type"],
            "repo_search.repository.id": job.get("repository_id"),
            "repo_search.job.attempt_count": job.get("attempt_count"),
        },
    ) as span:
        try:
            heartbeat = JobHeartbeat(job)
            with heartbeat:
                processed, completion_event, completion_message = execute_job(job)

            if heartbeat.lease_lost:
                status = "lease_lost"
                emit_app_event(
                    "job.lease_lost",
                    SERVICE_NAME,
                    job_id=job["id"],
                    job_type=job["job_type"],
                    attempt_count=job.get("attempt_count"),
                    status=status,
                )
                return

            completed = finish_job(
                job["id"],
                job["lease_token"],
                "succeeded",
                processed,
                completion_message,
            )
            if not completed:
                status = "lease_lost"
                emit_app_event(
                    "job.lease_lost",
                    SERVICE_NAME,
                    job_id=job["id"],
                    job_type=job["job_type"],
                    attempt_count=job.get("attempt_count"),
                    status=status,
                )
                return

            if span is not None:
                span.set_attribute("repo_search.records_processed", processed)
            emit_app_event(
                completion_event,
                SERVICE_NAME,
                job_id=job["id"],
                repository_id=job.get("repository_id"),
                processed_records=processed,
                attempt_count=job.get("attempt_count"),
                status="succeeded",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
            status = "succeeded"
        except Exception as exc:
            retryable = not isinstance(exc, NonRetryableJobError)
            outcome = fail_or_requeue_job(job, str(exc), retryable=retryable)
            status = outcome or "failed"
            if span is not None:
                span.set_attribute("repo_search.job.error", str(exc))
            emit_app_event(
                "job.failed",
                SERVICE_NAME,
                job_id=job["id"],
                job_type=job["job_type"],
                repository_id=job.get("repository_id"),
                attempt_count=job.get("attempt_count"),
                retryable=retryable,
                status=status,
                error=str(exc),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
        finally:
            if span is not None:
                span.set_attribute("repo_search.job.status", status)
            record_job_event(SERVICE_NAME, job["job_type"], status)
            record_job_duration(SERVICE_NAME, job["job_type"], status, time.perf_counter() - started_at)


def main() -> None:
    start_worker_observability(SERVICE_NAME)
    while True:
        try:
            recover_stale_jobs()
            job = claim_next_job()
        except Exception as exc:
            print(f"Worker poll failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        run_job(job)


if __name__ == "__main__":
    main()
