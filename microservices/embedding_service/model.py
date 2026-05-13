from sentence_transformers import SentenceTransformer
import torch

device = "cuda" if torch.cuda.is_available() else "cpu"

model = SentenceTransformer(
    "intfloat/multilingual-e5-large",
    device=device,
)


def build_document_text(title, abstract):
    parts = []

    if title:
        parts.append(f"Title: {title}")
    if abstract:
        parts.append(f"Abstract: {abstract}")

    body = "\n".join(parts).strip()
    return f"passage: {body}"


def warm_up_embedding_model():
    model.encode("query: warmup", normalize_embeddings=True)
