from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest

from microservices.job_service import main as job_service
from microservices.workers import job_worker


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def job_database(
    postgres_connection_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], Any]:
    monkeypatch.setattr(job_service, "get_connection", postgres_connection_factory)
    monkeypatch.setattr(job_service, "refresh_job_metrics", lambda: None)
    monkeypatch.setattr(job_worker, "get_connection", postgres_connection_factory)
    job_service.ensure_schema()
    return postgres_connection_factory


def insert_job(
    connection_factory: Callable[[], Any],
    *,
    job_type: str = "repository_harvest",
    repository_id: int | None = 1,
    status: str = "queued",
    attempt_count: int = 0,
    heartbeat_at: datetime | None = None,
    lease_token: str | None = None,
    started_at: datetime | None = None,
) -> int:
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO admin_job (
                    job_type, repository_id, status, message, attempt_count,
                    heartbeat_at, lease_token, started_at
                )
                VALUES (%s, %s, %s, 'test job', %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    job_type,
                    repository_id,
                    status,
                    attempt_count,
                    heartbeat_at,
                    lease_token,
                    started_at or utc_now_naive(),
                ),
            )
            job_id = cursor.fetchone()[0]
        connection.commit()
        return job_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_job(connection_factory: Callable[[], Any], job_id: int) -> dict[str, Any]:
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, attempt_count, heartbeat_at, lease_token,
                       finished_at, processed_records, message
                FROM admin_job
                WHERE id = %s
                """,
                (job_id,),
            )
            row = cursor.fetchone()
        return {
            "status": row[0],
            "attempt_count": row[1],
            "heartbeat_at": row[2],
            "lease_token": row[3],
            "finished_at": row[4],
            "processed_records": row[5],
            "message": row[6],
        }
    finally:
        connection.close()


def test_ensure_schema_upgrades_legacy_admin_job_table(
    postgres_connection_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = postgres_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE admin_job (
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
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO admin_job (job_type, status, message)
                VALUES ('embedding_backfill', 'queued', 'legacy row')
                """
            )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(job_service, "get_connection", postgres_connection_factory)
    job_service.ensure_schema()

    connection = postgres_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT attempt_count, heartbeat_at, lease_token
                FROM admin_job
                WHERE message = 'legacy row'
                """
            )
            assert cursor.fetchone() == (0, None, None)
    finally:
        connection.close()


def test_two_concurrent_claims_return_job_to_only_one_worker(
    job_database: Callable[[], Any],
) -> None:
    job_id = insert_job(job_database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: job_worker.claim_next_job(max_attempts=3), range(2)))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == job_id
    assert claimed[0]["attempt_count"] == 1
    assert read_job(job_database, job_id)["attempt_count"] == 1


def test_recent_heartbeat_is_not_recovered(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(
        job_database,
        status="running",
        attempt_count=1,
        heartbeat_at=utc_now_naive(),
        lease_token="recent-lease",
        started_at=utc_now_naive() - timedelta(hours=1),
    )

    assert job_worker.recover_stale_jobs(stale_timeout_seconds=60, max_attempts=3) == []
    job = read_job(job_database, job_id)
    assert job["status"] == "running"
    assert job["lease_token"] == "recent-lease"


def test_stale_legacy_job_is_requeued_when_attempts_remain(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(
        job_database,
        status="running",
        attempt_count=1,
        heartbeat_at=None,
        lease_token="stale-lease",
        started_at=utc_now_naive() - timedelta(minutes=10),
    )

    recovered = job_worker.recover_stale_jobs(stale_timeout_seconds=60, max_attempts=3)

    assert recovered[0]["id"] == job_id
    assert recovered[0]["status"] == "queued"
    job = read_job(job_database, job_id)
    assert job["status"] == "queued"
    assert job["heartbeat_at"] is None
    assert job["lease_token"] is None
    assert "requeued" in job["message"]


def test_stale_job_is_failed_after_maximum_attempts(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(
        job_database,
        status="running",
        attempt_count=3,
        heartbeat_at=utc_now_naive() - timedelta(minutes=10),
        lease_token="final-lease",
    )

    recovered = job_worker.recover_stale_jobs(stale_timeout_seconds=60, max_attempts=3)

    assert recovered[0]["status"] == "failed"
    job = read_job(job_database, job_id)
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert job["lease_token"] is None
    assert "maximum attempts" in job["message"]


def test_concurrent_recovery_updates_stale_job_once(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(
        job_database,
        status="running",
        attempt_count=1,
        heartbeat_at=utc_now_naive() - timedelta(minutes=10),
        lease_token="stale-lease",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: job_worker.recover_stale_jobs(
                    stale_timeout_seconds=60,
                    max_attempts=3,
                ),
                range(2),
            )
        )

    assert sum(len(result) for result in results) == 1
    assert read_job(job_database, job_id)["status"] == "queued"


def test_worker_can_claim_recovered_job(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(
        job_database,
        status="running",
        attempt_count=1,
        heartbeat_at=utc_now_naive() - timedelta(minutes=10),
        lease_token="old-lease",
    )
    job_worker.recover_stale_jobs(stale_timeout_seconds=60, max_attempts=3)

    claimed = job_worker.claim_next_job(max_attempts=3)

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["attempt_count"] == 2
    assert claimed["lease_token"] != "old-lease"


def test_successful_completion_clears_lease_state(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(job_database)
    claimed = job_worker.claim_next_job(max_attempts=3)
    assert claimed is not None

    assert job_worker.refresh_job_heartbeat(job_id, claimed["lease_token"]) is True
    assert job_worker.finish_job(
        job_id,
        claimed["lease_token"],
        "succeeded",
        12,
        "completed",
    ) is True

    job = read_job(job_database, job_id)
    assert job["status"] == "succeeded"
    assert job["heartbeat_at"] is None
    assert job["lease_token"] is None
    assert job["processed_records"] == 12


def test_transient_failure_requeues_and_next_claim_increments_attempt(
    job_database: Callable[[], Any],
) -> None:
    job_id = insert_job(job_database)
    first_claim = job_worker.claim_next_job(max_attempts=3)
    assert first_claim is not None

    assert job_worker.fail_or_requeue_job(
        first_claim,
        "temporary timeout",
        retryable=True,
        max_attempts=3,
    ) == "queued"
    second_claim = job_worker.claim_next_job(max_attempts=3)

    assert second_claim is not None
    assert second_claim["id"] == job_id
    assert second_claim["attempt_count"] == 2


def test_final_execution_failure_becomes_failed(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(job_database, attempt_count=2)
    claimed = job_worker.claim_next_job(max_attempts=3)
    assert claimed is not None
    assert claimed["attempt_count"] == 3

    assert job_worker.fail_or_requeue_job(
        claimed,
        "last timeout",
        retryable=True,
        max_attempts=3,
    ) == "failed"

    job = read_job(job_database, job_id)
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert job["lease_token"] is None


def test_old_lease_owner_cannot_overwrite_reassigned_job(job_database: Callable[[], Any]) -> None:
    job_id = insert_job(job_database)
    old_claim = job_worker.claim_next_job(max_attempts=3)
    assert old_claim is not None

    connection = job_database()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_job
                SET heartbeat_at = NOW() - INTERVAL '10 minutes'
                WHERE id = %s
                """,
                (job_id,),
            )
        connection.commit()
    finally:
        connection.close()

    job_worker.recover_stale_jobs(stale_timeout_seconds=60, max_attempts=3)
    new_claim = job_worker.claim_next_job(max_attempts=3)
    assert new_claim is not None
    assert new_claim["lease_token"] != old_claim["lease_token"]

    assert job_worker.finish_job(
        job_id,
        old_claim["lease_token"],
        "succeeded",
        99,
        "old worker completed",
    ) is False
    job = read_job(job_database, job_id)
    assert job["status"] == "running"
    assert job["lease_token"] == new_claim["lease_token"]


def test_existing_duplicate_active_job_constraints_remain(
    job_database: Callable[[], Any],
) -> None:
    import psycopg2

    insert_job(job_database, repository_id=77)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        insert_job(job_database, repository_id=77)

    insert_job(job_database, job_type="embedding_backfill", repository_id=None)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        insert_job(job_database, job_type="embedding_backfill", repository_id=None)
