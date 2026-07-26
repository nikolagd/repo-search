from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from performance_measurement.common import (
    MeasurementError,
    build_metadata,
    format_utc,
    require_nonblank,
    require_positive_float,
    utc_now,
    validate_common_config,
    validate_url,
)


TERMINAL_STATUSES = {"succeeded", "failed"}
VALID_STATUSES = {"queued", "running", *TERMINAL_STATUSES}


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    common = validate_common_config(config)
    section = config.get("backfill")
    if not isinstance(section, dict):
        raise MeasurementError("backfill must be a JSON object")
    allowed = {
        "job_service_url",
        "api_token_env",
        "poll_interval_seconds",
        "timeout_seconds",
        "request_timeout_seconds",
        "job_timestamp_timezone",
    }
    if set(section) - allowed:
        raise MeasurementError("backfill configuration contains unsupported fields")
    timestamp_timezone = require_nonblank(
        section.get("job_timestamp_timezone"),
        "backfill.job_timestamp_timezone",
    )
    if timestamp_timezone != "UTC":
        raise MeasurementError("backfill.job_timestamp_timezone must be UTC")
    return {
        **common,
        "job_service_url": validate_url(section.get("job_service_url"), "backfill.job_service_url"),
        "api_token_env": require_nonblank(section.get("api_token_env"), "backfill.api_token_env"),
        "poll_interval_seconds": require_positive_float(section.get("poll_interval_seconds", 2.0), "backfill.poll_interval_seconds"),
        "timeout_seconds": require_positive_float(section.get("timeout_seconds", 3600.0), "backfill.timeout_seconds"),
        "request_timeout_seconds": require_positive_float(section.get("request_timeout_seconds", 30.0), "backfill.request_timeout_seconds"),
        "job_timestamp_timezone": timestamp_timezone,
    }


def _request_json(response: httpx.Response, label: str) -> Any:
    if not 200 <= response.status_code < 300:
        raise MeasurementError(f"{label} returned HTTP {response.status_code}")
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise MeasurementError(f"{label} returned malformed JSON") from exc


def _validate_job(payload: Any, *, expected_id: int | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MeasurementError("Job Service returned a malformed job")
    job_id = payload.get("id")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise MeasurementError("Job Service returned an invalid job ID")
    if expected_id is not None and job_id != expected_id:
        raise MeasurementError("Job Service returned an unexpected job ID")
    if payload.get("job_type") != "embedding_backfill":
        raise MeasurementError("Job Service returned an unexpected job type")
    status = payload.get("status")
    if status not in VALID_STATUSES:
        raise MeasurementError("Job Service returned an invalid job status")
    attempt_count = payload.get("attempt_count", 0)
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count < 0:
        raise MeasurementError("Job Service returned an invalid attempt count")
    processed = payload.get("processed_records")
    if processed is not None and (isinstance(processed, bool) or not isinstance(processed, int) or processed < 0):
        raise MeasurementError("Job Service returned an invalid processed record count")
    return {
        "id": job_id,
        "job_type": "embedding_backfill",
        "status": status,
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "attempt_count": attempt_count,
        "processed_records": processed,
        "message": payload.get("message") if isinstance(payload.get("message"), str) else None,
    }


def _job_timestamp(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MeasurementError(f"{field} must be a timestamp or null")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MeasurementError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration(start: str | None, finish: str | None) -> float | None:
    if start is None or finish is None:
        return None
    started = _job_timestamp(start, "job.started_at")
    finished = _job_timestamp(finish, "job.finished_at")
    assert started is not None and finished is not None
    seconds = (finished - started).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        raise MeasurementError("Job Service returned invalid job timestamps")
    return seconds


def run_backfill_measurement(
    config: dict[str, Any],
    *,
    api_token: str,
    git_commit: str,
    config_sha256: str,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validated = _validate_config(config)
    token = require_nonblank(api_token, "API token environment variable")
    headers = {"X-API-Key": token}
    started_at = clock()
    own_client = client is None
    active_client = client or httpx.Client(timeout=validated["request_timeout_seconds"])
    observations = []
    try:
        try:
            response = active_client.post(
                f"{validated['job_service_url']}/jobs/embedding-backfill",
                headers=headers,
                timeout=validated["request_timeout_seconds"],
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise MeasurementError("embedding backfill creation request failed") from exc
        created = _validate_job(_request_json(response, "embedding backfill creation"))
        queued_observed_at = clock()
        observations.append({"observed_at_utc": format_utc(queued_observed_at), **created})
        job_id = created["id"]
        deadline = monotonic() + validated["timeout_seconds"]
        final_job = created
        while final_job["status"] not in TERMINAL_STATUSES:
            if monotonic() >= deadline:
                raise MeasurementError("embedding backfill polling timed out")
            sleeper(validated["poll_interval_seconds"])
            try:
                response = active_client.get(
                    f"{validated['job_service_url']}/jobs",
                    params={"job_type": "embedding_backfill", "include_acknowledged": "true"},
                    headers=headers,
                    timeout=validated["request_timeout_seconds"],
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise MeasurementError("embedding backfill polling request failed") from exc
            payload = _request_json(response, "embedding backfill polling")
            if not isinstance(payload, list):
                raise MeasurementError("Job Service jobs response must be an array")
            match = next((row for row in payload if isinstance(row, dict) and row.get("id") == job_id), None)
            if match is None:
                raise MeasurementError("created embedding backfill job disappeared from polling results")
            final_job = _validate_job(match, expected_id=job_id)
            observations.append({"observed_at_utc": format_utc(clock()), **final_job})
    finally:
        if own_client:
            active_client.close()

    finished_at = clock()
    service_duration = _duration(final_job["started_at"], final_job["finished_at"])
    observed_duration = (finished_at - queued_observed_at).total_seconds()
    if not math.isfinite(observed_duration) or observed_duration < 0:
        raise MeasurementError("observed backfill duration is invalid")
    processed = final_job["processed_records"]
    records_per_second = None
    if final_job["status"] == "succeeded" and processed is not None and service_duration is not None:
        if service_duration == 0:
            if processed:
                raise MeasurementError("nonzero processed records cannot have zero duration")
            records_per_second = None
        else:
            records_per_second = processed / service_duration
    return {
        "metadata": build_metadata(
            config,
            measurement_type="embedding_backfill",
            git_commit=git_commit,
            started_at=started_at,
            finished_at=finished_at,
            input_sha256={"config": config_sha256},
        ),
        "job_service_url": validated["job_service_url"],
        "job": {
            "id": final_job["id"],
            "status": final_job["status"],
            "queued_at_utc": format_utc(queued_observed_at),
            "started_at_utc": format_utc(_job_timestamp(final_job["started_at"], "job.started_at")) if final_job["started_at"] else None,
            "finished_at_utc": format_utc(_job_timestamp(final_job["finished_at"], "job.finished_at")) if final_job["finished_at"] else None,
            "attempts": final_job["attempt_count"],
            "processed_records": processed,
            "service_duration_seconds": service_duration,
            "observed_duration_seconds": observed_duration,
            "records_per_second": records_per_second,
            "message": final_job["message"],
        },
        "samples": observations,
    }
