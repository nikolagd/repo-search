import os

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.auth import require_api_token
from api.schemas import HealthResponse, RepositoryResponse, SearchRequest, StatsResponse
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


app.include_router(api_router)
