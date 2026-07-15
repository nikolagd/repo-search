from __future__ import annotations

from datetime import datetime

from microservices.job_service.main import job_from_row


def test_job_response_preserves_existing_fields_and_adds_reliability_data() -> None:
    started_at = datetime(2026, 7, 11, 10, 0, 0)
    heartbeat_at = datetime(2026, 7, 11, 10, 1, 0)

    job = job_from_row(
        (
            1,
            "repository_harvest",
            7,
            "running",
            started_at,
            None,
            None,
            "Job started.",
            2,
            heartbeat_at,
        )
    )

    assert job == {
        "id": 1,
        "job_type": "repository_harvest",
        "repository_id": 7,
        "status": "running",
        "started_at": "2026-07-11T10:00:00",
        "finished_at": None,
        "processed_records": None,
        "message": "Job started.",
        "attempt_count": 2,
        "heartbeat_at": "2026-07-11T10:01:00",
        "received_records": None,
        "parsed_records": None,
        "skipped_records": None,
        "deleted_records": None,
        "pages_processed": None,
    }


def test_job_response_accepts_legacy_row_shape() -> None:
    job = job_from_row(
        (
            2,
            "embedding_backfill",
            None,
            "queued",
            datetime(2026, 7, 11, 11, 0, 0),
            None,
            None,
            "Embedding backfill queued.",
        )
    )

    assert job["attempt_count"] == 0
    assert job["heartbeat_at"] is None
    assert job["received_records"] is None
    assert job["parsed_records"] is None
    assert job["skipped_records"] is None
    assert job["deleted_records"] is None
    assert job["pages_processed"] is None


def test_job_response_exposes_harvest_statistics_without_changing_existing_fields() -> None:
    job = job_from_row(
        (
            3,
            "repository_harvest",
            9,
            "succeeded",
            datetime(2026, 7, 11, 12, 0, 0),
            datetime(2026, 7, 11, 12, 1, 0),
            7,
            "Harvest completed.",
            1,
            None,
            10,
            7,
            2,
            1,
            3,
        )
    )

    assert job["processed_records"] == 7
    assert job["received_records"] == 10
    assert job["parsed_records"] == 7
    assert job["skipped_records"] == 2
    assert job["deleted_records"] == 1
    assert job["pages_processed"] == 3
