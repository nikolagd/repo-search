import os

import psycopg2
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from api.admin_auth import (
    authenticate_admin_user,
    build_auth_response,
    clear_admin_cookie,
    create_admin_user,
    has_admin_users,
    require_admin_user,
    set_admin_cookie,
)
from api.auth import require_api_token
from api.admin_jobs import (
    get_embedding_status,
    list_admin_repositories,
    queue_embedding_backfill,
    queue_repository_harvest,
)
from api.schemas import (
    AdminCredentials,
    AdminRepositoryResponse,
    AdminUserResponse,
    AuthResponse,
    EmbeddingStatusResponse,
    HarvestJobResponse,
    HealthResponse,
    RepositoryResponse,
    SearchRequest,
    StatsResponse,
)
from api.services import check_database, get_repositories, get_stats, run_search


def parse_origins() -> list[str]:
    raw = os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title="Repo Search API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_api_token)],
)

admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin_user)],
)


@api_router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        database_ok = check_database()
    except Exception:
        database_ok = False

    return HealthResponse(
        status="ok",
        database="ok" if database_ok else "unavailable",
    )


@api_router.get("/repositories", response_model=list[RepositoryResponse])
def repositories() -> list[RepositoryResponse]:
    try:
        return [RepositoryResponse(**repo) for repo in get_repositories()]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed while loading repositories.",
        ) from exc


@api_router.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    try:
        return StatsResponse(**get_stats())
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed while loading statistics.",
        ) from exc


@api_router.post("/search")
def search(request: SearchRequest) -> dict:
    query = request.query.strip()

    if not query:
        raise HTTPException(status_code=422, detail="Query cannot be empty.")

    try:
        return run_search(query=query, limit=request.limit)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Search failed. Check the database, embedding model, and LLM parser configuration.",
        ) from exc


@api_router.post("/auth/register", response_model=AuthResponse)
def register(request: AdminCredentials, response: Response) -> AuthResponse:
    if has_admin_users():
        raise HTTPException(
            status_code=403,
            detail="Admin registration is closed because an admin account already exists.",
        )

    try:
        admin_user = create_admin_user(request.username, request.password)
    except psycopg2.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=409,
            detail="Admin username is already registered.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Admin registration failed.",
        ) from exc

    set_admin_cookie(response, admin_user)
    return AuthResponse(**build_auth_response(admin_user))


@api_router.post("/auth/login", response_model=AuthResponse)
def login(request: AdminCredentials, response: Response) -> AuthResponse:
    admin_user = authenticate_admin_user(request.username, request.password)

    if admin_user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
        )

    set_admin_cookie(response, admin_user)
    return AuthResponse(**build_auth_response(admin_user))


@api_router.get("/auth/me", response_model=AdminUserResponse)
def me(admin_user: dict = Depends(require_admin_user)) -> AdminUserResponse:
    return AdminUserResponse(**admin_user)


@api_router.post("/auth/logout")
def logout(response: Response) -> dict:
    clear_admin_cookie(response)
    return {"status": "ok"}


@admin_router.get("/repositories", response_model=list[AdminRepositoryResponse])
def admin_repositories() -> list[AdminRepositoryResponse]:
    return [AdminRepositoryResponse(**repo) for repo in list_admin_repositories()]


@admin_router.post("/repositories/{repo_id}/harvest", response_model=HarvestJobResponse)
def harvest_repository_admin(repo_id: int, background_tasks: BackgroundTasks) -> HarvestJobResponse:
    return HarvestJobResponse(**queue_repository_harvest(repo_id, background_tasks))


@admin_router.get("/embeddings", response_model=EmbeddingStatusResponse)
def embedding_status() -> EmbeddingStatusResponse:
    return EmbeddingStatusResponse(**get_embedding_status())


@admin_router.post("/embeddings/backfill", response_model=HarvestJobResponse)
def embedding_backfill(background_tasks: BackgroundTasks) -> HarvestJobResponse:
    return HarvestJobResponse(**queue_embedding_backfill(background_tasks))


api_router.include_router(admin_router)
app.include_router(api_router)
