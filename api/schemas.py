from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(10, ge=1, le=50)


class HealthResponse(BaseModel):
    status: str
    database: str


class RepositoryResponse(BaseModel):
    id: int
    name: str
    oai_endpoint: str
    last_harvest: str | None
    refresh_interval: int | None


class StatsResponse(BaseModel):
    repositories: int
    publications: int
    publications_with_embeddings: int
    last_harvest: str | None
