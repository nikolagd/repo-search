from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from microservices.search_service.main import PublicationEmbeddingRequest


def test_embedding_request_rejects_declared_or_database_dimension_mismatch() -> None:
    base = {
        "embedding": [0.1, 0.2],
        "embedding_model": "model-a",
        "embedding_model_revision": "revision-a",
        "embedding_template_version": "e5-title-abstract-v1",
        "embedding_generated_at": datetime.now(timezone.utc),
        "embedding_source_hash": "a" * 64,
    }
    with pytest.raises(ValidationError, match="does not match"):
        PublicationEmbeddingRequest(**base, embedding_dimension=3)
    with pytest.raises(ValidationError, match="length must be 1024"):
        PublicationEmbeddingRequest(**base, embedding_dimension=2)


def test_legacy_embedding_request_remains_accepted_with_unknown_provenance() -> None:
    request = PublicationEmbeddingRequest(embedding=[0.0] * 1024)
    assert request.embedding_model is None
    assert request.embedding_model_revision is None
    assert request.embedding_template_version is None
    assert request.embedding_dimension is None
