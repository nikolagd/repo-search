from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


API_TOKEN = "unit-test-api-token"
AUTH_HEADERS = {"X-API-Key": API_TOKEN}


class FakeEmbeddingModel:
    def get_sentence_embedding_dimension(self) -> int:
        return 1024


@pytest.fixture
def embedding_service(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ModuleType, ModuleType]]:
    main_module_name = "microservices.embedding_service.main"
    model_module_name = "microservices.embedding_service.model"
    previous_main = sys.modules.pop(main_module_name, None)

    fake_model_module = ModuleType(model_module_name)
    fake_model_module.MODEL_NAME = "unit-test-embedding-model"
    fake_model_module.REQUESTED_DEVICE = "auto"
    fake_model_module.GPU_REQUIRED = False
    fake_model_module.device = "cpu"
    fake_model_module.model = FakeEmbeddingModel()
    fake_model_module.initialization_error = None
    fake_model_module.fail_warmup = False
    fake_model_module.warmup_calls = 0

    def warm_up_embedding_model() -> None:
        fake_model_module.warmup_calls += 1
        if fake_model_module.fail_warmup:
            fake_model_module.model = None
            fake_model_module.device = None
            fake_model_module.initialization_error = "embedding warmup failed"
            raise RuntimeError("embedding warmup failed")

    fake_model_module.warm_up_embedding_model = warm_up_embedding_model
    fake_model_module.require_embedding_model = lambda: fake_model_module.model
    fake_model_module.build_document_text = lambda title, abstract: f"{title or ''} {abstract or ''}".strip()
    monkeypatch.setitem(sys.modules, model_module_name, fake_model_module)

    module = importlib.import_module(main_module_name)
    monkeypatch.setattr(module, "set_retrieval_model_info", lambda *_args, **_kwargs: None)
    try:
        yield module, fake_model_module
    finally:
        sys.modules.pop(main_module_name, None)
        if previous_main is not None:
            sys.modules[main_module_name] = previous_main


def test_embedding_readiness_tracks_successful_lightweight_startup(
    embedding_service: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, fake_model_module = embedding_service
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    client = TestClient(module.app)
    try:
        live = client.get("/live")
        protected = client.get("/ready")
        before_startup = client.get("/ready", headers=AUTH_HEADERS)
        compatibility = client.get("/health", headers=AUTH_HEADERS)

        module.startup()
        after_startup = client.get("/ready", headers=AUTH_HEADERS)
        model_status = client.get("/model/status", headers=AUTH_HEADERS)
    finally:
        client.close()

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert protected.status_code == 401
    assert before_startup.status_code == 503
    assert before_startup.json() == {
        "status": "unavailable",
        "dependencies": {"model": "unavailable"},
    }
    assert compatibility.status_code == 200
    assert compatibility.json() == {"status": "unavailable", "database": "not-used"}
    assert after_startup.status_code == 200
    assert after_startup.json() == {"status": "ok", "dependencies": {"model": "ok"}}
    assert model_status.json()["embedding_device"] == "cpu"
    assert model_status.json()["embedding_device_requested"] == "auto"
    assert model_status.json()["embedding_gpu_required"] is False
    assert fake_model_module.warmup_calls == 1


def test_embedding_readiness_remains_unavailable_after_failed_warmup(
    embedding_service: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, fake_model_module = embedding_service
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    fake_model_module.fail_warmup = True

    with pytest.raises(RuntimeError, match="embedding warmup failed"):
        module.startup()

    client = TestClient(module.app)
    try:
        response = client.get("/ready", headers=AUTH_HEADERS)
        model_status = client.get("/model/status", headers=AUTH_HEADERS)
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "dependencies": {"model": "unavailable"},
    }
    assert model_status.json()["embedding_device"] is None
    assert model_status.json()["embedding_initialization_error"] == "embedding warmup failed"
