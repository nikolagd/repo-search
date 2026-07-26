from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from performance_measurement.common import (
    MeasurementError,
    build_metadata,
    format_utc,
    parse_timestamp,
    require_nonblank,
    require_positive_float,
    require_positive_int,
    sha256_file,
    summarize_values,
    utc_now,
    validate_common_config,
    validate_no_secrets,
    validate_url,
)


@dataclass(frozen=True)
class SearchQuery:
    query_id: str
    query: str
    limit: int


def load_queries(path: str | Path) -> list[SearchQuery]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeasurementError("query input is not readable valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"queries"} or not isinstance(payload["queries"], list):
        raise MeasurementError("query input must contain only a queries array")
    queries = []
    for index, row in enumerate(payload["queries"]):
        if not isinstance(row, dict) or set(row) - {"id", "query", "limit"}:
            raise MeasurementError(f"queries[{index}] contains unsupported fields")
        queries.append(
            SearchQuery(
                require_nonblank(row.get("id"), f"queries[{index}].id"),
                require_nonblank(row.get("query"), f"queries[{index}].query"),
                require_positive_int(row.get("limit", 10), f"queries[{index}].limit"),
            )
        )
    if not queries:
        raise MeasurementError("query input must contain at least one query")
    identifiers = [query.query_id for query in queries]
    if len(identifiers) != len(set(identifiers)):
        raise MeasurementError("query IDs must be unique")
    return queries


def validate_cold_evidence(
    evidence: dict[str, Any] | None,
    *,
    deployment_label: str,
    measurement_started_at: datetime,
) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise MeasurementError("cold classification requires external restart/readiness evidence")
    validate_no_secrets(evidence, "cold_evidence")
    required = {
        "deployment_label",
        "source",
        "restart_completed_at_utc",
        "readiness_confirmed_at_utc",
    }
    if set(evidence) != required:
        raise MeasurementError("cold evidence must contain deployment, source, restart, and readiness fields")
    if require_nonblank(evidence["deployment_label"], "cold_evidence.deployment_label") != deployment_label:
        raise MeasurementError("cold evidence deployment label does not match the measurement")
    source = require_nonblank(evidence["source"], "cold_evidence.source")
    restarted = parse_timestamp(evidence["restart_completed_at_utc"], "cold_evidence.restart_completed_at_utc")
    ready = parse_timestamp(evidence["readiness_confirmed_at_utc"], "cold_evidence.readiness_confirmed_at_utc")
    if ready < restarted:
        raise MeasurementError("cold evidence readiness precedes restart completion")
    if ready > measurement_started_at:
        raise MeasurementError("cold evidence readiness occurs after measurement start")
    return {
        "deployment_label": deployment_label,
        "source": source,
        "restart_completed_at_utc": format_utc(restarted),
        "readiness_confirmed_at_utc": format_utc(ready),
    }


def _validate_search_config(config: dict[str, Any]) -> dict[str, Any]:
    common = validate_common_config(config)
    search = config.get("search")
    if not isinstance(search, dict):
        raise MeasurementError("search must be a JSON object")
    allowed = {
        "endpoint",
        "api_token_env",
        "warmup_repetitions",
        "measured_repetitions",
        "timeout_seconds",
        "run_classification",
    }
    if set(search) - allowed:
        raise MeasurementError("search configuration contains unsupported fields")
    api_token_env = require_nonblank(search.get("api_token_env"), "search.api_token_env")
    warmups = require_positive_int(search.get("warmup_repetitions", 1), "search.warmup_repetitions", allow_zero=True)
    measured = require_positive_int(search.get("measured_repetitions", 10), "search.measured_repetitions")
    classification = require_nonblank(search.get("run_classification", "warm"), "search.run_classification")
    if classification not in {"cold", "first_request", "warm"}:
        raise MeasurementError("search.run_classification must be cold, first_request, or warm")
    if classification in {"cold", "first_request"} and warmups:
        raise MeasurementError(f"{classification} classification requires zero warm-up repetitions")
    return {
        **common,
        "endpoint": validate_url(search.get("endpoint"), "search.endpoint"),
        "api_token_env": api_token_env,
        "warmup_repetitions": warmups,
        "measured_repetitions": measured,
        "timeout_seconds": require_positive_float(search.get("timeout_seconds", 180.0), "search.timeout_seconds"),
        "run_classification": classification,
    }


def _validate_response(response: httpx.Response) -> tuple[int, str | None]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise MeasurementError("search response is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise MeasurementError("search response must contain a results array")
    if any(not isinstance(item, dict) for item in payload["results"]):
        raise MeasurementError("search response results must be JSON objects")
    parser_mode = None
    if "plan" in payload:
        plan = payload["plan"]
        if not isinstance(plan, dict):
            raise MeasurementError("search response plan must be a JSON object")
        parser_mode = plan.get("parser_mode")
        if parser_mode is None and isinstance(plan.get("used_fallback"), bool):
            parser_mode = "fallback" if plan["used_fallback"] else "llm"
        if parser_mode is not None and (not isinstance(parser_mode, str) or not parser_mode.strip()):
            raise MeasurementError("search response parser mode is invalid")
    return len(payload["results"]), parser_mode


def _classification(configured: str, request_number: int) -> str:
    if request_number > 1:
        return "warm"
    return configured


def run_search_measurement(
    config: dict[str, Any],
    queries: list[SearchQuery],
    *,
    api_token: str,
    git_commit: str,
    config_sha256: str,
    query_sha256: str,
    cold_evidence: dict[str, Any] | None = None,
    cold_evidence_sha256: str | None = None,
    client: httpx.Client | None = None,
    perf_counter_ns: Callable[[], int] = time.perf_counter_ns,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    validated = _validate_search_config(config)
    token = require_nonblank(api_token, "API token environment variable")
    if not queries:
        raise MeasurementError("at least one query is required")
    started_at = clock()
    evidence = None
    if validated["run_classification"] == "cold":
        evidence = validate_cold_evidence(
            cold_evidence,
            deployment_label=validated["deployment_label"],
            measurement_started_at=started_at,
        )
    elif cold_evidence is not None:
        raise MeasurementError("cold evidence is only valid for a cold run")

    own_client = client is None
    active_client = client or httpx.Client(timeout=validated["timeout_seconds"])
    samples: list[dict[str, Any]] = []
    request_number = 0
    try:
        phases = (
            ("warmup", validated["warmup_repetitions"]),
            ("measured", validated["measured_repetitions"]),
        )
        for phase, repetitions in phases:
            for repetition in range(1, repetitions + 1):
                for query in queries:
                    request_number += 1
                    sample_classification = (
                        "warmup"
                        if phase == "warmup"
                        else _classification(validated["run_classification"], request_number)
                    )
                    started_ns = perf_counter_ns()
                    response = None
                    outcome = "transport_error"
                    error_category = None
                    result_count = None
                    parser_mode = None
                    try:
                        response = active_client.post(
                            validated["endpoint"],
                            json={"query": query.query, "limit": query.limit},
                            headers={"X-API-Key": token},
                            timeout=validated["timeout_seconds"],
                        )
                        if not 200 <= response.status_code < 300:
                            outcome = "http_error"
                            error_category = "non_success_status"
                        else:
                            try:
                                result_count, parser_mode = _validate_response(response)
                                outcome = "succeeded"
                            except MeasurementError:
                                outcome = "invalid_response"
                                error_category = "response_validation"
                    except httpx.TimeoutException:
                        outcome = "transport_error"
                        error_category = "timeout"
                    except httpx.RequestError:
                        outcome = "transport_error"
                        error_category = "connection"
                    finished_ns = perf_counter_ns()
                    if isinstance(started_ns, bool) or isinstance(finished_ns, bool) or not isinstance(started_ns, int) or not isinstance(finished_ns, int) or finished_ns < started_ns:
                        raise MeasurementError("performance counter produced an invalid sample")
                    latency_ns = finished_ns - started_ns
                    latency_ms = latency_ns / 1_000_000
                    samples.append(
                        {
                            "phase": phase,
                            "classification": sample_classification,
                            "query_id": query.query_id,
                            "repetition": repetition,
                            "status": outcome,
                            "outcome": outcome,
                            "http_status": response.status_code if response is not None else None,
                            "latency_ns": latency_ns,
                            "latency_ms": latency_ms,
                            "result_count": result_count,
                            "parser_mode": parser_mode,
                            "error_category": error_category,
                        }
                    )
    finally:
        if own_client:
            active_client.close()

    finished_at = clock()
    measured_samples = [sample for sample in samples if sample["phase"] == "measured"]
    successful = [sample for sample in measured_samples if sample["outcome"] == "succeeded"]
    overall = summarize_values(
        [sample["latency_ms"] for sample in successful],
        attempted_count=len(measured_samples),
    )
    overall = {f"{key}_ms" if key in {"mean", "median", "minimum", "maximum", "p50", "p95"} else key: value for key, value in overall.items()}
    by_classification = []
    for name in ("cold", "first_request", "warm"):
        classified = [sample for sample in measured_samples if sample["classification"] == name]
        if not classified:
            continue
        classified_success = [sample for sample in classified if sample["outcome"] == "succeeded"]
        row = summarize_values(
            [sample["latency_ms"] for sample in classified_success],
            attempted_count=len(classified),
        )
        by_classification.append(
            {
                "classification": name,
                **{f"{key}_ms" if key in {"mean", "median", "minimum", "maximum", "p50", "p95"} else key: value for key, value in row.items()},
            }
        )
    input_hashes = {"config": config_sha256, "queries": query_sha256}
    if cold_evidence_sha256 is not None:
        input_hashes["cold_evidence"] = cold_evidence_sha256
    return {
        "metadata": build_metadata(
            config,
            measurement_type="search_latency",
            git_commit=git_commit,
            started_at=started_at,
            finished_at=finished_at,
            input_sha256=input_hashes,
            repetitions={
                "warmup": validated["warmup_repetitions"],
                "measured": validated["measured_repetitions"],
                "query_count": len(queries),
            },
        ),
        "endpoint": validated["endpoint"],
        "configured_classification": validated["run_classification"],
        "cold_evidence": evidence,
        "summary": overall,
        "summary_by_classification": by_classification,
        "samples": samples,
    }


def input_hashes(config_path: str | Path, query_path: str | Path) -> tuple[str, str]:
    return sha256_file(config_path), sha256_file(query_path)
