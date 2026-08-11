from __future__ import annotations

import pytest

from microservices.embedding_service.device import (
    EmbeddingDeviceConfigurationError,
    parse_gpu_required,
    requested_embedding_device,
    select_embedding_device,
)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_gpu_required_accepts_true_values(value: str) -> None:
    assert parse_gpu_required(value) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_gpu_required_accepts_false_values(value: str) -> None:
    assert parse_gpu_required(value) is False


def test_gpu_required_rejects_ambiguous_value() -> None:
    with pytest.raises(EmbeddingDeviceConfigurationError, match="GPU_REQUIRED"):
        parse_gpu_required("sometimes")


def test_requested_device_is_normalized_and_validated() -> None:
    assert requested_embedding_device(" CUDA ") == "cuda"
    with pytest.raises(EmbeddingDeviceConfigurationError, match="EMBEDDING_DEVICE"):
        requested_embedding_device("gpu")


def test_explicit_cuda_is_selected_when_available() -> None:
    assert select_embedding_device("cuda", gpu_required=True, cuda_available=True) == "cuda"


def test_gpu_required_fails_when_cuda_is_unavailable() -> None:
    with pytest.raises(EmbeddingDeviceConfigurationError, match="CUDA je obavezna"):
        select_embedding_device("auto", gpu_required=True, cuda_available=False)


def test_cpu_fallback_is_available_only_when_gpu_is_not_required() -> None:
    assert select_embedding_device("auto", gpu_required=False, cuda_available=False) == "cpu"
    assert select_embedding_device("cpu", gpu_required=False, cuda_available=True) == "cpu"
    with pytest.raises(EmbeddingDeviceConfigurationError, match="nije kompatibilan"):
        select_embedding_device("cpu", gpu_required=True, cuda_available=True)
