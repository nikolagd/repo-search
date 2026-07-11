from sentence_transformers import SentenceTransformer
import torch

from microservices.common.embedding_provenance import build_document_text, embedding_model_name

MODEL_NAME = embedding_model_name()
device = "cuda" if torch.cuda.is_available() else "cpu"

model = SentenceTransformer(
    MODEL_NAME,
    device=device,
)


def warm_up_embedding_model():
    model.encode("query: warmup", normalize_embeddings=True)
