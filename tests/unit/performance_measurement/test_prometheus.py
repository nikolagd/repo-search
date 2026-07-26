from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from performance_measurement.common import MeasurementError
from performance_measurement.prometheus import collect_resources


pytestmark = pytest.mark.unit


def _config(metrics):
    return {
        "deployment_label": "compose",
        "expected_models": {
            "embedding_model": "embedding",
            "embedding_model_revision": "revision",
            "embedding_template_version": "template",
            "llm_model": "llm",
        },
        "runtime_identity": {},
        "prometheus": {
            "base_url": "https://prometheus.example.test",
            "timeout_seconds": 5,
            "metrics": metrics,
        },
    }


def _clock():
    values = iter(
        [
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        ]
    )
    return lambda: next(values)


def test_prometheus_vector_range_parsing_and_unavailable_metrics(runner_git_commit, verified_runtime_identity) -> None:
    metrics = [
        {"name": "cpu", "metric_type": "cpu", "unit": "cores", "query_kind": "query", "query": "cpu_query"},
        {
            "name": "ram",
            "metric_type": "ram",
            "unit": "bytes",
            "query_kind": "query_range",
            "query": "ram_query",
            "start": 1,
            "end": 2,
            "step": "15s",
        },
        {"name": "gpu", "metric_type": "gpu_utilization", "unit": "percent", "query_kind": "query", "query": "gpu_query"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        if query == "cpu_query":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {"resultType": "vector", "result": [{"metric": {"pod": "search", "__name__": "cpu"}, "value": [1, "2.5"]}]},
                },
            )
        if query == "ram_query":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {"resultType": "matrix", "result": [{"metric": {"pod": "search"}, "values": [[1, "10"], [2, "20"]]}]},
                },
            )
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}})

    report = collect_resources(
        _config(metrics),
        api_token=None,
        runner_git_commit=runner_git_commit,
        runtime_identity=verified_runtime_identity,
        config_sha256="a" * 64,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=_clock(),
    )
    assert len(report["samples"]) == 3
    assert [row["name"] for row in report["metric_definitions"]] == ["cpu", "ram", "gpu"]
    assert report["metric_definitions"][1]["step"] == "15s"
    assert report["samples"][0]["labels"] == {"pod": "search"}
    ram = next(row for row in report["metric_summaries"] if row["name"] == "ram")
    assert ram["sample_count"] == 2
    assert ram["accepted_labels"] == {"pod": "search"}
    assert ram["series_count"] == 1
    assert ram["mean"] == 15.0
    assert ram["p95"] == 20.0
    gpu = next(row for row in report["metric_summaries"] if row["name"] == "gpu")
    assert gpu["availability"] == "unavailable"
    assert gpu["unavailable_reason"] == "no_samples"
    assert gpu["accepted_labels"] is None
    assert gpu["series_count"] == 0
    assert gpu["mean"] is None
    assert gpu["minimum"] is None


def test_http_failure_is_unavailable_not_zero(runner_git_commit, verified_runtime_identity) -> None:
    metric = {"name": "gpu", "metric_type": "gpu_framebuffer", "unit": "bytes", "query_kind": "query", "query": "gpu"}
    report = collect_resources(
        _config([metric]),
        api_token=None,
        runner_git_commit=runner_git_commit,
        runtime_identity=verified_runtime_identity,
        config_sha256="a" * 64,
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(503))),
        clock=_clock(),
    )
    assert report["samples"] == []
    assert report["metric_summaries"][0]["availability"] == "unavailable"
    assert report["metric_summaries"][0]["mean"] is None
    assert report["metric_summaries"][0]["series_count"] is None


def test_non_finite_prometheus_sample_is_rejected(runner_git_commit, verified_runtime_identity) -> None:
    metric = {"name": "cpu", "metric_type": "cpu", "unit": "cores", "query_kind": "query", "query": "cpu"}
    response = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "NaN"]}]},
    }
    with pytest.raises(MeasurementError, match="non-finite"):
        collect_resources(
            _config([metric]),
            api_token=None,
            runner_git_commit=runner_git_commit,
            runtime_identity=verified_runtime_identity,
            config_sha256="a" * 64,
            client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=response))),
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_multiple_prometheus_series_are_rejected_with_narrowing_instruction(
    runner_git_commit,
    verified_runtime_identity,
) -> None:
    metric = {"name": "cpu", "metric_type": "cpu", "unit": "cores", "query_kind": "query", "query": "cpu"}
    response = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"pod": "search-1"}, "value": [1, "1"]},
                {"metric": {"pod": "search-2"}, "value": [1, "2"]},
            ],
        },
    }
    with pytest.raises(MeasurementError, match="aggregate or narrow"):
        collect_resources(
            _config([metric]),
            api_token=None,
            runner_git_commit=runner_git_commit,
            runtime_identity=verified_runtime_identity,
            config_sha256="a" * 64,
            client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=response))),
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
