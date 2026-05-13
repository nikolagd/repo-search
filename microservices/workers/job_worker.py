from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from microservices.common.config import service_url
from microservices.common.db import get_connection
from microservices.common.security import internal_headers
from microservices.workers.oai_client import OAINoRecordsMatch, choose_metadata_prefix, fetch_page, get_granularity
from microservices.workers.parser import parse_oai_xml
from microservices.workers.time_utils import format_oai_from_date, utc_now_naive

CATALOG_SERVICE_URL = service_url("CATALOG_SERVICE_URL", "http://catalog-service:8000")
EMBEDDING_SERVICE_URL = service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
SEARCH_SERVICE_URL = service_url("SEARCH_SERVICE_URL", "http://search-service:8000")
POLL_INTERVAL_SECONDS = int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))
OUTPUT_DIR = Path(os.getenv("HARVEST_OUTPUT_DIR", "/app/data"))


def request_json(method: str, url: str, **kwargs) -> Any:
    headers = kwargs.pop("headers", {})
    headers.update(internal_headers())
    response = requests.request(method, url, headers=headers, timeout=120, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def claim_next_job() -> dict[str, Any] | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_job
                SET status = 'running',
                    message = 'Job started.',
                    updated_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM admin_job
                    WHERE status = 'queued'
                    ORDER BY started_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, job_type, repository_id
                """
            )
            row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        return {"id": row[0], "job_type": row[1], "repository_id": row[2]}
    finally:
        conn.close()


def finish_job(job_id: int, status: str, processed_records: int | None, message: str) -> None:
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


def sync_publication_to_search(publication: dict[str, Any]) -> None:
    embedding = request_json(
        "POST",
        f"{EMBEDDING_SERVICE_URL}/embed/document",
        json={
            "title": publication.get("title"),
            "abstract": publication.get("abstract"),
        },
    )["embedding"]

    request_json(
        "POST",
        f"{SEARCH_SERVICE_URL}/publications",
        json={**publication, "embedding": embedding},
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

    try:
        xml_text = fetch_page(
            from_date=from_date,
            metadata_prefix=metadata_prefix,
            base_url=base_url,
        )
    except OAINoRecordsMatch:
        request_json("POST", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/last-harvest")
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

    request_json("POST", f"{CATALOG_SERVICE_URL}/repositories/{repository_id}/last-harvest")
    return total_processed


def backfill_embeddings() -> int:
    publications = request_json("GET", f"{CATALOG_SERVICE_URL}/publications")
    processed = 0

    for publication in publications:
        sync_publication_to_search(publication)
        processed += 1

    return processed


def run_job(job: dict[str, Any]) -> None:
    try:
        if job["job_type"] == "repository_harvest":
            processed = harvest_repository(job)
            finish_job(
                job["id"],
                "succeeded",
                processed,
                f"Harvest completed. Processed records: {processed}.",
            )
            return

        if job["job_type"] == "embedding_backfill":
            processed = backfill_embeddings()
            finish_job(
                job["id"],
                "succeeded",
                processed,
                f"Embedding backfill completed. Embedded records: {processed}.",
            )
            return

        finish_job(job["id"], "failed", None, f"Unsupported job type: {job['job_type']}")
    except Exception as exc:
        finish_job(job["id"], "failed", None, f"Job failed: {exc}")


def main() -> None:
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        run_job(job)


if __name__ == "__main__":
    main()
