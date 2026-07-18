from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _deployment(documents: list[dict], name: str) -> dict:
    return next(
        document
        for document in documents
        if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == name
    )


def _container(deployment: dict, name: str) -> dict:
    return next(
        container
        for container in deployment["spec"]["template"]["spec"]["containers"]
        if container["name"] == name
    )


def _environment(container: dict) -> dict[str, str]:
    return {item["name"]: item["value"] for item in container.get("env", []) if "value" in item}


def test_compose_is_cuda_required_by_default_with_explicit_cpu_override() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["embedding-service"]["environment"]

    assert compose["services"]["embedding-service"]["gpus"] == "all"
    assert compose["services"]["ollama"]["gpus"] == "all"
    assert environment["EMBEDDING_DEVICE"] == "${EMBEDDING_DEVICE:-cuda}"
    assert environment["GPU_REQUIRED"] == "${GPU_REQUIRED:-true}"


def test_base_is_cpu_fallback_and_gpu_overlay_requires_cuda() -> None:
    base_config = next(
        document
        for document in yaml.safe_load_all((REPOSITORY_ROOT / "k8s" / "01-config.yaml").read_text(encoding="utf-8"))
        if document.get("kind") == "ConfigMap" and document["metadata"]["name"] == "repo-search-config"
    )
    patch = yaml.safe_load((REPOSITORY_ROOT / "k8s-gpu" / "embedding-service-gpu-patch.yaml").read_text(encoding="utf-8"))
    patch_container = _container(patch, "embedding-service")
    environment = _environment(patch_container)

    assert base_config["data"]["EMBEDDING_DEVICE"] == "auto"
    assert base_config["data"]["GPU_REQUIRED"] == "false"
    assert environment["EMBEDDING_DEVICE"] == "cuda"
    assert environment["GPU_REQUIRED"] == "true"
    assert environment["NVIDIA_DRIVER_CAPABILITIES"] == "compute,utility"
    assert patch_container["resources"]["limits"]["nvidia.com/gpu"] == "1"


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl nije dostupan za Kustomize render")
def test_generated_gpu_manifests_use_validated_single_gpu_runtime_sharing() -> None:
    rendered = subprocess.run(
        ["kubectl", "kustomize", "k8s-gpu"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    documents = [document for document in yaml.safe_load_all(rendered) if document]
    embedding = _deployment(documents, "embedding-service")
    ollama = _deployment(documents, "ollama")
    embedding_container = _container(embedding, "embedding-service")
    ollama_container = _container(ollama, "ollama")

    assert embedding["spec"]["template"]["spec"]["runtimeClassName"] == "nvidia"
    assert ollama["spec"]["template"]["spec"]["runtimeClassName"] == "nvidia"
    assert embedding_container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert "nvidia.com/gpu" not in ollama_container.get("resources", {}).get("limits", {})
    assert _environment(ollama_container)["NVIDIA_VISIBLE_DEVICES"] == "all"
    assert _environment(ollama_container)["NVIDIA_DRIVER_CAPABILITIES"] == "compute,utility"
    assert any(document.get("kind") == "RuntimeClass" and document["metadata"]["name"] == "nvidia" for document in documents)
    assert any(document.get("kind") == "DaemonSet" and document["metadata"]["name"] == "dcgm-exporter" for document in documents)


def test_documentation_uses_gpu_overlay_as_primary_without_redundant_apply_sequence() -> None:
    for relative_path in ("README.md", "k8s/README.md"):
        content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        apply_commands = re.findall(r"kubectl apply -k (k8s(?:-gpu)?/)", content)
        assert apply_commands[0] == "k8s-gpu/"
        assert not re.search(
            r"kubectl apply -k k8s/\s*(?:\r?\n)+\s*kubectl apply -k k8s-gpu/",
            content,
        )


def test_gpu_verification_script_has_strict_modes_and_nonzero_failure_path() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "verify-gpu-deployment.ps1").read_text(encoding="utf-8")

    assert '[ValidateSet("Compose", "Kubernetes", "All")]' in script
    assert "embedding_gpu_required" in script
    assert "torch.cuda.is_available()" in script
    assert "ollama ps" in script
    assert "DCGM_FI_DEV_GPU_UTIL" in script
    assert re.search(r"catch\s*\{[\s\S]*?exit 1", script)
