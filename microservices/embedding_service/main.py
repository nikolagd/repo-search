from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from microservices.common.app_logging import emit_app_event
from microservices.common.observability import observe_embedding, set_retrieval_model_info, setup_observability
from microservices.common.schemas import HealthResponse
from microservices.embedding_service.model import MODEL_NAME, build_document_text, device, model, warm_up_embedding_model
from microservices.common.security import require_api_token

app = FastAPI(title="Repo Search Embedding Service", version="0.1.0")
setup_observability(app, "embedding-service")


class QueryEmbeddingRequest(BaseModel):
    query: str = Field(..., min_length=1)


class DocumentEmbeddingRequest(BaseModel):
    title: str | None = None
    abstract: str | None = None


@app.on_event("startup")
def startup() -> None:
    warm_up_embedding_model()
    set_retrieval_model_info(
        "embedding-service",
        "embedding_model",
        {
            "model": MODEL_NAME,
            "device": device,
            "dimension": model.get_sentence_embedding_dimension(),
        },
    )


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    return HealthResponse(status="ok", database="not-used")


@app.get("/model/status", dependencies=[Depends(require_api_token)])
def model_status() -> dict[str, str | int | None]:
    return {
        "embedding_model": MODEL_NAME,
        "embedding_device": device,
        "embedding_dimension": model.get_sentence_embedding_dimension(),
    }


@app.post("/embed/query", dependencies=[Depends(require_api_token)])
def embed_query(request: QueryEmbeddingRequest) -> dict[str, list[float]]:
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
            model=MODEL_NAME,
            device=device,
        )
    return {"embedding": vector}


@app.post("/embed/document", dependencies=[Depends(require_api_token)])
def embed_document(request: DocumentEmbeddingRequest) -> dict[str, list[float]]:
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
            model=MODEL_NAME,
            device=device,
        )
    return {"embedding": vector}
