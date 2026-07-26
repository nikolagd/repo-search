from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from performance_measurement.common import MeasurementError
from performance_measurement.runtime_identity import verify_runtime_identity


pytestmark = pytest.mark.unit

RUNNER_COMMIT = "a" * 40
EVIDENCE_HASH = "d" * 64
EXPECTED_MODELS = {
    "embedding_model": "embedding",
    "embedding_model_revision": "revision",
    "embedding_template_version": "template",
    "llm_model": "llm",
}


def _config(*, runtime_kind="microservices", thesis_ready=True):
    runtime_identity = {"runtime_kind": runtime_kind, "thesis_ready": thesis_ready}
    if runtime_kind == "microservices":
        runtime_identity.update(
            {
                "api_token_env": "PERFORMANCE_API_TOKEN",
                "query_model_status_url": "https://query.example.test/model/status",
                "embedding_model_status_url": "https://embedding.example.test/model/status",
                "request_timeout_seconds": 5,
            }
        )
    return {
        "deployment_label": "compose",
        "expected_models": dict(EXPECTED_MODELS),
        "runtime_identity": runtime_identity,
    }


def _evidence(*, runtime_kind="microservices", revision=RUNNER_COMMIT, models=None):
    evidence = {
        "deployment_label": "compose",
        "runtime_kind": runtime_kind,
        "deployment_git_revision": revision,
        "image_identities": {"application": "repo-search@sha256:" + "1" * 64},
        "captured_at_utc": "2026-01-01T00:00:00Z",
        "source": "external deployment inspection record",
    }
    if runtime_kind == "legacy_monolith":
        evidence["observed_runtime_models"] = dict(models or EXPECTED_MODELS)
    return evidence


def _microservice_client(*, embedding_revision="revision"):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "query.example.test":
            return httpx.Response(
                200,
                json={"llm_model": "llm", "llm_url": "http://not-persisted.invalid"},
            )
        return httpx.Response(
            200,
            json={
                "embedding_model": "embedding",
                "embedding_model_revision": embedding_revision,
                "embedding_template_version": "template",
                "embedding_device": "cuda",
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def test_microservice_preflight_authenticates_compares_and_retains_only_relevant_fields() -> None:
    client, requests = _microservice_client()
    identity = verify_runtime_identity(
        _config(),
        _evidence(),
        runner_git_commit=RUNNER_COMMIT,
        deployment_evidence_sha256=EVIDENCE_HASH,
        api_token="sentinel-token",
        client=client,
        clock=lambda: datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    assert len(requests) == 2
    assert all(request.headers["X-API-Key"] == "sentinel-token" for request in requests)
    observed = identity.observed_model_metadata()
    assert observed == {**EXPECTED_MODELS, "verified_at_utc": "2026-01-01T00:00:01Z"}
    assert "llm_url" not in observed
    assert "embedding_device" not in observed
    deployment = identity.deployment_metadata()
    assert deployment["deployment_git_revision"] == RUNNER_COMMIT
    assert deployment["revision_matches_runner"] is True
    assert deployment["evidence_sha256"] == EVIDENCE_HASH
    assert "source" not in deployment


def test_runtime_model_mismatch_fails_preflight() -> None:
    client, _ = _microservice_client(embedding_revision="wrong-revision")
    with pytest.raises(MeasurementError, match="embedding_model_revision"):
        verify_runtime_identity(
            _config(),
            _evidence(),
            runner_git_commit=RUNNER_COMMIT,
            deployment_evidence_sha256=EVIDENCE_HASH,
            api_token="token",
            client=client,
            clock=lambda: datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )


def test_thesis_ready_revision_must_match_runner_and_non_thesis_run_records_mismatch() -> None:
    client, _ = _microservice_client()
    with pytest.raises(MeasurementError, match="does not match runner_git_commit"):
        verify_runtime_identity(
            _config(thesis_ready=True),
            _evidence(revision="b" * 40),
            runner_git_commit=RUNNER_COMMIT,
            deployment_evidence_sha256=EVIDENCE_HASH,
            api_token="token",
            client=client,
            clock=lambda: datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )

    client, _ = _microservice_client()
    identity = verify_runtime_identity(
        _config(thesis_ready=False),
        _evidence(revision="b" * 40),
        runner_git_commit=RUNNER_COMMIT,
        deployment_evidence_sha256=EVIDENCE_HASH,
        api_token="token",
        client=client,
        clock=lambda: datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    assert identity.thesis_ready is False
    assert identity.revision_matches_runner is False


def test_legacy_monolith_fails_closed_without_equivalent_model_evidence() -> None:
    incomplete = _evidence(runtime_kind="legacy_monolith")
    incomplete.pop("observed_runtime_models")
    with pytest.raises(MeasurementError, match="incomplete"):
        verify_runtime_identity(
            _config(runtime_kind="legacy_monolith"),
            incomplete,
            runner_git_commit=RUNNER_COMMIT,
            deployment_evidence_sha256=EVIDENCE_HASH,
            api_token=None,
            clock=lambda: datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )

    identity = verify_runtime_identity(
        _config(runtime_kind="legacy_monolith"),
        _evidence(runtime_kind="legacy_monolith"),
        runner_git_commit=RUNNER_COMMIT,
        deployment_evidence_sha256=EVIDENCE_HASH,
        api_token=None,
        clock=lambda: datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    assert identity.runtime_kind == "legacy_monolith"
    assert identity.observed_model_metadata()["embedding_model_revision"] == "revision"
