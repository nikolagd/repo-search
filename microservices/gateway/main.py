from __future__ import annotations

import asyncio
import hmac
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from microservices.common.config import service_url
from microservices.common.health import (
    HEALTH_OK,
    HEALTH_UNAVAILABLE,
    aggregate_status,
    build_health_response,
    build_liveness_response,
    build_readiness_response,
)
from microservices.common.http import observed_async_request, proxy_request, raise_for_service
from microservices.common.observability import setup_observability
from microservices.common.schemas import HealthResponse, LivenessResponse, ReadinessResponse, StatsResponse
from microservices.common.security import internal_headers, require_api_token

app = FastAPI(title="Repo Search API Gateway", version="0.1.0")
setup_observability(app, "gateway")

AUTH_SERVICE_URL = service_url("AUTH_SERVICE_URL", "http://auth-service:8000")
CATALOG_SERVICE_URL = service_url("CATALOG_SERVICE_URL", "http://catalog-service:8000")
SEARCH_SERVICE_URL = service_url("SEARCH_SERVICE_URL", "http://search-service:8000")
QUERY_SERVICE_URL = service_url("QUERY_SERVICE_URL", "http://query-service:8000")
EMBEDDING_SERVICE_URL = service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
JOB_SERVICE_URL = service_url("JOB_SERVICE_URL", "http://job-service:8000")
PROMETHEUS_URL = service_url("PROMETHEUS_URL", "http://prometheus:9090")
CSRF_COOKIE_NAME = "repo_search_admin_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
MODEL_OBSERVABILITY_WINDOWS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "15d": 15 * 24 * 60 * 60,
}
GATEWAY_REQUIRED_SERVICES = {
    "auth-service": AUTH_SERVICE_URL,
    "catalog-service": CATALOG_SERVICE_URL,
    "search-service": SEARCH_SERVICE_URL,
    "job-service": JOB_SERVICE_URL,
}


async def require_admin_request(request: Request) -> None:
    if request.method.upper() not in SAFE_METHODS:
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        csrf_header = request.headers.get(CSRF_HEADER_NAME)
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    headers = dict(internal_headers())
    if cookie := request.headers.get("cookie"):
        headers["cookie"] = cookie

    async with httpx.AsyncClient(timeout=30) as client:
        response = await observed_async_request(
            client,
            "GET",
            f"{AUTH_SERVICE_URL}/auth/me",
            service_name="gateway",
            upstream_service="auth-service",
            headers=headers,
        )
        raise_for_service(response, "Auth service")


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _prometheus_vector_value(result: list[dict[str, Any]]) -> float:
    if not result:
        return 0.0
    return _float_value(result[0].get("value", [None, 0])[1])


