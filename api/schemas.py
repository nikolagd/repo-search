from typing import Literal

from pydantic import BaseModel, Field


UserRole = Literal["admin", "editor", "viewer"]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(10, ge=1, le=50)


class AdminCredentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=8, max_length=200)


class AdminUserResponse(BaseModel):
    id: int
    username: str
    role: UserRole


class AdminUserListResponse(AdminUserResponse):
    created_at: str | None


class AdminUserCreateRequest(AdminCredentials):
    role: UserRole = "viewer"


class AuthResponse(BaseModel):
    expires_in: int
    admin: AdminUserResponse


class HealthResponse(BaseModel):
    status: str
    database: str


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


class HarvestJobResponse(BaseModel):
    id: int | None = None
    job_type: str | None = None
    repository_id: int | None = None
    status: str
    started_at: str | None
    finished_at: str | None
    processed_records: int | None
    message: str


class AdminRepositoryResponse(RepositoryResponse):
    harvest_job: HarvestJobResponse | None


class EmbeddingStatusResponse(BaseModel):
    missing_embeddings: int
    embedding_job: HarvestJobResponse | None


class StatsResponse(BaseModel):
    repositories: int
    publications: int
    publications_with_embeddings: int
    last_harvest: str | None
