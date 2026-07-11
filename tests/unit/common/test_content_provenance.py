from microservices.common.embedding_provenance import (
    build_document_text,
    document_source_hash,
    embedding_is_current,
)


def test_document_hash_is_deterministic_for_exact_embedding_input() -> None:
    assert build_document_text("A title", "An abstract") == "passage: Title: A title\nAbstract: An abstract"
    assert document_source_hash("A title", "An abstract") == document_source_hash("A title", "An abstract")
    assert document_source_hash(" A title", "An abstract") != document_source_hash("A title", "An abstract")


def test_unchanged_metadata_with_complete_provenance_is_current() -> None:
    publication = {
        "title": "A title",
        "abstract": "An abstract",
        "has_embedding": True,
        "embedding_model": "model-a",
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("A title", "An abstract"),
    }
    assert embedding_is_current(publication, model_name="model-a", dimension=1024)


def test_title_or_abstract_change_makes_embedding_stale() -> None:
    publication = {
        "title": "Changed title",
        "abstract": "An abstract",
        "has_embedding": True,
        "embedding_model": "model-a",
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("Original title", "An abstract"),
    }
    assert not embedding_is_current(publication, model_name="model-a", dimension=1024)
    publication["title"] = "Original title"
    publication["abstract"] = "Changed abstract"
    assert not embedding_is_current(publication, model_name="model-a", dimension=1024)


def test_model_change_or_unknown_provenance_is_stale() -> None:
    publication = {
        "title": "A title",
        "abstract": None,
        "has_embedding": True,
        "embedding_model": "old-model",
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("A title", None),
    }
    assert not embedding_is_current(publication, model_name="new-model", dimension=1024)
    publication["embedding_model"] = None
    assert not embedding_is_current(publication, model_name="old-model", dimension=1024)
