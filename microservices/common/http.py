import time
from typing import Any
from urllib.parse import urlparse

import httpx
import requests
from fastapi import HTTPException, Request, Response

from microservices.common.observability import record_outbound_http_request
from microservices.common.security import internal_headers


def upstream_name(url: str) -> str:
    parsed = urlparse(url)
    return parsed.hostname or "unknown"


async def observed_async_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service_name: str,
    upstream_service: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    start = time.perf_counter()
    status_code = "exception"
    try:
        response = await client.request(method, url, **kwargs)
        status_code = str(response.status_code)
        return response
    finally:
        record_outbound_http_request(
            service_name=service_name,
            upstream_service=upstream_service or upstream_name(url),
            method=method,
            status_code=status_code,
            duration_seconds=time.perf_counter() - start,
        )


def observed_sync_request(
    method: str,
    url: str,
    *,
    service_name: str,
    upstream_service: str | None = None,
    **kwargs: Any,
) -> requests.Response:
    start = time.perf_counter()
    status_code = "exception"
    try:
        response = requests.request(method, url, **kwargs)
        status_code = str(response.status_code)
        return response
    finally:
        record_outbound_http_request(
            service_name=service_name,
            upstream_service=upstream_service or upstream_name(url),
            method=method,
            status_code=status_code,
            duration_seconds=time.perf_counter() - start,
        )


async def proxy_request(request: Request, base_url: str, path: str) -> Response:
    target = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    headers.update(internal_headers())

    async with httpx.AsyncClient(timeout=120) as client:
        upstream = await observed_async_request(
            client,
            request.method,
            target,
            service_name=getattr(request.app.state, "service_name", "unknown"),
            upstream_service=upstream_name(base_url),
            params=request.query_params,
            content=body,
            headers=headers,
            cookies=request.cookies,
        )

    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )

    for cookie in upstream.headers.get_list("set-cookie"):
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))

    return response


def raise_for_service(response: httpx.Response, service_name: str) -> None:
    if response.status_code < 400:
        return

    try:
        detail: Any = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    raise HTTPException(
        status_code=response.status_code,
        detail=f"{service_name} failed: {detail}",
    )
