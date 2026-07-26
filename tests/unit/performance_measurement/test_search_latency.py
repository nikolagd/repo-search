from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from performance_measurement.common import MeasurementError
from performance_measurement.search_latency import SearchQuery, run_search_measurement


pytestmark = pytest.mark.unit


def _config(label: str = "compose", **search_overrides):
    search = {
        "endpoint": "https://search.example.test/api/search",
        "api_token_env": "PERFORMANCE_API_TOKEN",
        "warmup_repetitions": 1,
        "measured_repetitions": 2,
        "timeout_seconds": 5,
        "run_classification": "warm",
        **search_overrides,
    }
    return {
        "deployment_label": label,
        "models": {
            "embedding_model": "embedding",
            "embedding_model_revision": "revision",
            "embedding_template_version": "template",
            "llm_model": "llm",
        },
        "search": search,
    }


def _clock(*values: datetime):
    iterator = iter(values)
    return lambda: next(iterator)


def test_warmups_are_preserved_but_excluded_from_statistics() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"results": [{"id": 1}], "plan": {"parser_mode": "llm"}})
    )
    counters = iter([0, 9_000_000, 10_000_000, 11_000_000, 12_000_000, 15_000_000])
    report = run_search_measurement(
        _config(),
        [SearchQuery("q1", "query", 10)],
        api_token="sentinel-token",
        git_commit="abc123",
        config_sha256="a" * 64,
        query_sha256="b" * 64,
        client=httpx.Client(transport=transport),
        perf_counter_ns=lambda: next(counters),
        clock=_clock(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        ),
    )
    assert [sample["latency_ms"] for sample in report["samples"]] == [9.0, 1.0, 3.0]
    assert report["samples"][0]["phase"] == "warmup"
    assert report["summary"]["sample_count"] == 2
    assert report["summary"]["mean_ms"] == 2.0
    assert report["summary"]["p95_ms"] == 3.0


def test_cold_run_requires_ordered_external_restart_and_readiness_evidence() -> None:
    config = _config(warmup_repetitions=0, measured_repetitions=1, run_classification="cold")
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    kwargs = {
        "api_token": "token",
        "git_commit": "abc",
        "config_sha256": "a" * 64,
        "query_sha256": "b" * 64,
        "client": httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"results": []}))),
        "perf_counter_ns": iter([0, 1_000_000]).__next__,
        "clock": _clock(start, datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)),
    }
    with pytest.raises(MeasurementError, match="restart/readiness evidence"):
        run_search_measurement(config, [SearchQuery("q", "query", 1)], **kwargs)

    evidence = {
        "deployment_label": "compose",
        "source": "external restart and readiness log",
        "restart_completed_at_utc": "2026-01-01T11:58:00Z",
        "readiness_confirmed_at_utc": "2026-01-01T11:59:00Z",
    }
    report = run_search_measurement(
        config,
        [SearchQuery("q", "query", 1)],
        api_token="token",
        git_commit="abc",
        config_sha256="a" * 64,
        query_sha256="b" * 64,
        cold_evidence=evidence,
        cold_evidence_sha256="c" * 64,
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"results": []}))),
        perf_counter_ns=iter([0, 1_000_000]).__next__,
        clock=_clock(start, datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)),
    )
    assert report["samples"][0]["classification"] == "cold"
    assert report["metadata"]["input_sha256"]["cold_evidence"] == "c" * 64


def test_failed_samples_are_explicit_and_do_not_enter_statistics() -> None:
    responses = iter(
        [
            httpx.Response(503, json={"detail": "unavailable"}),
            httpx.Response(200, json={"results": [], "plan": {"used_fallback": True}}),
        ]
    )
    report = run_search_measurement(
        _config(warmup_repetitions=0, measured_repetitions=2),
        [SearchQuery("q", "query", 10)],
        api_token="token",
        git_commit="abc",
        config_sha256="a" * 64,
        query_sha256="b" * 64,
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: next(responses))),
        perf_counter_ns=iter([0, 2_000_000, 3_000_000, 7_000_000]).__next__,
        clock=_clock(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        ),
    )
    assert report["samples"][0]["outcome"] == "http_error"
    assert report["samples"][0]["status"] == "http_error"
    assert report["samples"][0]["http_status"] == 503
    assert report["samples"][1]["parser_mode"] == "fallback"
    assert report["summary"]["attempted_sample_count"] == 2
    assert report["summary"]["sample_count"] == 1
    assert report["summary"]["failed_sample_count"] == 1
    assert report["summary"]["mean_ms"] == 4.0


def test_invalid_performance_counter_sample_is_rejected() -> None:
    with pytest.raises(MeasurementError, match="performance counter"):
        run_search_measurement(
            _config(warmup_repetitions=0, measured_repetitions=1),
            [SearchQuery("q", "query", 10)],
            api_token="token",
            git_commit="abc",
            config_sha256="a" * 64,
            query_sha256="b" * 64,
            client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"results": []}))),
            perf_counter_ns=iter([10, 9]).__next__,
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_cold_evidence_rejects_readiness_before_restart() -> None:
    with pytest.raises(MeasurementError, match="readiness precedes"):
        run_search_measurement(
            _config(warmup_repetitions=0, measured_repetitions=1, run_classification="cold"),
            [SearchQuery("q", "query", 1)],
            api_token="token",
            git_commit="abc",
            config_sha256="a" * 64,
            query_sha256="b" * 64,
            cold_evidence={
                "deployment_label": "compose",
                "source": "external log",
                "restart_completed_at_utc": "2026-01-01T11:59:00Z",
                "readiness_confirmed_at_utc": "2026-01-01T11:58:00Z",
            },
            client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"results": []}))),
            perf_counter_ns=iter([0, 1]).__next__,
            clock=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )


def test_deployment_label_does_not_change_query_order_or_payloads() -> None:
    observed: list[tuple[str, dict]] = []

    def run(label: str):
        def handler(request: httpx.Request) -> httpx.Response:
            observed.append((label, request.read() and __import__("json").loads(request.content)))
            return httpx.Response(200, json={"results": []})

        return run_search_measurement(
            _config(label, warmup_repetitions=0, measured_repetitions=1),
            [SearchQuery("q1", "one", 3), SearchQuery("q2", "two", 4)],
            api_token="token",
            git_commit="abc",
            config_sha256="a" * 64,
            query_sha256="b" * 64,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            perf_counter_ns=iter([0, 1, 2, 3]).__next__,
            clock=_clock(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
            ),
        )

    first = run("compose")
    second = run("kubernetes")
    assert [payload for label, payload in observed if label == "compose"] == [
        payload for label, payload in observed if label == "kubernetes"
    ]
    assert [sample["query_id"] for sample in first["samples"]] == [sample["query_id"] for sample in second["samples"]]
    assert first["metadata"]["deployment_label"] != second["metadata"]["deployment_label"]
