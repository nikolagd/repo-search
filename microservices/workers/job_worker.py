from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
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
from microservices.workers.parser import OAIPageResult, OAITombstone, parse_oai_page
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


class JobLeaseLostError(RuntimeError):
    pass


@dataclass
class HarvestStatistics:
    received_records: int = 0
    parsed_records: int = 0
    skipped_records: int = 0
    deleted_records: int = 0
    deactivated_records: int = 0
    unknown_tombstones: int = 0
    already_inactive_tombstones: int = 0
    invalid_tombstones: int = 0
    processed_records: int = 0
    pages_processed: int = 0

    def add_page(self, page: OAIPageResult) -> None:
        self.received_records += page.received_records
        self.parsed_records += page.parsed_records
        self.skipped_records += page.skipped_records
        self.deleted_records += page.deleted_records
        self.pages_processed += 1

    def add_tombstone_outcome(self, outcome: str) -> None:
        if outcome == "deactivated":
            self.deactivated_records += 1
        elif outcome == "unknown":
            self.unknown_tombstones += 1
        elif outcome == "already_inactive":
            self.already_inactive_tombstones += 1
        elif outcome == "invalid":
            self.invalid_tombstones += 1
        else:
            raise RuntimeError(f"Unsupported tombstone outcome from Catalog Service: {outcome}")


@dataclass(frozen=True)
class JobExecutionResult:
    processed_records: int
    completion_event: str
    completion_message: str
    harvest_statistics: HarvestStatistics | None = None


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
                    processed_records = 0,
                    received_records = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    parsed_records = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    skipped_records = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    deleted_records = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    deactivated_records = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    unknown_tombstones = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    already_inactive_tombstones = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    invalid_tombstones = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
                    pages_processed = CASE WHEN job_type = 'repository_harvest' THEN 0 ELSE NULL END,
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


