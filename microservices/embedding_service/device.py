from __future__ import annotations

import os


class EmbeddingDeviceConfigurationError(RuntimeError):
    """Raised when the requested embedding device cannot be used safely."""


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})
SUPPORTED_DEVICES = frozenset({"auto", "cpu", "cuda"})


def parse_gpu_required(value: str | None = None) -> bool:
    raw_value = os.getenv("GPU_REQUIRED", "false") if value is None else value
    normalized = raw_value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise EmbeddingDeviceConfigurationError(
        "GPU_REQUIRED mora biti true/false, 1/0, yes/no ili on/off."
    )


def requested_embedding_device(value: str | None = None) -> str:
    raw_value = os.getenv("EMBEDDING_DEVICE", "auto") if value is None else value
    normalized = raw_value.strip().lower()
    if normalized not in SUPPORTED_DEVICES:
        raise EmbeddingDeviceConfigurationError(
            "EMBEDDING_DEVICE mora biti auto, cpu ili cuda."
        )
    return normalized


def select_embedding_device(
    requested: str,
    *,
    gpu_required: bool,
    cuda_available: bool,
) -> str:
    normalized = requested_embedding_device(requested)
    if gpu_required and normalized == "cpu":
        raise EmbeddingDeviceConfigurationError(
            "GPU_REQUIRED=true nije kompatibilan sa EMBEDDING_DEVICE=cpu."
        )
    if normalized == "cuda" or gpu_required:
        if not cuda_available:
            raise EmbeddingDeviceConfigurationError(
                "CUDA je obavezna, ali torch.cuda.is_available() je false."
            )
        return "cuda"
    if normalized == "cpu":
        return "cpu"
    return "cuda" if cuda_available else "cpu"
