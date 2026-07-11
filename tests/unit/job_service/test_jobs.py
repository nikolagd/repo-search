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
