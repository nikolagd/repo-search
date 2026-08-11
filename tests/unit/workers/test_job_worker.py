from __future__ import annotations

import time
from contextlib import nullcontext
from pathlib import Path
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
        lambda _job: job_worker.JobExecutionResult(
            processed_records=3,
            completion_event="job.embedding_backfill_completed",
            completion_message="completed",
        ),
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


def test_sync_publication_persists_embedding_and_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    provenance = {
        "embedding": [0.0] * 1024,
        "embedding_model": "model-a",
        "embedding_model_revision": "revision-a",
        "embedding_template_version": "template-a",
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": "a" * 64,
    }
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        return provenance if url.endswith("/embed/document") else {"status": "ok"}

    monkeypatch.setattr(job_worker, "request_json", request)
    monkeypatch.setattr(job_worker, "trace_span", lambda *_args, **_kwargs: nullcontext(None))

    job_worker.sync_publication_to_search({"id": 4, "title": "Title", "abstract": "Abstract"})

    assert calls[1][2] == provenance


def test_tombstone_processing_distinguishes_catalog_outcomes_and_invalid_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {"status": "deactivated", "publication_id": 1, "observation_count": 1},
            {"status": "unknown", "publication_id": None, "observation_count": 1},
            {"status": "already_inactive", "publication_id": 1, "observation_count": 2},
        ]
    )
    payloads: list[dict[str, Any]] = []
    events: list[tuple[str, dict[str, Any]]] = []

    def request(_method: str, _url: str, **kwargs):
        payloads.append(kwargs["json"])
        return next(responses)

    monkeypatch.setattr(job_worker, "request_json", request)
    monkeypatch.setattr(
        job_worker,
        "emit_app_event",
        lambda event, _service, **fields: events.append((event, fields)),
    )
    statistics = job_worker.HarvestStatistics(deleted_records=4)
    job = {"id": 13}

    for identifier in ("oai:test:active", "oai:test:unknown", "oai:test:inactive"):
        job_worker.process_tombstone(
            job,
            5,
            job_worker.OAITombstone(identifier, "2026-07-25", ("publications",)),
            statistics,
        )
    job_worker.process_tombstone(
        job,
        5,
        job_worker.OAITombstone(None, "2026-07-25", ()),
        statistics,
    )

    assert statistics.deactivated_records == 1
    assert statistics.unknown_tombstones == 1
    assert statistics.already_inactive_tombstones == 1
    assert statistics.invalid_tombstones == 1
    assert len(payloads) == 3
    assert payloads[0] == {
        "oai_identifier": "oai:test:active",
        "datestamp": "2026-07-25",
        "set_specs": ["publications"],
    }
    assert [event for event, _fields in events] == [
        "job.harvest_tombstone_processed",
        "job.harvest_tombstone_processed",
        "job.harvest_tombstone_processed",
        "job.harvest_tombstone_invalid",
    ]


