from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_collect_runs_compose_uses_frozen_network_runtime_and_current_checkout() -> None:
    compose = yaml.safe_load(
        (REPOSITORY_ROOT / "evaluation" / "docker-compose.collect-runs.yml").read_text(encoding="utf-8")
    )
    runner = compose["services"]["evaluation-runner"]

    assert runner["image"] == "repo-search-eval-search-service"
    assert runner["pull_policy"] == "never"
    assert runner["entrypoint"] == ["python", "-m", "evaluation"]
    assert runner["working_dir"] == "/workspace"
    assert runner["read_only"] is True
    assert "ports" not in runner
    assert "depends_on" not in runner
    assert runner["environment"]["EVALUATION_DATABASE_URL"].endswith(
        "@db-primary:5432/${DB_NAME:-repo_search}"
    )
    assert runner["environment"]["EVALUATION_API_TOKEN"] == (
        "${API_TOKEN:-local-dev-api-token-change-me}"
    )

    volumes = {volume["target"]: volume for volume in runner["volumes"]}
    assert volumes["/workspace"] == {
        "type": "bind",
        "source": ".",
        "target": "/workspace",
        "read_only": True,
    }
    assert volumes["/evaluation-input"]["read_only"] is True
    assert volumes["/evaluation-output"].get("read_only") is not True
