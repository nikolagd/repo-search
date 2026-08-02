import re

from pydantic import BaseModel, Field, field_validator, model_validator


MAX_AUTHOR_FILTERS = 10
MAX_AUTHOR_NAME_LENGTH = 200


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=1000)
    author_names: list[str] = Field(default_factory=list, max_length=MAX_AUTHOR_FILTERS)
    limit: int = Field(10, ge=1, le=50)

    @field_validator("author_names", mode="before")
    @classmethod
    def normalize_author_names(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("author_names must be a list")
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("author names must be strings")
            name = " ".join(item.split())
            if not name:
                raise ValueError("author names must not be blank")
            if not re.search(r"[^\W\d_]", name, flags=re.UNICODE):
                raise ValueError("author names must contain at least one letter")
            if len(name) > MAX_AUTHOR_NAME_LENGTH:
                raise ValueError(f"author names must not exceed {MAX_AUTHOR_NAME_LENGTH} characters")
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                result.append(name)
        return result

    @model_validator(mode="after")
    def require_query_or_author(self) -> "SearchRequest":
        self.query = self.query.strip()
        if not self.query and not self.author_names:
            raise ValueError("a nonblank query or at least one author name is required")
        return self


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
