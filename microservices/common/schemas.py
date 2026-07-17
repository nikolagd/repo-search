from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(10, ge=1, le=50)


class AdminCredentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=8, max_length=200)


class AdminUserResponse(BaseModel):
    id: int
    username: str


class AuthResponse(BaseModel):
    expires_in: int
    admin: AdminUserResponse


class HealthResponse(BaseModel):
    status: str
    database: str


class LivenessResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    dependencies: dict[str, str]


class RepositoryResponse(BaseModel):
    id: int
    name: str
    oai_endpoint: str
    last_harvest: str | None
    refresh_interval: int | None


class RepositoryWriteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    oai_endpoint: str = Field(..., min_length=1, max_length=1000)
    refresh_interval: int | None = Field(default=None, ge=1, le=525600)


class StatsResponse(BaseModel):
    repositories: int
    publications: int
    publications_with_embeddings: int
    last_harvest: str | None
