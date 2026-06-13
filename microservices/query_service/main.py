from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from microservices.common.app_logging import emit_app_event
from microservices.common.observability import (
    observe_query_parse,
    record_retrieval_parser_event,
    set_retrieval_model_info,
    setup_observability,
)
from microservices.common.schemas import HealthResponse
from microservices.common.security import require_api_token
from microservices.query_service.llm_parser import LLM_MODEL, LLM_PROVIDER, LLM_TIMEOUT, LLM_URL
from microservices.query_service.query_handler import parse_query

app = FastAPI(title="Repo Search Query Service", version="0.1.0")
setup_observability(app, "query-service")


class QueryParseRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


@app.on_event("startup")
def startup() -> None:
    set_retrieval_model_info(
        "query-service",
        "query_parser",
        {
            "llm_provider": LLM_PROVIDER,
            "llm_model": LLM_MODEL,
            "llm_timeout_seconds": LLM_TIMEOUT,
        },
    )


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    return HealthResponse(status="ok", database="not-used")


@app.get("/model/status", dependencies=[Depends(require_api_token)])
def model_status() -> dict:
    return {
        "llm_provider": LLM_PROVIDER,
        "llm_model": LLM_MODEL,
        "llm_url": LLM_URL,
        "llm_timeout_seconds": LLM_TIMEOUT,
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
            )
        if span is not None:
            span.set_attribute("repo_search.embedding_query_count", len(plan.get("embedding_queries", [])))
            span.set_attribute("repo_search.used_fallback", bool(plan.get("used_fallback")))
            span.set_attribute("repo_search.parser_mode", parser_mode)
        return plan
