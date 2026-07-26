from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import httpx

from performance_measurement.common import (
    MeasurementError,
    build_metadata,
    require_nonblank,
    sha256_file,
    summarize_values,
    utc_now,
    validate_common_config,
    validate_url,
)


@dataclass(frozen=True)
class PrometheusDefinition:
    name: str
    metric_type: str
    unit: str
    query_kind: str
    query: str
    start: str | float | None = None
    end: str | float | None = None
    step: str | float | None = None


def _range_value(value: Any, field: str) -> str | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise MeasurementError(f"{field} must be a nonblank string or finite number")
    if isinstance(value, str):
        return require_nonblank(value, field)
    converted = float(value)
    if not math.isfinite(converted):
        raise MeasurementError(f"{field} must be a nonblank string or finite number")
    return converted


def _validate_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[PrometheusDefinition]]:
    common = validate_common_config(config)
    section = config.get("prometheus")
    if not isinstance(section, dict):
        raise MeasurementError("prometheus must be a JSON object")
    if set(section) - {"base_url", "api_token_env", "timeout_seconds", "metrics"}:
        raise MeasurementError("prometheus configuration contains unsupported fields")
    timeout = section.get("timeout_seconds", 30.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise MeasurementError("prometheus.timeout_seconds must be finite and positive")
    token_env = section.get("api_token_env")
    if token_env is not None:
        token_env = require_nonblank(token_env, "prometheus.api_token_env")
    rows = section.get("metrics")
    if not isinstance(rows, list) or not rows:
        raise MeasurementError("prometheus.metrics must contain at least one definition")
    definitions = []
    allowed = {"name", "metric_type", "unit", "query_kind", "query", "start", "end", "step"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - allowed:
            raise MeasurementError(f"prometheus.metrics[{index}] contains unsupported fields")
        kind = require_nonblank(row.get("query_kind"), f"prometheus.metrics[{index}].query_kind")
        if kind not in {"query", "query_range"}:
            raise MeasurementError("Prometheus query_kind must be query or query_range")
        start, end, step = row.get("start"), row.get("end"), row.get("step")
        if kind == "query_range" and any(value is None for value in (start, end, step)):
            raise MeasurementError("Prometheus range queries require start, end, and step")
        if kind == "query" and any(value is not None for value in (start, end, step)):
            raise MeasurementError("Prometheus instant queries must not define start, end, or step")
        if kind == "query_range":
            start = _range_value(start, f"prometheus.metrics[{index}].start")
            end = _range_value(end, f"prometheus.metrics[{index}].end")
            step = _range_value(step, f"prometheus.metrics[{index}].step")
        definitions.append(
            PrometheusDefinition(
                name=require_nonblank(row.get("name"), f"prometheus.metrics[{index}].name"),
                metric_type=require_nonblank(row.get("metric_type"), f"prometheus.metrics[{index}].metric_type"),
                unit=require_nonblank(row.get("unit"), f"prometheus.metrics[{index}].unit"),
                query_kind=kind,
                query=require_nonblank(row.get("query"), f"prometheus.metrics[{index}].query"),
                start=start,
                end=end,
                step=step,
            )
        )
    names = [definition.name for definition in definitions]
    if len(names) != len(set(names)):
        raise MeasurementError("Prometheus metric names must be unique")
    return (
        {
            **common,
            "base_url": validate_url(section.get("base_url"), "prometheus.base_url"),
            "api_token_env": token_env,
            "timeout_seconds": float(timeout),
        },
        definitions,
    )


def _sample(timestamp: Any, value: Any, *, definition: PrometheusDefinition, labels: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed_timestamp = float(timestamp)
        parsed_value = float(value)
    except (TypeError, ValueError) as exc:
        raise MeasurementError(f"Prometheus metric {definition.name} contains a non-numeric sample") from exc
    if not math.isfinite(parsed_timestamp) or not math.isfinite(parsed_value):
        raise MeasurementError(f"Prometheus metric {definition.name} contains a non-finite sample")
    if not isinstance(labels, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()):
        raise MeasurementError(f"Prometheus metric {definition.name} contains invalid labels")
    normalized_labels = dict(labels)
    normalized_labels.pop("__name__", None)
    return {
        "metric_name": definition.name,
        "metric_type": definition.metric_type,
        "unit": definition.unit,
        "query_kind": definition.query_kind,
        "labels": dict(sorted(normalized_labels.items())),
        "timestamp": parsed_timestamp,
        "value": parsed_value,
    }


def parse_prometheus_payload(payload: Any, definition: PrometheusDefinition) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise MeasurementError(f"Prometheus metric {definition.name} returned an unsuccessful payload")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("resultType"), str):
        raise MeasurementError(f"Prometheus metric {definition.name} returned malformed data")
    result_type = data["resultType"]
    result = data.get("result")
    samples = []
    if result_type == "vector":
        if not isinstance(result, list):
            raise MeasurementError(f"Prometheus metric {definition.name} returned a malformed vector")
        for series in result:
            if not isinstance(series, dict) or not isinstance(series.get("value"), list) or len(series["value"]) != 2:
                raise MeasurementError(f"Prometheus metric {definition.name} returned a malformed vector sample")
            samples.append(_sample(*series["value"], definition=definition, labels=series.get("metric", {})))
    elif result_type == "matrix":
        if not isinstance(result, list):
            raise MeasurementError(f"Prometheus metric {definition.name} returned a malformed matrix")
        for series in result:
            if not isinstance(series, dict) or not isinstance(series.get("values"), list):
                raise MeasurementError(f"Prometheus metric {definition.name} returned a malformed series")
            for value in series["values"]:
                if not isinstance(value, list) or len(value) != 2:
                    raise MeasurementError(f"Prometheus metric {definition.name} returned a malformed range sample")
                samples.append(_sample(*value, definition=definition, labels=series.get("metric", {})))
    elif result_type == "scalar":
        if not isinstance(result, list) or len(result) != 2:
            raise MeasurementError(f"Prometheus metric {definition.name} returned a malformed scalar")
        samples.append(_sample(*result, definition=definition, labels={}))
    else:
        raise MeasurementError(f"Prometheus metric {definition.name} returned unsupported result type")
    return samples


def _unavailable(definition: PrometheusDefinition, reason: str) -> dict[str, Any]:
    return {
        "name": definition.name,
        "metric_type": definition.metric_type,
        "unit": definition.unit,
        "query_kind": definition.query_kind,
        "availability": "unavailable",
        "unavailable_reason": reason,
        "sample_count": 0,
        "mean": None,
        "median": None,
        "minimum": None,
        "maximum": None,
        "p50": None,
        "p95": None,
    }


def collect_resources(
    config: dict[str, Any],
    *,
    api_token: str | None,
    git_commit: str,
    config_sha256: str,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    validated, definitions = _validate_config(config)
    if validated["api_token_env"] is not None and not api_token:
        raise MeasurementError("configured Prometheus API token environment variable is not set")
    started_at = clock()
    own_client = client is None
    active_client = client or httpx.Client(timeout=validated["timeout_seconds"])
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
    raw_samples = []
    summaries = []
    try:
        for definition in definitions:
            endpoint = "query" if definition.query_kind == "query" else "query_range"
            params: dict[str, Any] = {"query": definition.query}
            if definition.query_kind == "query_range":
                params.update({"start": definition.start, "end": definition.end, "step": definition.step})
            try:
                response = active_client.get(
                    f"{validated['base_url']}/api/v1/{endpoint}",
                    params=params,
                    headers=headers,
                    timeout=validated["timeout_seconds"],
                )
            except httpx.TimeoutException:
                summaries.append(_unavailable(definition, "timeout"))
                continue
            except httpx.RequestError:
                summaries.append(_unavailable(definition, "connection"))
                continue
            if not 200 <= response.status_code < 300:
                summaries.append(_unavailable(definition, "http_error"))
                continue
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                summaries.append(_unavailable(definition, "malformed_json"))
                continue
            try:
                samples = parse_prometheus_payload(payload, definition)
            except MeasurementError as exc:
                if "unsuccessful payload" in str(exc):
                    summaries.append(_unavailable(definition, "prometheus_error"))
                    continue
                raise
            if not samples:
                summaries.append(_unavailable(definition, "no_samples"))
                continue
            raw_samples.extend(samples)
            summary = summarize_values([sample["value"] for sample in samples])
            summaries.append(
                {
                    "name": definition.name,
                    "metric_type": definition.metric_type,
                    "unit": definition.unit,
                    "query_kind": definition.query_kind,
                    "availability": "available",
                    "unavailable_reason": None,
                    **{key: value for key, value in summary.items() if key not in {"attempted_sample_count", "failed_sample_count"}},
                }
            )
    finally:
        if own_client:
            active_client.close()
    finished_at = clock()
    return {
        "metadata": build_metadata(
            config,
            measurement_type="resources",
            git_commit=git_commit,
            started_at=started_at,
            finished_at=finished_at,
            input_sha256={"config": config_sha256},
        ),
        "prometheus_base_url": validated["base_url"],
        "metric_definitions": [
            {
                "name": definition.name,
                "metric_type": definition.metric_type,
                "unit": definition.unit,
                "query_kind": definition.query_kind,
                "query": definition.query,
                **(
                    {"start": definition.start, "end": definition.end, "step": definition.step}
                    if definition.query_kind == "query_range"
                    else {}
                ),
            }
            for definition in definitions
        ],
        "metric_summaries": summaries,
        "samples": raw_samples,
    }


def config_hash(path: str) -> str:
    return sha256_file(path)
