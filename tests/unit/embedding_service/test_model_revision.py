from microservices.common.embedding_provenance import DEFAULT_EMBEDDING_MODEL_REVISION
from microservices.embedding_service import model as embedding_model


def test_sentence_transformer_receives_the_pinned_revision(monkeypatch) -> None:
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            calls.append((model_name, kwargs))

        def encode(self, text, *, normalize_embeddings):
            assert text == "query: warmup"
            assert normalize_embeddings is True

    monkeypatch.setattr(embedding_model, "SentenceTransformer", FakeSentenceTransformer)
    monkeypatch.setattr(embedding_model.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(embedding_model, "REQUESTED_DEVICE", "cpu")
    monkeypatch.setattr(embedding_model, "GPU_REQUIRED", False)

    embedding_model.warm_up_embedding_model()

    assert embedding_model.MODEL_REVISION == DEFAULT_EMBEDDING_MODEL_REVISION
    assert calls == [
        (
            embedding_model.MODEL_NAME,
            {"revision": DEFAULT_EMBEDDING_MODEL_REVISION, "device": "cpu"},
        )
    ]
