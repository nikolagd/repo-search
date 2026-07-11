from __future__ import annotations

import time
from contextlib import nullcontext
from threading import Event
from typing import Any

import pytest

from microservices.workers import job_worker


class FakeHeartbeat:
    lease_lost = False

    def __init__(self, _job: dict[str, Any]):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        pass


@pytest.fixture
def isolated_run_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_worker, "JobHeartbeat", FakeHeartbeat)
    monkeypatch.setattr(job_worker, "trace_span", lambda *_args, **_kwargs: nullcontext(None))
    monkeypatch.setattr(job_worker, "emit_app_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_worker, "record_job_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_worker, "record_job_duration", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize(
    ("attempt_count", "retryable", "expected"),
    [
        (1, True, "queued"),
        (2, True, "queued"),
        (3, True, "failed"),
        (1, False, "failed"),
    ],
)
def test_failure_outcome_is_bounded(
    attempt_count: int,
    retryable: bool,
    expected: str,
) -> None:
    assert job_worker.failure_outcome(attempt_count, retryable, max_attempts=3) == expected


def test_execute_job_rejects_unsupported_type_without_external_calls() -> None:
    with pytest.raises(job_worker.NonRetryableJobError, match="Unsupported job type"):
        job_worker.execute_job({"job_type": "unknown"})


def test_execute_job_rejects_harvest_without_repository() -> None:
    with pytest.raises(job_worker.NonRetryableJobError, match="missing repository_id"):
        job_worker.execute_job({"job_type": "repository_harvest", "repository_id": None})


def test_heartbeat_thread_refreshes_during_slow_work(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed_twice = Event()
    calls: list[tuple[int, str]] = []

    def refresh(job_id: int, lease_token: str) -> bool:
        calls.append((job_id, lease_token))
        if len(calls) >= 2:
            refreshed_twice.set()
        return True

    monkeypatch.setattr(job_worker, "refresh_job_heartbeat", refresh)
    job = {"id": 7, "lease_token": "lease-7"}

    with job_worker.JobHeartbeat(job, interval_seconds=0.01):
        assert refreshed_twice.wait(timeout=1)

    assert len(calls) >= 2
    assert set(calls) == {(7, "lease-7")}


def test_heartbeat_shutdown_is_bounded_when_database_refresh_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_started = Event()
    release_refresh = Event()

    def blocked_refresh(_job_id: int, _lease_token: str) -> bool:
        refresh_started.set()
        release_refresh.wait(timeout=2)
        return False

    monkeypatch.setattr(job_worker, "refresh_job_heartbeat", blocked_refresh)
    heartbeat = job_worker.JobHeartbeat(
        {"id": 8, "lease_token": "old-lease"},
        interval_seconds=0.01,
        shutdown_timeout_seconds=0.02,
    )

    started_at = time.perf_counter()
    with heartbeat:
        assert refresh_started.wait(timeout=1)
    shutdown_duration = time.perf_counter() - started_at

    assert shutdown_duration < 0.5
    assert heartbeat._thread.is_alive()

    release_refresh.set()
    heartbeat._thread.join(timeout=1)
    assert not heartbeat._thread.is_alive()


@pytest.mark.parametrize(
    ("heartbeat_lost", "finish_result"),
    [(True, None), (False, False)],
)
def test_run_job_records_lease_lost_observability(
    monkeypatch: pytest.MonkeyPatch,
    isolated_run_job: None,
    heartbeat_lost: bool,
    finish_result: bool | None,
) -> None:
    class LeaseStateHeartbeat(FakeHeartbeat):
        lease_lost = heartbeat_lost

    job = {
        "id": 9,
        "job_type": "embedding_backfill",
        "repository_id": None,
        "attempt_count": 1,
        "lease_token": "lease-9",
    }
    event_statuses: list[str] = []
    duration_statuses: list[str] = []

    monkeypatch.setattr(job_worker, "JobHeartbeat", LeaseStateHeartbeat)
    monkeypatch.setattr(
        job_worker,
        "execute_job",
        lambda _job: (3, "job.embedding_backfill_completed", "completed"),
    )

    if finish_result is None:
        monkeypatch.setattr(
            job_worker,
            "finish_job",
            lambda *_args, **_kwargs: pytest.fail("finish_job must not run after heartbeat lease loss"),
        )
    else:
        monkeypatch.setattr(job_worker, "finish_job", lambda *_args, **_kwargs: finish_result)

    monkeypatch.setattr(
        job_worker,
        "record_job_event",
        lambda _service, _job_type, status: event_statuses.append(status),
    )
    monkeypatch.setattr(
        job_worker,
        "record_job_duration",
        lambda _service, _job_type, status, _duration: duration_statuses.append(status),
    )

    job_worker.run_job(job)

    assert event_statuses[-1] == "lease_lost"
    assert duration_statuses == ["lease_lost"]


@pytest.mark.parametrize(
    ("attempt_count", "expected_status"),
    [(1, "queued"), (3, "failed")],
)
def test_transient_execution_failure_uses_bounded_retry_decision(
    monkeypatch: pytest.MonkeyPatch,
    isolated_run_job: None,
    attempt_count: int,
    expected_status: str,
) -> None:
    job = {
        "id": 10,
        "job_type": "repository_harvest",
        "repository_id": 5,
        "attempt_count": attempt_count,
        "lease_token": "lease-10",
    }
    decisions: list[tuple[bool, str]] = []

    monkeypatch.setattr(job_worker, "execute_job", lambda _job: (_ for _ in ()).throw(RuntimeError("timeout")))

    def fail_or_requeue(current_job, error, *, retryable, max_attempts=job_worker.MAX_ATTEMPTS):
        outcome = job_worker.failure_outcome(current_job["attempt_count"], retryable, max_attempts=3)
        decisions.append((retryable, outcome))
        return outcome

    monkeypatch.setattr(job_worker, "fail_or_requeue_job", fail_or_requeue)

    job_worker.run_job(job)

    assert decisions == [(True, expected_status)]


def test_non_retryable_validation_failure_is_not_requeued(
    monkeypatch: pytest.MonkeyPatch,
    isolated_run_job: None,
) -> None:
    job = {
        "id": 11,
        "job_type": "unsupported",
        "repository_id": None,
        "attempt_count": 1,
        "lease_token": "lease-11",
    }
    decisions: list[bool] = []

    def fail_without_retry(_job, _error, *, retryable, max_attempts=job_worker.MAX_ATTEMPTS):
        decisions.append(retryable)
        return "failed"

    monkeypatch.setattr(job_worker, "fail_or_requeue_job", fail_without_retry)

    job_worker.run_job(job)

    assert decisions == [False]
