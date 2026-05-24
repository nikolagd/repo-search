from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from microservices.common.schemas import HealthResponse
from microservices.common.security import require_api_token
from microservices.query_service.query_handler import parse_query

app = FastAPI(title="Repo Search Query Service", version="0.1.0")


class QueryParseRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    return HealthResponse(status="ok", database="not-used")


@app.post("/query/parse", dependencies=[Depends(require_api_token)])
def parse(request: QueryParseRequest) -> dict:
    return parse_query(request.query)
