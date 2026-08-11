from sentence_transformers import SentenceTransformer
import torch

from microservices.common.embedding_provenance import (
    build_document_text,
    embedding_model_name,
    embedding_model_revision,
)
from microservices.embedding_service.device import parse_gpu_required, requested_embedding_device, select_embedding_device

MODEL_NAME = embedding_model_name()
MODEL_REVISION = embedding_model_revision()
REQUESTED_DEVICE = requested_embedding_device()
GPU_REQUIRED = parse_gpu_required()
device: str | None = None
model: SentenceTransformer | None = None
initialization_error: str | None = None


def warm_up_embedding_model() -> None:
    global device, initialization_error, model

    device = None
    model = None
    initialization_error = None
    try:
        selected_device = select_embedding_device(
            REQUESTED_DEVICE,
            gpu_required=GPU_REQUIRED,
            cuda_available=torch.cuda.is_available(),
        )
        candidate = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, device=selected_device)
        candidate.encode("query: warmup", normalize_embeddings=True)
    except Exception as exc:
        initialization_error = str(exc)
        raise

    device = selected_device
    model = candidate


def require_embedding_model() -> SentenceTransformer:
    if model is None:
        raise RuntimeError(initialization_error or "Embedding model nije inicijalizovan.")
    return model