def test_harvest_accumulates_absolute_statistics_over_multiple_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page_one = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <header><identifier>oai:test:1</identifier></header>
          <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>First</dc:title></oai_dc:dc></metadata>
        </record>
        <record><header status="deleted"><identifier>oai:test:deleted</identifier></header></record>
        <resumptionToken completeListSize="100">page-two</resumptionToken>
      </ListRecords>
    </OAI-PMH>"""
    page_two = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <header><identifier>oai:test:2</identifier></header>
          <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Second</dc:title></oai_dc:dc></metadata>
        </record>
        <record>
          <header/>
          <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Missing identifier</dc:title></oai_dc:dc></metadata>
        </record>
        <record>
          <header><identifier>oai:test:empty</identifier></header>
          <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/"/></metadata>
        </record>
      </ListRecords>
    </OAI-PMH>"""
    fetched_tokens: list[str | None] = []
    persisted_identifiers: list[str] = []
    tombstone_payloads: list[dict[str, Any]] = []
    updates: list[job_worker.HarvestStatistics] = []

    def fetch(resumption_token=None, **_kwargs):
        fetched_tokens.append(resumption_token)
        return page_two if resumption_token == "page-two" else page_one

    def request(method: str, url: str, **kwargs):
        if method == "GET" and url.endswith("/repositories/5"):
            return {
                "id": 5,
                "name": "Synthetic",
                "oai_endpoint": "https://example.test/oai",
                "last_harvest": None,
            }
        if method == "POST" and url.endswith("/publications"):
            persisted_identifiers.append(kwargs["json"]["oai_identifier"])
            return {"id": len(persisted_identifiers)}
        if method == "POST" and url.endswith("/repositories/5/tombstones"):
            tombstone_payloads.append(kwargs["json"])
            return {"status": "unknown", "publication_id": None, "observation_count": 1}
        if method == "POST" and url.endswith("/repositories/5/last-harvest"):
            return {"status": "ok"}
        raise AssertionError(f"Unexpected request: {method} {url}")

    def update(_job_id: int, _lease_token: str, statistics: job_worker.HarvestStatistics) -> bool:
        updates.append(job_worker.HarvestStatistics(**vars(statistics)))
        return True

    monkeypatch.setattr(job_worker, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(job_worker, "get_granularity", lambda **_kwargs: "YYYY-MM-DD")
    monkeypatch.setattr(job_worker, "choose_metadata_prefix", lambda **_kwargs: "oai_dc")
    monkeypatch.setattr(job_worker, "fetch_page", fetch)
    monkeypatch.setattr(job_worker, "request_json", request)
    monkeypatch.setattr(job_worker, "sync_publication_to_search", lambda _publication: None)
    monkeypatch.setattr(job_worker, "update_harvest_statistics", update)
    monkeypatch.setattr(job_worker, "trace_span", lambda *_args, **_kwargs: nullcontext(None))
    monkeypatch.setattr(job_worker, "emit_app_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_worker, "record_harvest_records", lambda *_args, **_kwargs: None)

    statistics = job_worker.harvest_repository(
        {"id": 12, "repository_id": 5, "lease_token": "lease-12"}
    )

    assert statistics == job_worker.HarvestStatistics(
        received_records=5,
        parsed_records=2,
        skipped_records=2,
        deleted_records=1,
        unknown_tombstones=1,
        processed_records=2,
        pages_processed=2,
    )
    assert fetched_tokens == [None, "page-two"]
    assert persisted_identifiers == ["oai:test:1", "oai:test:2"]
    assert tombstone_payloads == [
        {"oai_identifier": "oai:test:deleted", "datestamp": None, "set_specs": []}
    ]
    assert updates[-1] == statistics
    assert [update.pages_processed for update in updates] == [1, 1, 2, 2]
    assert [update.processed_records for update in updates] == [0, 1, 1, 2]


def test_backfill_skips_current_and_regenerates_missing_or_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from microservices.common.embedding_provenance import document_source_hash

    publications = [
        {
            "id": 1,
            "title": "Current",
            "abstract": None,
            "has_embedding": True,
            "embedding_model": "model-a",
            "embedding_model_revision": "revision-a",
            "embedding_template_version": "template-a",
            "embedding_dimension": 1024,
            "embedding_generated_at": "2026-07-11T10:00:00+00:00",
            "embedding_source_hash": document_source_hash("Current", None),
        },
        {"id": 2, "title": "Missing", "abstract": None, "has_embedding": False},
        {
            "id": 3,
            "title": "Changed",
            "abstract": None,
            "has_embedding": True,
            "embedding_model": "model-a",
            "embedding_model_revision": "revision-a",
            "embedding_template_version": "template-a",
            "embedding_dimension": 1024,
            "embedding_generated_at": "2026-07-11T10:00:00+00:00",
            "embedding_source_hash": document_source_hash("Old", None),
        },
    ]

    def request(_method: str, url: str, **_kwargs):
        if url.endswith("/publications"):
            return publications
        if url.endswith("/model/status"):
            return {
                "embedding_model": "model-a",
                "embedding_model_revision": "revision-a",
                "embedding_template_version": "template-a",
                "embedding_dimension": 1024,
            }
        raise AssertionError(url)

    regenerated: list[int] = []
    monkeypatch.setattr(job_worker, "request_json", request)
    monkeypatch.setattr(job_worker, "sync_publication_to_search", lambda publication: regenerated.append(publication["id"]))
    monkeypatch.setattr(job_worker, "trace_span", lambda *_args, **_kwargs: nullcontext(None))

    assert job_worker.backfill_embeddings() == 2
    assert regenerated == [2, 3]
