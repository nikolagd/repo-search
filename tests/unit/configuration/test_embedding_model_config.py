from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = "intfloat/multilingual-e5-large"


def test_embedding_model_is_wired_consistently_to_both_docker_services() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text(encoding="utf-8"))
    expected = f"${{EMBEDDING_MODEL:-{DEFAULT_MODEL}}}"

    assert compose["services"]["search-service"]["environment"]["EMBEDDING_MODEL"] == expected
    assert compose["services"]["embedding-service"]["environment"]["EMBEDDING_MODEL"] == expected


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
