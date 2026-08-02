from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel, Field

from microservices.common.app_logging import emit_app_event
from microservices.common.health import build_health_response, build_liveness_response, build_readiness_response
from microservices.common.observability import (
    observe_query_parse,
    record_retrieval_parser_event,
    set_retrieval_model_info,
    setup_observability,
)
from microservices.common.schemas import HealthResponse, LivenessResponse, ReadinessResponse
from microservices.common.security import require_api_token
from microservices.query_service.llm_parser import LLM_MODEL, LLM_PROVIDER, LLM_TIMEOUT, LLM_URL, LLM_WARMUP_ENABLED, warm_up_llm
from microservices.query_service.query_handler import parse_query

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    warm_up_llm()
    set_retrieval_model_info(
        "query-service",
        "query_parser",
        {
            "llm_provider": LLM_PROVIDER,
            "llm_model": LLM_MODEL,
            "llm_timeout_seconds": LLM_TIMEOUT,
            "llm_warmup_enabled": LLM_WARMUP_ENABLED,
        },
    )
    yield


app = FastAPI(title="Repo Search Query Service", version="0.1.0", lifespan=lifespan)
setup_observability(app, "query-service")


class QueryParseRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


def readiness_dependencies() -> dict[str, str]:
    return {}


@app.get("/live", response_model=LivenessResponse)
def live() -> LivenessResponse:
    return build_liveness_response()


@app.get("/ready", response_model=ReadinessResponse, dependencies=[Depends(require_api_token)])
def ready(response: Response) -> ReadinessResponse:
    return build_readiness_response(response, readiness_dependencies())


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    return build_health_response("not-used", readiness_dependencies())


@app.get("/model/status", dependencies=[Depends(require_api_token)])
def model_status() -> dict:
    return {
        "llm_provider": LLM_PROVIDER,
        "llm_model": LLM_MODEL,
        "llm_url": LLM_URL,
        "llm_timeout_seconds": LLM_TIMEOUT,
        "llm_warmup_enabled": LLM_WARMUP_ENABLED,
    }


@app.post("/query/parse", dependencies=[Depends(require_api_token)])
def parse(request: QueryParseRequest) -> dict:
    with observe_query_parse("query-service", "configured") as span:
        if span is not None:
            span.set_attribute("repo_search.query_length", len(request.query))
        plan = parse_query(request.query)
        parser_mode = plan.get("parser_mode", "fallback" if plan.get("used_fallback") else "llm")
        record_retrieval_parser_event("query-service", parser_mode)
        if parser_mode in {"fallback", "fallback_service_error"}:
            emit_app_event(
                "query.parser_fallback",
                "query-service",
                parser_mode=parser_mode,
                query_length=len(request.query),
                embedding_query_count=len(plan.get("embedding_queries", [])),
                search_mode=plan.get("search_mode", "semantic"),
                author_filter_count=len(plan.get("author_names", [])),
            )
        if span is not None:
            span.set_attribute("repo_search.embedding_query_count", len(plan.get("embedding_queries", [])))
            span.set_attribute("repo_search.search_mode", plan.get("search_mode", "semantic"))
            span.set_attribute("repo_search.author_filter_count", len(plan.get("author_names", [])))
            span.set_attribute("repo_search.used_fallback", bool(plan.get("used_fallback")))
            span.set_attribute("repo_search.parser_mode", parser_mode)
        return plan
