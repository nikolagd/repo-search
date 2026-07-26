from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from performance_measurement.backfill import run_backfill_measurement


pytestmark = pytest.mark.unit


def _config():
    return {
        "deployment_label": "compose",
        "expected_models": {
            "embedding_model": "embedding",
            "embedding_model_revision": "revision",
            "embedding_template_version": "template",
            "llm_model": "llm",
        },
        "runtime_identity": {},
        "backfill": {
            "job_service_url": "https://jobs.example.test",
            "api_token_env": "PERFORMANCE_API_TOKEN",
            "poll_interval_seconds": 0.01,
            "timeout_seconds": 30,
            "request_timeout_seconds": 5,
            "job_timestamp_timezone": "UTC",
        },
    }


class StepClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def _job(status: str, *, processed=None, attempts=1):
    return {
        "id": 7,
        "job_type": "embedding_backfill",
        "status": status,
        "started_at": "2026-01-01T00:00:10" if status != "queued" else None,
        "finished_at": "2026-01-01T00:00:12" if status in {"succeeded", "failed"} else None,
        "attempt_count": attempts,
        "processed_records": processed,
        "message": status,
    }


def test_backfill_polling_success_and_throughput(runner_git_commit, verified_runtime_identity) -> None:
    polls = iter([[_job("running", processed=4)], [_job("succeeded", processed=10)]])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_job("queued", attempts=0))
        return httpx.Response(200, json=next(polls))

    report = run_backfill_measurement(
        _config(),
        api_token="sentinel-token",
        runner_git_commit=runner_git_commit,
        runtime_identity=verified_runtime_identity,
        config_sha256="a" * 64,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=StepClock(),
        monotonic=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    assert [row["status"] for row in report["samples"]] == ["queued", "running", "succeeded"]
    assert report["job"]["attempts"] == 1
    assert report["job"]["processed_records"] == 10
    assert report["job"]["service_duration_seconds"] == 2.0
    assert report["job"]["records_per_second"] == 5.0
    assert report["job"]["started_at_utc"].endswith("Z")


def test_backfill_terminal_failure_is_retained_without_throughput(runner_git_commit, verified_runtime_identity) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_job("queued", attempts=0)) if request.method == "POST" else httpx.Response(200, json=[_job("failed", processed=3, attempts=2)])

    report = run_backfill_measurement(
        _config(),
        api_token="token",
        runner_git_commit=runner_git_commit,
        runtime_identity=verified_runtime_identity,
        config_sha256="a" * 64,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=StepClock(),
        monotonic=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    assert report["job"]["status"] == "failed"
    assert report["job"]["attempts"] == 2
    assert report["job"]["processed_records"] == 3
    assert report["job"]["records_per_second"] is None
