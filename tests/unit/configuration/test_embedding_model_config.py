from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_REVISION = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"


def test_embedding_model_is_wired_consistently_to_both_docker_services() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text(encoding="utf-8"))
    expected = f"${{EMBEDDING_MODEL:-{DEFAULT_MODEL}}}"
    expected_revision = f"${{EMBEDDING_MODEL_REVISION:-{DEFAULT_REVISION}}}"

    assert compose["services"]["search-service"]["environment"]["EMBEDDING_MODEL"] == expected
    assert compose["services"]["embedding-service"]["environment"]["EMBEDDING_MODEL"] == expected
    assert compose["services"]["search-service"]["environment"]["EMBEDDING_MODEL_REVISION"] == expected_revision
    assert compose["services"]["embedding-service"]["environment"]["EMBEDDING_MODEL_REVISION"] == expected_revision


def test_embedding_model_defaults_match_in_example_env_and_kubernetes_config() -> None:
    example_env = dict(
        line.split("=", 1)
        for line in (REPOSITORY_ROOT / ".env.microservices.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    kubernetes_documents = list(
        yaml.safe_load_all((REPOSITORY_ROOT / "k8s" / "01-config.yaml").read_text(encoding="utf-8"))
    )

    assert example_env["EMBEDDING_MODEL"] == DEFAULT_MODEL
    assert kubernetes_documents[0]["data"]["EMBEDDING_MODEL"] == DEFAULT_MODEL
    assert example_env["EMBEDDING_MODEL_REVISION"] == DEFAULT_REVISION
    assert kubernetes_documents[0]["data"]["EMBEDDING_MODEL_REVISION"] == DEFAULT_REVISION


def test_gpu_overlay_inherits_the_shared_kubernetes_config() -> None:
    gpu_kustomization = yaml.safe_load((REPOSITORY_ROOT / "k8s-gpu" / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "../k8s" in gpu_kustomization["resources"]
