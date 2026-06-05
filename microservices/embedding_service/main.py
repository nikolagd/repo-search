from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from microservices.common.observability import observe_embedding, setup_observability
from microservices.common.schemas import HealthResponse
from microservices.embedding_service.model import build_document_text, model, warm_up_embedding_model
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


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    return HealthResponse(status="ok", database="not-used")


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
    return {"embedding": vector}
