from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest, start_http_server

_TRACING_READY = False
_FASTAPI_INSTRUMENTED_APPS: set[int] = set()

HTTP_REQUESTS_TOTAL = Counter(
    "repo_search_http_requests_total",
    "Total inbound HTTP requests.",
    ["service", "method", "route", "status_code"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "repo_search_http_request_duration_seconds",
    "Inbound HTTP request latency in seconds.",
    ["service", "method", "route", "status_code"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "repo_search_http_requests_in_progress",
    "Inbound HTTP requests currently in progress.",
    ["service", "method"],
)
OUTBOUND_HTTP_REQUESTS_TOTAL = Counter(
    "repo_search_outbound_http_requests_total",
    "Total outbound HTTP requests to dependencies.",
    ["service", "upstream_service", "method", "status_code"],
)
OUTBOUND_HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "repo_search_outbound_http_request_duration_seconds",
    "Outbound HTTP request latency in seconds.",
    ["service", "upstream_service", "method", "status_code"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
SEARCH_REQUEST_DURATION_SECONDS = Histogram(
    "repo_search_search_request_duration_seconds",
    "Search request execution latency in seconds.",
    ["service"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
QUERY_PARSE_DURATION_SECONDS = Histogram(
    "repo_search_query_parse_duration_seconds",
    "Query parsing latency in seconds.",
    ["service", "parser"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
EMBEDDING_DURATION_SECONDS = Histogram(
    "repo_search_embedding_duration_seconds",
    "Embedding generation latency in seconds.",
    ["service", "kind"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
JOB_EVENTS_TOTAL = Counter(
    "repo_search_job_events_total",
    "Job lifecycle events.",
    ["service", "job_type", "status"],
)
JOB_DURATION_SECONDS = Histogram(
    "repo_search_job_duration_seconds",
    "Background job execution latency in seconds.",
    ["service", "job_type", "status"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)
JOB_QUEUE_DEPTH = Gauge(
    "repo_search_job_queue_depth",
    "Queued background jobs by type.",
    ["service", "job_type"],
)
JOBS_BY_STATUS = Gauge(
    "repo_search_jobs_by_status",
    "Background jobs grouped by type and status.",
    ["service", "job_type", "status"],
)
JOB_OLDEST_QUEUED_AGE_SECONDS = Gauge(
    "repo_search_job_oldest_queued_age_seconds",
    "Age in seconds of the oldest queued background job by type.",
    ["service", "job_type"],
)
JOB_OLDEST_RUNNING_AGE_SECONDS = Gauge(
    "repo_search_job_oldest_running_age_seconds",
    "Age in seconds of the oldest running background job by type.",
    ["service", "job_type"],
)


def _env_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _setup_tracing(service_name: str, app: FastAPI | None = None) -> None:
    if not _env_enabled("OTEL_TRACING_ENABLED"):
        return

    global _TRACING_READY

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        print(f"OpenTelemetry tracing is enabled but could not be initialized: {exc}", file=sys.stderr, flush=True)
        return

    if not _TRACING_READY:
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": os.getenv("OTEL_SERVICE_NAMESPACE", "repo-search"),
                "deployment.environment": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "local"),
            }
        )
        provider = TracerProvider(resource=resource)
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        trace.set_tracer_provider(provider)

        HTTPXClientInstrumentor().instrument()
        RequestsInstrumentor().instrument()
        Psycopg2Instrumentor().instrument()
        _TRACING_READY = True

    if app is not None and id(app) not in _FASTAPI_INSTRUMENTED_APPS:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics")
        _FASTAPI_INSTRUMENTED_APPS.add(id(app))


def setup_observability(app: FastAPI, service_name: str) -> None:
    app.state.service_name = service_name
    _setup_tracing(service_name, app)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        status_code = "500"
        start = time.perf_counter()
        HTTP_REQUESTS_IN_PROGRESS.labels(service_name, method).inc()

        try:
            response = await call_next(request)
            status_code = str(response.status_code)
            return response
        finally:
            duration = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            HTTP_REQUESTS_IN_PROGRESS.labels(service_name, method).dec()
            HTTP_REQUESTS_TOTAL.labels(service_name, method, route_path, status_code).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(service_name, method, route_path, status_code).observe(duration)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        collect_metrics = getattr(app.state, "collect_metrics", None)
        if collect_metrics is not None:
            try:
                collect_metrics()
            except Exception:
                pass
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_outbound_http_request(
    service_name: str,
    upstream_service: str,
    method: str,
    status_code: str,
    duration_seconds: float,
) -> None:
    OUTBOUND_HTTP_REQUESTS_TOTAL.labels(service_name, upstream_service, method.upper(), status_code).inc()
    OUTBOUND_HTTP_REQUEST_DURATION_SECONDS.labels(
        service_name,
        upstream_service,
        method.upper(),
        status_code,
    ).observe(duration_seconds)


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    if not _TRACING_READY:
        yield None
        return

    from opentelemetry import trace

    with trace.get_tracer("repo-search").start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        yield span


@contextmanager
def observe_search_request(service_name: str) -> Iterator[Any]:
    with trace_span("search.request", {"repo_search.service": service_name}) as span:
        with SEARCH_REQUEST_DURATION_SECONDS.labels(service_name).time():
            yield span


@contextmanager
def observe_query_parse(service_name: str, parser: str) -> Iterator[Any]:
    with trace_span("query.parse", {"repo_search.service": service_name, "repo_search.parser": parser}) as span:
        with QUERY_PARSE_DURATION_SECONDS.labels(service_name, parser).time():
            yield span


@contextmanager
def observe_embedding(service_name: str, kind: str) -> Iterator[Any]:
    with trace_span("embedding.generate", {"repo_search.service": service_name, "repo_search.embedding.kind": kind}) as span:
        with EMBEDDING_DURATION_SECONDS.labels(service_name, kind).time():
            yield span


@contextmanager
def observe_job(service_name: str, job_type: str) -> Iterator[None]:
    start = time.perf_counter()
    status = "failed"
    try:
        with trace_span("job.execute", {"repo_search.service": service_name, "repo_search.job.type": job_type}):
            yield
        status = "succeeded"
    finally:
        duration = time.perf_counter() - start
        JOB_EVENTS_TOTAL.labels(service_name, job_type, status).inc()
        JOB_DURATION_SECONDS.labels(service_name, job_type, status).observe(duration)


def record_job_event(service_name: str, job_type: str, status: str) -> None:
    JOB_EVENTS_TOTAL.labels(service_name, job_type, status).inc()


def record_job_duration(service_name: str, job_type: str, status: str, duration_seconds: float) -> None:
    JOB_DURATION_SECONDS.labels(service_name, job_type, status).observe(duration_seconds)


def set_job_queue_depth(service_name: str, job_type: str, depth: int) -> None:
    JOB_QUEUE_DEPTH.labels(service_name, job_type).set(depth)


def set_jobs_by_status(service_name: str, job_type: str, status: str, count: int) -> None:
    JOBS_BY_STATUS.labels(service_name, job_type, status).set(count)


def set_job_oldest_queued_age(service_name: str, job_type: str, age_seconds: float) -> None:
    JOB_OLDEST_QUEUED_AGE_SECONDS.labels(service_name, job_type).set(max(age_seconds, 0))


def set_job_oldest_running_age(service_name: str, job_type: str, age_seconds: float) -> None:
    JOB_OLDEST_RUNNING_AGE_SECONDS.labels(service_name, job_type).set(max(age_seconds, 0))


def start_worker_observability(service_name: str) -> None:
    _setup_tracing(service_name)
    port = int(os.getenv("METRICS_PORT", "9100"))
    start_http_server(port)
    record_job_event(service_name, "worker", "started")
