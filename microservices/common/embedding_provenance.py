from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any


DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
EXPECTED_EMBEDDING_DIMENSION = 1024


def embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def build_document_text(title: str | None, abstract: str | None) -> str:
    parts = []
    if title:
        parts.append(f"Title: {title}")
    if abstract:
        parts.append(f"Abstract: {abstract}")
    body = "\n".join(parts).strip()
    return f"passage: {body}"


def document_source_hash(title: str | None, abstract: str | None) -> str:
    return hashlib.sha256(build_document_text(title, abstract).encode("utf-8")).hexdigest()


def utc_generated_at() -> datetime:
    return datetime.now(timezone.utc)


def embedding_is_current(
    publication: dict[str, Any],
    *,
    model_name: str,
    dimension: int,
) -> bool:
    return bool(
        publication.get("has_embedding")
        and publication.get("embedding_generated_at")
        and publication.get("embedding_model") == model_name
        and publication.get("embedding_dimension") == dimension
        and publication.get("embedding_source_hash")
        == document_source_hash(publication.get("title"), publication.get("abstract"))
    )
