from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from performance_measurement.common import (
    MeasurementError,
    build_metadata,
    canonical_sha256,
    nearest_rank_percentile,
    summarize_values,
    validate_no_secrets,
    validate_url,
)


pytestmark = pytest.mark.unit


def test_nearest_rank_p50_and_p95_are_deterministic() -> None:
    values = [float(value) for value in range(1, 21)]
    assert nearest_rank_percentile(values, 0.50) == 10.0
    assert nearest_rank_percentile(values, 0.95) == 19.0
    assert summarize_values([4.0, 1.0, 3.0, 2.0], attempted_count=5) == {
        "attempted_sample_count": 5,
        "sample_count": 4,
        "failed_sample_count": 1,
        "mean": 2.5,
        "median": 2.5,
        "minimum": 1.0,
        "maximum": 4.0,
        "p50": 2.0,
        "p95": 4.0,
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_samples_are_rejected(value: float) -> None:
    with pytest.raises(MeasurementError, match="finite"):
        nearest_rank_percentile([1.0, value], 0.95)


def test_hashes_are_canonical_and_independent_of_key_order() -> None:
    assert canonical_sha256({"b": 2, "a": [1, 3]}) == canonical_sha256({"a": [1, 3], "b": 2})
    assert len(canonical_sha256({"value": math.pi})) == 64


def test_metadata_is_deterministic_for_equal_inputs(runner_git_commit, verified_runtime_identity) -> None:
    config = {
        "deployment_label": "compose",
        "expected_models": {
            "embedding_model": "embedding",
            "embedding_model_revision": "revision",
            "embedding_template_version": "template",
            "llm_model": "llm",
        },
        "runtime_identity": {},
    }
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = build_metadata(
        config,
        measurement_type="search_latency",
        runner_git_commit=runner_git_commit,
        runtime_identity=verified_runtime_identity,
        started_at=timestamp,
        finished_at=timestamp,
        input_sha256={"queries": "b" * 64, "config": "a" * 64},
        repetitions={"measured": 2, "warmup": 1},
    )
    second = build_metadata(
        config,
        measurement_type="search_latency",
        runner_git_commit=runner_git_commit,
        runtime_identity=verified_runtime_identity,
        started_at=timestamp,
        finished_at=timestamp,
        input_sha256={"config": "a" * 64, "queries": "b" * 64},
        repetitions={"warmup": 1, "measured": 2},
    )
    assert first == second
    assert list(first["input_sha256"]) == ["config", "deployment_evidence", "queries"]
    assert list(first["repetitions"]) == ["measured", "warmup"]
    assert first["runner_git_commit"] == runner_git_commit
    assert first["configured_expectations"]["llm_model"] == "llm"
    assert first["verified_deployment_identity"]["deployment_git_revision"] == runner_git_commit
    assert first["observed_runtime_model_identity"]["embedding_model_revision"] == "revision"


def test_secret_fields_and_credential_urls_are_rejected_without_echoing_values() -> None:
    with pytest.raises(MeasurementError, match="credential") as error:
        validate_no_secrets({"nested": {"api_key": "sentinel-secret"}})
    assert "sentinel-secret" not in str(error.value)
    with pytest.raises(MeasurementError, match="credentials"):
        validate_url("https://user:sentinel@example.test/api", "endpoint")
    with pytest.raises(MeasurementError, match="credential query"):
        validate_url("https://example.test/api?access_token=sentinel", "endpoint")
    with pytest.raises(MeasurementError, match="credential-bearing URL"):
        validate_no_secrets({"source": "https://user:sentinel@example.test/log"})
