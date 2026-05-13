from __future__ import annotations

import asyncio
import hmac

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from microservices.common.config import service_url
from microservices.common.http import proxy_request, raise_for_service
from microservices.common.schemas import HealthResponse, StatsResponse
from microservices.common.security import internal_headers, require_api_token

app = FastAPI(title="Repo Search API Gateway", version="0.1.0")

AUTH_SERVICE_URL = service_url("AUTH_SERVICE_URL", "http://auth-service:8000")
CATALOG_SERVICE_URL = service_url("CATALOG_SERVICE_URL", "http://catalog-service:8000")
SEARCH_SERVICE_URL = service_url("SEARCH_SERVICE_URL", "http://search-service:8000")
JOB_SERVICE_URL = service_url("JOB_SERVICE_URL", "http://job-service:8000")
CSRF_COOKIE_NAME = "repo_search_admin_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


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
        response = await client.get(f"{AUTH_SERVICE_URL}/auth/me", headers=headers)
        raise_for_service(response, "Auth service")


@app.get("/api/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
async def health() -> HealthResponse:
    async with httpx.AsyncClient(timeout=10) as client:
        responses = await asyncio.gather(
            client.get(f"{CATALOG_SERVICE_URL}/health", headers=internal_headers()),
            client.get(f"{SEARCH_SERVICE_URL}/health", headers=internal_headers()),
            client.get(f"{JOB_SERVICE_URL}/health", headers=internal_headers()),
            client.get(f"{AUTH_SERVICE_URL}/health", headers=internal_headers()),
        )

    database = "ok" if all(response.status_code < 400 for response in responses) else "unavailable"
    return HealthResponse(status="ok", database=database)


@app.get("/api/repositories", dependencies=[Depends(require_api_token)])
async def repositories(request: Request) -> Response:
    return await proxy_request(request, CATALOG_SERVICE_URL, "/repositories")


@app.get("/api/stats", response_model=StatsResponse, dependencies=[Depends(require_api_token)])
async def stats() -> StatsResponse:
    async with httpx.AsyncClient(timeout=30) as client:
        catalog = await client.get(f"{CATALOG_SERVICE_URL}/stats", headers=internal_headers())
        raise_for_service(catalog, "Catalog service")
        embedding_status = await client.get(f"{SEARCH_SERVICE_URL}/embeddings/status", headers=internal_headers())
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
        repos_response = await client.get(f"{CATALOG_SERVICE_URL}/repositories", headers=internal_headers())
        raise_for_service(repos_response, "Catalog service")
        jobs_response = await client.get(
            f"{JOB_SERVICE_URL}/jobs",
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
        response = await client.post(
            f"{JOB_SERVICE_URL}/jobs/harvest",
            json={"repository_id": repo_id},
            headers=internal_headers(),
        )
        raise_for_service(response, "Job service")
        return response.json()


@app.get("/api/admin/embeddings", dependencies=[Depends(require_api_token)])
async def admin_embeddings(request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        catalog_response = await client.get(f"{CATALOG_SERVICE_URL}/stats", headers=internal_headers())
        raise_for_service(catalog_response, "Catalog service")
        status_response = await client.get(f"{SEARCH_SERVICE_URL}/embeddings/status", headers=internal_headers())
        raise_for_service(status_response, "Search service")
        jobs_response = await client.get(
            f"{JOB_SERVICE_URL}/jobs",
            params={"job_type": "embedding_backfill"},
            headers=internal_headers(),
        )
        raise_for_service(jobs_response, "Job service")

    jobs = jobs_response.json()
    catalog_publications = catalog_response.json()["publications"]
    search_status = status_response.json()
    missing_embeddings = max(catalog_publications - search_status["publications_with_embeddings"], 0)
    return {
        "missing_embeddings": missing_embeddings,
        "embedding_job": jobs[0] if jobs else None,
    }


@app.post("/api/admin/embeddings/backfill", dependencies=[Depends(require_api_token)])
async def admin_embedding_backfill(request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{JOB_SERVICE_URL}/jobs/embedding-backfill",
            headers=internal_headers(),
        )
        raise_for_service(response, "Job service")
        return response.json()


@app.post("/api/admin/jobs/{job_id}/acknowledge", dependencies=[Depends(require_api_token)])
async def acknowledge_job(job_id: int, request: Request) -> dict:
    await require_admin_request(request)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{JOB_SERVICE_URL}/jobs/{job_id}/acknowledge",
            headers=internal_headers(),
        )
        raise_for_service(response, "Job service")
        return response.json()