def _prometheus_series(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    series = []
    for item in result:
        metric = dict(item.get("metric", {}))
        metric.pop("__name__", None)
        series.append(
            {
                "metric": metric,
                "values": [
                    {"timestamp": _float_value(timestamp), "value": _float_value(value)}
                    for timestamp, value in item.get("values", [])
                ],
            }
        )
    return series


async def prometheus_query(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    response = await observed_async_request(
        client,
        "GET",
        f"{PROMETHEUS_URL}/api/v1/query",
        service_name="gateway",
        upstream_service="prometheus",
        params={"query": query},
    )
    raise_for_service(response, "Prometheus")
    payload = response.json()
    if payload.get("status") != "success":
        raise HTTPException(status_code=502, detail="Prometheus query failed.")
    return payload.get("data", {}).get("result", [])


async def prometheus_query_range(client: httpx.AsyncClient, query: str, window_seconds: int) -> list[dict[str, Any]]:
    end = time.time()
    start = end - window_seconds
    step = max(15, min(300, window_seconds // 60))
    response = await observed_async_request(
        client,
        "GET",
        f"{PROMETHEUS_URL}/api/v1/query_range",
        service_name="gateway",
        upstream_service="prometheus",
        params={
            "query": query,
            "start": f"{start:.0f}",
            "end": f"{end:.0f}",
            "step": str(step),
        },
    )
    raise_for_service(response, "Prometheus")
    payload = response.json()
    if payload.get("status") != "success":
        raise HTTPException(status_code=502, detail="Prometheus range query failed.")
    return payload.get("data", {}).get("result", [])


async def gateway_readiness_dependencies() -> dict[str, str]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            results = await asyncio.gather(
                *(
                    observed_async_request(
                        client,
                        "GET",
                        f"{base_url}/ready",
                        service_name="gateway",
                        upstream_service=service_name,
                        headers=internal_headers(),
                    )
                    for service_name, base_url in GATEWAY_REQUIRED_SERVICES.items()
                ),
                return_exceptions=True,
            )
    except Exception:
        return {service_name: HEALTH_UNAVAILABLE for service_name in GATEWAY_REQUIRED_SERVICES}

    return {
        service_name: (
            HEALTH_UNAVAILABLE
            if isinstance(result, BaseException)
            else HEALTH_OK
            if result.status_code < 400
            else HEALTH_UNAVAILABLE
        )
        for service_name, result in zip(GATEWAY_REQUIRED_SERVICES, results, strict=True)
    }


@app.get("/api/live", response_model=LivenessResponse)
def api_live() -> LivenessResponse:
    return build_liveness_response()


@app.get("/api/ready", response_model=ReadinessResponse, dependencies=[Depends(require_api_token)])
async def api_ready(response: Response) -> ReadinessResponse:
    return build_readiness_response(response, await gateway_readiness_dependencies())


@app.get("/api/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
async def health() -> HealthResponse:
    dependencies = await gateway_readiness_dependencies()
    return build_health_response(aggregate_status(dependencies), dependencies)


@app.get("/api/repositories", dependencies=[Depends(require_api_token)])
async def repositories(request: Request) -> Response:
    return await proxy_request(request, CATALOG_SERVICE_URL, "/repositories")


@app.get("/api/stats", response_model=StatsResponse, dependencies=[Depends(require_api_token)])
async def stats() -> StatsResponse:
    async with httpx.AsyncClient(timeout=30) as client:
        catalog = await observed_async_request(
            client,
            "GET",
            f"{CATALOG_SERVICE_URL}/stats",
            service_name="gateway",
            upstream_service="catalog-service",
            headers=internal_headers(),
        )
        raise_for_service(catalog, "Catalog service")
        embedding_status = await observed_async_request(
            client,
            "GET",
            f"{SEARCH_SERVICE_URL}/embeddings/status",
            service_name="gateway",
            upstream_service="search-service",
            headers=internal_headers(),
        )
        raise_for_service(embedding_status, "Search service")

    payload = catalog.json()
    payload["publications_with_embeddings"] = embedding_status.json()["publications_with_embeddings"]
    return StatsResponse(**payload)


@app.api_route("/api/search", methods=["POST"], dependencies=[Depends(require_api_token)])
async def search(request: Request) -> Response:
    return await proxy_request(request, SEARCH_SERVICE_URL, "/search")


@app.api_route("/api/auth/{path:path}", methods=["GET", "POST"], dependencies=[Depends(require_api_token)])
async def auth_proxy(path: str, request: Request) -> Response:
    return await proxy_request(request, AUTH_SERVICE_URL, f"/auth/{path}")


@app.get("/api/admin/repositories", dependencies=[Depends(require_api_token)])
async def admin_repositories(request: Request) -> list[dict]:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        repos_response = await observed_async_request(
            client,
            "GET",
            f"{CATALOG_SERVICE_URL}/repositories",
            service_name="gateway",
            upstream_service="catalog-service",
            headers=internal_headers(),
        )
        raise_for_service(repos_response, "Catalog service")
        jobs_response = await observed_async_request(
            client,
            "GET",
            f"{JOB_SERVICE_URL}/jobs",
            service_name="gateway",
            upstream_service="job-service",
            params={"job_type": "repository_harvest"},
            headers=internal_headers(),
        )
        raise_for_service(jobs_response, "Job service")

    jobs_by_repo = {}
    for job in jobs_response.json():
        repository_id = job.get("repository_id")
        if repository_id is not None and repository_id not in jobs_by_repo:
            jobs_by_repo[repository_id] = job

    repositories_payload = repos_response.json()
    for repository in repositories_payload:
        repository["harvest_job"] = jobs_by_repo.get(repository["id"])

    return repositories_payload


@app.api_route(
    "/api/admin/repositories",
    methods=["POST"],
    dependencies=[Depends(require_api_token)],
)
async def admin_create_repository(request: Request) -> Response:
    await require_admin_request(request)
    return await proxy_request(request, CATALOG_SERVICE_URL, "/repositories")


@app.api_route(
    "/api/admin/repositories/{repo_id}",
    methods=["PUT"],
    dependencies=[Depends(require_api_token)],
)
async def admin_update_repository(repo_id: int, request: Request) -> Response:
    await require_admin_request(request)
    return await proxy_request(request, CATALOG_SERVICE_URL, f"/repositories/{repo_id}")


@app.post("/api/admin/repositories/{repo_id}/harvest", dependencies=[Depends(require_api_token)])
async def admin_harvest_repository(repo_id: int, request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await observed_async_request(
            client,
            "POST",
            f"{JOB_SERVICE_URL}/jobs/harvest",
            service_name="gateway",
            upstream_service="job-service",
            json={"repository_id": repo_id},
            headers=internal_headers(),
        )
        raise_for_service(response, "Job service")
        return response.json()


@app.get("/api/admin/embeddings", dependencies=[Depends(require_api_token)])
async def admin_embeddings(request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        catalog_response = await observed_async_request(
            client,
            "GET",
            f"{CATALOG_SERVICE_URL}/stats",
            service_name="gateway",
            upstream_service="catalog-service",
            headers=internal_headers(),
        )
        raise_for_service(catalog_response, "Catalog service")
        status_response = await observed_async_request(
            client,
            "GET",
            f"{SEARCH_SERVICE_URL}/embeddings/status",
            service_name="gateway",
            upstream_service="search-service",
            headers=internal_headers(),
        )
        raise_for_service(status_response, "Search service")
        jobs_response = await observed_async_request(
            client,
            "GET",
            f"{JOB_SERVICE_URL}/jobs",
            service_name="gateway",
            upstream_service="job-service",
            params={"job_type": "embedding_backfill"},
            headers=internal_headers(),
        )
        raise_for_service(jobs_response, "Job service")

    jobs = jobs_response.json()
    catalog_publications = catalog_response.json()["publications"]
    search_status = status_response.json()
    return build_admin_embedding_status(catalog_publications, search_status, jobs)


def build_admin_embedding_status(
    catalog_publications: int,
    search_status: dict,
    jobs: list[dict],
) -> dict:
    missing_embeddings = search_status.get(
        "missing_embeddings",
        max(catalog_publications - search_status["publications_with_embeddings"], 0),
    )
    return {
        "current_embeddings": search_status.get("current_embeddings", search_status["publications_with_embeddings"]),
        "missing_embeddings": missing_embeddings,
        "stale_embeddings": search_status.get("stale_embeddings", 0),
        "embedding_job": jobs[0] if jobs else None,
    }


@app.post("/api/admin/embeddings/backfill", dependencies=[Depends(require_api_token)])
async def admin_embedding_backfill(request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await observed_async_request(
            client,
            "POST",
            f"{JOB_SERVICE_URL}/jobs/embedding-backfill",
            service_name="gateway",
            upstream_service="job-service",
            headers=internal_headers(),
        )
        raise_for_service(response, "Job service")
        return response.json()


@app.get("/api/admin/model-observability", dependencies=[Depends(require_api_token)])
async def admin_model_observability(request: Request, window: str = "1h") -> dict:
    await require_admin_request(request)

    window_seconds = MODEL_OBSERVABILITY_WINDOWS.get(window, MODEL_OBSERVABILITY_WINDOWS["1h"])
    window = window if window in MODEL_OBSERVABILITY_WINDOWS else "1h"
    rate_window = window

    async with httpx.AsyncClient(timeout=30) as client:
        search_status_response, query_status_response, embedding_status_response = await asyncio.gather(
            observed_async_request(
                client,
                "GET",
                f"{SEARCH_SERVICE_URL}/model-observability/status",
                service_name="gateway",
                upstream_service="search-service",
                headers=internal_headers(),
            ),
            observed_async_request(
                client,
                "GET",
                f"{QUERY_SERVICE_URL}/model/status",
                service_name="gateway",
                upstream_service="query-service",
                headers=internal_headers(),
            ),
            observed_async_request(
                client,
                "GET",
                f"{EMBEDDING_SERVICE_URL}/model/status",
                service_name="gateway",
                upstream_service="embedding-service",
                headers=internal_headers(),
            ),
        )
        raise_for_service(search_status_response, "Search service")
        raise_for_service(query_status_response, "Query service")
        raise_for_service(embedding_status_response, "Embedding service")

        queries = {
            "searches": f"sum(increase(repo_search_retrieval_searches_total[{rate_window}]))",
            "zero_results": f"sum(increase(repo_search_retrieval_zero_results_total[{rate_window}]))",
            "parser_events": (
                "sum(increase(repo_search_retrieval_parser_events_total"
                f'{{service="search-service"}}[{rate_window}]))'
            ),
            "fallback_parser_events": (
                "sum(increase(repo_search_retrieval_parser_events_total"
                f'{{service="search-service",parser_mode=~"fallback|fallback_service_error"}}[{rate_window}]))'
            ),
            "p95_total_latency": (
                "histogram_quantile(0.95, "
                f"sum by (le) (rate(repo_search_retrieval_stage_duration_seconds_bucket"
                f'{{stage="total"}}[{rate_window}])))'
            ),
            "avg_result_count": (
                f"sum(increase(repo_search_retrieval_final_results_sum[{rate_window}])) "
                f"/ clamp_min(sum(increase(repo_search_retrieval_final_results_count[{rate_window}])), 1)"
            ),
            "avg_candidates": (
                f"sum(increase(repo_search_retrieval_vector_candidates_sum[{rate_window}])) "
                f"/ clamp_min(sum(increase(repo_search_retrieval_vector_candidates_count[{rate_window}])), 1)"
            ),
            "avg_embedding_queries": (
                f"sum(increase(repo_search_retrieval_embedding_query_count_sum[{rate_window}])) "
                f"/ clamp_min(sum(increase(repo_search_retrieval_embedding_query_count_count[{rate_window}])), 1)"
            ),
            "avg_top_score": (
                f"sum(increase(repo_search_retrieval_top_score_sum[{rate_window}])) "
                f"/ clamp_min(sum(increase(repo_search_retrieval_top_score_count[{rate_window}])), 1)"
            ),
            "avg_score": (
                f"sum(increase(repo_search_retrieval_average_score_sum[{rate_window}])) "
                f"/ clamp_min(sum(increase(repo_search_retrieval_average_score_count[{rate_window}])), 1)"
            ),
            "coverage": "avg(repo_search_retrieval_embedding_coverage_ratio)",
            "stage_latency": (
                "histogram_quantile(0.95, "
                f"sum by (le, stage) (rate(repo_search_retrieval_stage_duration_seconds_bucket[{rate_window}])))"
            ),
            "parser_modes": f'sum by (parser_mode) (increase(repo_search_retrieval_parser_events_total{{service="search-service"}}[{rate_window}]))',
            "search_rate": "sum(rate(repo_search_retrieval_searches_total[5m]))",
        }

        results = await asyncio.gather(
            *[prometheus_query(client, query) for key, query in queries.items() if key != "search_rate"],
            prometheus_query_range(client, queries["search_rate"], window_seconds),
        )

    query_keys = [key for key in queries if key != "search_rate"]
    prometheus = dict(zip(query_keys, results[:-1]))
    search_rate = results[-1]
    total_searches = _prometheus_vector_value(prometheus["searches"])
    zero_results = _prometheus_vector_value(prometheus["zero_results"])
    parser_events = _prometheus_vector_value(prometheus["parser_events"])
    fallback_parser_events = _prometheus_vector_value(prometheus["fallback_parser_events"])
    zero_result_rate = zero_results / total_searches if total_searches else 0
    fallback_rate = fallback_parser_events / parser_events if parser_events else 0

    stage_latency = [
        {
            "stage": item.get("metric", {}).get("stage", "unknown"),
            "p95_seconds": _float_value(item.get("value", [None, 0])[1]),
        }
        for item in prometheus["stage_latency"]
    ]
    stage_latency.sort(key=lambda item: item["stage"])

    parser_modes = [
        {
            "parser_mode": item.get("metric", {}).get("parser_mode", "unknown"),
            "count": _float_value(item.get("value", [None, 0])[1]),
        }
        for item in prometheus["parser_modes"]
        if _float_value(item.get("value", [None, 0])[1]) > 0
    ]
    parser_modes.sort(key=lambda item: item["parser_mode"])

    search_status = search_status_response.json()
    return {
        "window": window,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_config": {
            **query_status_response.json(),
            **embedding_status_response.json(),
            "ranking_config": search_status.get("ranking_config", {}),
        },
        "index": search_status.get("index", {}),
        "cards": [
            {"label": "Searches", "value": total_searches, "unit": "count"},
            {"label": "Zero-result rate", "value": zero_result_rate, "unit": "percent"},
            {"label": "Fallback parser rate", "value": fallback_rate, "unit": "percent"},
            {"label": "p95 search latency", "value": _prometheus_vector_value(prometheus["p95_total_latency"]), "unit": "seconds"},
            {"label": "Embedding coverage", "value": _prometheus_vector_value(prometheus["coverage"]), "unit": "percent"},
        ],
        "retrieval_output": {
            "avg_result_count": _prometheus_vector_value(prometheus["avg_result_count"]),
            "avg_candidates": _prometheus_vector_value(prometheus["avg_candidates"]),
            "avg_embedding_queries": _prometheus_vector_value(prometheus["avg_embedding_queries"]),
            "avg_top_score": _prometheus_vector_value(prometheus["avg_top_score"]),
            "avg_score": _prometheus_vector_value(prometheus["avg_score"]),
        },
        "stage_latency": stage_latency,
        "parser_modes": parser_modes,
        "search_rate": _prometheus_series(search_rate),
    }


@app.post("/api/admin/jobs/{job_id}/acknowledge", dependencies=[Depends(require_api_token)])
async def acknowledge_job(job_id: int, request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await observed_async_request(
            client,
            "POST",
            f"{JOB_SERVICE_URL}/jobs/{job_id}/acknowledge",
            service_name="gateway",
            upstream_service="job-service",
            headers=internal_headers(),
        )
        raise_for_service(response, "Job service")
        return response.json()
