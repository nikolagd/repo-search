from typing import Any

import httpx
from fastapi import HTTPException, Request, Response

from microservices.common.security import internal_headers


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
        upstream = await client.request(
            request.method,
            target,
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
