from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel, Field

from microservices.common.app_logging import emit_app_event
from microservices.common.embedding_provenance import document_source_hash, utc_generated_at
from microservices.common.health import (
    HEALTH_OK,
    HEALTH_UNAVAILABLE,
    build_health_response,
    build_liveness_response,
    build_readiness_response,
)
from microservices.common.observability import observe_embedding, set_retrieval_model_info, setup_observability
from microservices.common.schemas import HealthResponse, LivenessResponse, ReadinessResponse
from microservices.embedding_service import model as embedding_model
from microservices.embedding_service.model import build_document_text
from microservices.common.security import require_api_token

app = FastAPI(title="Repo Search Embedding Service", version="0.1.0")
setup_observability(app, "embedding-service")
app.state.model_ready = False


class QueryEmbeddingRequest(BaseModel):
    query: str = Field(..., min_length=1)


class DocumentEmbeddingRequest(BaseModel):
    title: str | None = None
    abstract: str | None = None


@app.on_event("startup")
def startup() -> None:
    app.state.model_ready = False
    embedding_model.warm_up_embedding_model()
    model = embedding_model.require_embedding_model()
    set_retrieval_model_info(
        "embedding-service",
        "embedding_model",
        {
            "model": embedding_model.MODEL_NAME,
            "device": embedding_model.device,
            "dimension": model.get_sentence_embedding_dimension(),
        },
    )
    app.state.model_ready = True


def readiness_dependencies() -> dict[str, str]:
    return {
        "model": HEALTH_OK if getattr(app.state, "model_ready", False) else HEALTH_UNAVAILABLE,
    }


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
def model_status() -> dict[str, str | int | bool | None]:
    model = embedding_model.model
    return {
        "embedding_model": embedding_model.MODEL_NAME,
        "embedding_device_requested": embedding_model.REQUESTED_DEVICE,
        "embedding_device": embedding_model.device,
        "embedding_gpu_required": embedding_model.GPU_REQUIRED,
        "embedding_dimension": model.get_sentence_embedding_dimension() if model is not None else None,
        "embedding_initialization_error": embedding_model.initialization_error,
    }


@app.post("/embed/query", dependencies=[Depends(require_api_token)])
def embed_query(request: QueryEmbeddingRequest) -> dict[str, list[float]]:
    model = embedding_model.require_embedding_model()
    with observe_embedding("embedding-service", "query") as span:
        query = request.query.strip()
        if span is not None:
            span.set_attribute("repo_search.query_length", len(query))
        vector = model.encode(
            f"query: {query}",
            normalize_embeddings=True,
        ).tolist()
        if span is not None:
            span.set_attribute("repo_search.embedding_dimension", len(vector))
        emit_app_event(
            "embedding.generated",
            "embedding-service",
            kind="query",
            text_length=len(query),
            embedding_dimension=len(vector),
            model=embedding_model.MODEL_NAME,
            device=embedding_model.device,
        )
    return {"embedding": vector}


@app.post("/embed/document", dependencies=[Depends(require_api_token)])
def embed_document(request: DocumentEmbeddingRequest) -> dict[str, object]:
    model = embedding_model.require_embedding_model()
    with observe_embedding("embedding-service", "document") as span:
        document_text = build_document_text(request.title, request.abstract)
        if span is not None:
            span.set_attribute("repo_search.document_text_length", len(document_text))
        vector = model.encode(
            document_text,
            normalize_embeddings=True,
        ).tolist()
        if span is not None:
            span.set_attribute("repo_search.embedding_dimension", len(vector))
        emit_app_event(
            "embedding.generated",
            "embedding-service",
            kind="document",
            text_length=len(document_text),
            embedding_dimension=len(vector),
            model=embedding_model.MODEL_NAME,
            device=embedding_model.device,
        )
    return {
        "embedding": vector,
        "embedding_model": embedding_model.MODEL_NAME,
        "embedding_dimension": len(vector),
        "embedding_generated_at": utc_generated_at().isoformat(),
        "embedding_source_hash": document_source_hash(request.title, request.abstract),
    }
