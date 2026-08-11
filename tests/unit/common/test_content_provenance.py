from microservices.common.embedding_provenance import (
    DOCUMENT_TEMPLATE_VERSION,
    build_document_text,
    document_source_hash,
    embedding_is_current,
)


REVISION = "revision-a"


def _is_current(publication, *, model_name="model-a", revision=REVISION, template=DOCUMENT_TEMPLATE_VERSION):
    return embedding_is_current(
        publication,
        model_name=model_name,
        model_revision=revision,
        template_version=template,
        dimension=1024,
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
        "embedding_model_revision": REVISION,
        "embedding_template_version": DOCUMENT_TEMPLATE_VERSION,
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("A title", "An abstract"),
    }
    assert _is_current(publication)


def test_title_or_abstract_change_makes_embedding_stale() -> None:
    publication = {
        "title": "Changed title",
        "abstract": "An abstract",
        "has_embedding": True,
        "embedding_model": "model-a",
        "embedding_model_revision": REVISION,
        "embedding_template_version": DOCUMENT_TEMPLATE_VERSION,
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("Original title", "An abstract"),
    }
    assert not _is_current(publication)
    publication["title"] = "Original title"
    publication["abstract"] = "Changed abstract"
    assert not _is_current(publication)


def test_model_change_or_unknown_provenance_is_stale() -> None:
    publication = {
        "title": "A title",
        "abstract": None,
        "has_embedding": True,
        "embedding_model": "old-model",
        "embedding_model_revision": REVISION,
        "embedding_template_version": DOCUMENT_TEMPLATE_VERSION,
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("A title", None),
    }
    assert not _is_current(publication, model_name="new-model")
    publication["embedding_model"] = None
    assert not _is_current(publication, model_name="old-model")


def test_missing_revision_or_template_and_changed_values_are_stale() -> None:
    publication = {
        "title": "A title",
        "abstract": None,
        "has_embedding": True,
        "embedding_model": "model-a",
        "embedding_model_revision": REVISION,
        "embedding_template_version": DOCUMENT_TEMPLATE_VERSION,
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T10:00:00+00:00",
        "embedding_source_hash": document_source_hash("A title", None),
    }
    assert _is_current(publication)
    assert not _is_current({**publication, "embedding_model_revision": None})
    assert not _is_current({**publication, "embedding_template_version": None})
    assert not _is_current(publication, revision="revision-b")
    assert not _is_current(publication, template="e5-title-abstract-v2")