def update_harvest_statistics(
    job_id: int,
    lease_token: str,
    statistics: HarvestStatistics,
) -> bool:
    """Replace the current attempt snapshot; stored counters are never incremented in SQL."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET received_records = %s,
                    parsed_records = %s,
                    skipped_records = %s,
                    deleted_records = %s,
                    deactivated_records = %s,
                    unknown_tombstones = %s,
                    already_inactive_tombstones = %s,
                    invalid_tombstones = %s,
                    processed_records = %s,
                    pages_processed = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'running'
                  AND lease_token = %s
                RETURNING id
                """,
                (
                    statistics.received_records,
                    statistics.parsed_records,
                    statistics.skipped_records,
                    statistics.deleted_records,
                    statistics.deactivated_records,
                    statistics.unknown_tombstones,
                    statistics.already_inactive_tombstones,
                    statistics.invalid_tombstones,
                    statistics.processed_records,
                    statistics.pages_processed,
                    job_id,
                    lease_token,
                ),
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
    harvest_statistics: HarvestStatistics | None = None,
) -> bool:
    received_records = harvest_statistics.received_records if harvest_statistics is not None else None
    parsed_records = harvest_statistics.parsed_records if harvest_statistics is not None else None
    skipped_records = harvest_statistics.skipped_records if harvest_statistics is not None else None
    deleted_records = harvest_statistics.deleted_records if harvest_statistics is not None else None
    deactivated_records = harvest_statistics.deactivated_records if harvest_statistics is not None else None
    unknown_tombstones = harvest_statistics.unknown_tombstones if harvest_statistics is not None else None
    already_inactive_tombstones = (
        harvest_statistics.already_inactive_tombstones if harvest_statistics is not None else None
    )
    invalid_tombstones = harvest_statistics.invalid_tombstones if harvest_statistics is not None else None
    pages_processed = harvest_statistics.pages_processed if harvest_statistics is not None else None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET status = %s,
                    finished_at = NOW(),
                    processed_records = %s,
                    received_records = %s,
                    parsed_records = %s,
                    skipped_records = %s,
                    deleted_records = %s,
                    deactivated_records = %s,
                    unknown_tombstones = %s,
                    already_inactive_tombstones = %s,
                    invalid_tombstones = %s,
                    pages_processed = %s,
                    message = %s,
                    heartbeat_at = NULL,
                    lease_token = NULL,
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'running'
                  AND lease_token = %s
                RETURNING id
                """,
                (
                    status,
                    processed_records,
                    received_records,
                    parsed_records,
                    skipped_records,
                    deleted_records,
                    deactivated_records,
                    unknown_tombstones,
                    already_inactive_tombstones,
                    invalid_tombstones,
                    pages_processed,
                    message,
                    job_id,
                    lease_token,
                ),
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


def persist_harvest_statistics(job: dict[str, Any], statistics: HarvestStatistics) -> None:
    if not update_harvest_statistics(job["id"], job["lease_token"], statistics):
        raise JobLeaseLostError(f"Lease lost while updating harvest statistics for job {job['id']}.")


def process_tombstone(
    job: dict[str, Any],
    repository_id: int,
    tombstone: OAITombstone,
    statistics: HarvestStatistics,
) -> None:
    if not tombstone.oai_identifier:
        statistics.add_tombstone_outcome("invalid")
        emit_app_event(
            "job.harvest_tombstone_invalid",
            SERVICE_NAME,
            job_id=job["id"],
            repository_id=repository_id,
            oai_datestamp=tombstone.datestamp,
            outcome="invalid",
        )
        return

    result = request_json(
        "POST",
        f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/tombstones",
        json={
            "oai_identifier": tombstone.oai_identifier,
            "datestamp": tombstone.datestamp,
            "set_specs": list(tombstone.set_specs),
        },
    )
    outcome = result.get("status")
    statistics.add_tombstone_outcome(outcome)
    emit_app_event(
        "job.harvest_tombstone_processed",
        SERVICE_NAME,
        job_id=job["id"],
        repository_id=repository_id,
        publication_id=result.get("publication_id"),
        oai_identifier=tombstone.oai_identifier,
        oai_datestamp=tombstone.datestamp,
        outcome=outcome,
        observation_count=result.get("observation_count"),
    )


def harvest_repository(job: dict[str, Any]) -> HarvestStatistics:
    repository_id = job["repository_id"]
    repository = request_json("GET", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    statistics = HarvestStatistics()
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
            persist_harvest_statistics(job, statistics)
            request_json("POST", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/last-harvest")
            if span is not None:
                span.set_attribute("repo_search.records_processed", 0)
                span.set_attribute("repo_search.oai.pages", 0)
            return statistics

        while True:
            output_file = OUTPUT_DIR / f"repo_{repository_id}_page_{page_num}.xml"
            output_file.write_text(xml_text, encoding="utf-8")

            page = parse_oai_page(xml_text, metadata_prefix)
            statistics.add_page(page)
            persist_harvest_statistics(job, statistics)
            page_deactivated_before = statistics.deactivated_records
            page_unknown_before = statistics.unknown_tombstones
            page_already_inactive_before = statistics.already_inactive_tombstones
            page_invalid_before = statistics.invalid_tombstones

            try:
                for tombstone in page.tombstones:
                    process_tombstone(job, repository_id, tombstone, statistics)

                for record in page.records:
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
                    statistics.processed_records += 1
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
            except Exception:
                persist_harvest_statistics(job, statistics)
                raise

            persist_harvest_statistics(job, statistics)
            emit_app_event(
                "job.harvest_page_processed",
                SERVICE_NAME,
                job_id=job["id"],
                repository_id=repository_id,
                page=page_num,
                page_received_records=page.received_records,
                page_parsed_records=page.parsed_records,
                page_skipped_records=page.skipped_records,
                page_deleted_records=page.deleted_records,
                page_deactivated_records=statistics.deactivated_records - page_deactivated_before,
                page_unknown_tombstones=statistics.unknown_tombstones - page_unknown_before,
                page_already_inactive_tombstones=(
                    statistics.already_inactive_tombstones - page_already_inactive_before
                ),
                page_invalid_tombstones=statistics.invalid_tombstones - page_invalid_before,
                received_records=statistics.received_records,
                parsed_records=statistics.parsed_records,
                skipped_records=statistics.skipped_records,
                deleted_records=statistics.deleted_records,
                deactivated_records=statistics.deactivated_records,
                unknown_tombstones=statistics.unknown_tombstones,
                already_inactive_tombstones=statistics.already_inactive_tombstones,
                invalid_tombstones=statistics.invalid_tombstones,
                processed_records=statistics.processed_records,
            )

            if not page.resumption_token:
                break

            xml_text = fetch_page(page.resumption_token, base_url=base_url)
            page_num += 1

        if span is not None:
            span.set_attribute("repo_search.records_processed", statistics.processed_records)
            span.set_attribute("repo_search.oai.records_received", statistics.received_records)
            span.set_attribute("repo_search.oai.records_parsed", statistics.parsed_records)
            span.set_attribute("repo_search.oai.records_skipped", statistics.skipped_records)
            span.set_attribute("repo_search.oai.records_deleted", statistics.deleted_records)
            span.set_attribute("repo_search.oai.records_deactivated", statistics.deactivated_records)
            span.set_attribute("repo_search.oai.tombstones_unknown", statistics.unknown_tombstones)
            span.set_attribute(
                "repo_search.oai.tombstones_already_inactive",
                statistics.already_inactive_tombstones,
            )
            span.set_attribute("repo_search.oai.tombstones_invalid", statistics.invalid_tombstones)
            span.set_attribute("repo_search.oai.pages", statistics.pages_processed)

    request_json("POST", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/last-harvest")
    record_harvest_records(
        SERVICE_NAME,
        repository.get("name"),
        "succeeded",
        statistics.processed_records,
    )
    return statistics


def backfill_embeddings() -> int:
    with trace_span("job.backfill_embeddings") as span:
        publications = request_json("GET", f"{CATALOG_SERVICE_URL}/publications")
        model_status = request_json("GET", f"{EMBEDDING_SERVICE_URL}/model/status")
        model_name = model_status["embedding_model"]
        model_revision = model_status["embedding_model_revision"]
        template_version = model_status["embedding_template_version"]
        dimension = model_status["embedding_dimension"]
        processed = 0

        for publication in publications:
            if embedding_is_current(
                publication,
                model_name=model_name,
                model_revision=model_revision,
                template_version=template_version,
                dimension=dimension,
            ):
                continue
            sync_publication_to_search(publication)
            processed += 1

        if span is not None:
            span.set_attribute("repo_search.records_processed", processed)
        return processed


def execute_job(job: dict[str, Any]) -> JobExecutionResult:
    if job["job_type"] == "repository_harvest":
        if job.get("repository_id") is None:
            raise NonRetryableJobError("Repository harvest job is missing repository_id.")
        statistics = harvest_repository(job)
        return JobExecutionResult(
            processed_records=statistics.processed_records,
            completion_event="job.harvest_completed",
            completion_message=(
                f"Harvest completed. Processed records: {statistics.processed_records}. "
                f"Received: {statistics.received_records}; parsed: {statistics.parsed_records}; "
                f"skipped: {statistics.skipped_records}; deleted: {statistics.deleted_records}; "
                f"deactivated: {statistics.deactivated_records}; "
                f"unknown tombstones: {statistics.unknown_tombstones}; "
                f"already inactive: {statistics.already_inactive_tombstones}; "
                f"invalid tombstones: {statistics.invalid_tombstones}; "
                f"pages: {statistics.pages_processed}."
            ),
            harvest_statistics=statistics,
        )

    if job["job_type"] == "embedding_backfill":
        processed = backfill_embeddings()
        return JobExecutionResult(
            processed_records=processed,
            completion_event="job.embedding_backfill_completed",
            completion_message=f"Embedding backfill completed. Embedded records: {processed}.",
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
                result = execute_job(job)

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
                result.processed_records,
                result.completion_message,
                harvest_statistics=result.harvest_statistics,
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
                span.set_attribute("repo_search.records_processed", result.processed_records)
            statistics_fields = {}
            if result.harvest_statistics is not None:
                statistics_fields = {
                    "received_records": result.harvest_statistics.received_records,
                    "parsed_records": result.harvest_statistics.parsed_records,
                    "skipped_records": result.harvest_statistics.skipped_records,
                    "deleted_records": result.harvest_statistics.deleted_records,
                    "deactivated_records": result.harvest_statistics.deactivated_records,
                    "unknown_tombstones": result.harvest_statistics.unknown_tombstones,
                    "already_inactive_tombstones": result.harvest_statistics.already_inactive_tombstones,
                    "invalid_tombstones": result.harvest_statistics.invalid_tombstones,
                    "pages_processed": result.harvest_statistics.pages_processed,
                }
            emit_app_event(
                result.completion_event,
                SERVICE_NAME,
                job_id=job["id"],
                repository_id=job.get("repository_id"),
                processed_records=result.processed_records,
                attempt_count=job.get("attempt_count"),
                status="succeeded",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
                **statistics_fields,
            )
            status = "succeeded"
        except JobLeaseLostError as exc:
            status = "lease_lost"
            if span is not None:
                span.set_attribute("repo_search.job.error", str(exc))
            emit_app_event(
                "job.lease_lost",
                SERVICE_NAME,
                job_id=job["id"],
                job_type=job["job_type"],
                repository_id=job.get("repository_id"),
                attempt_count=job.get("attempt_count"),
                status=status,
                error=str(exc),
            )
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
