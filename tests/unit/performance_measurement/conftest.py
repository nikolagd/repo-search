from __future__ import annotations

import pytest

from performance_measurement.common import VerifiedRuntimeIdentity


RUNNER_GIT_COMMIT = "a" * 40


@pytest.fixture
def runner_git_commit() -> str:
    return RUNNER_GIT_COMMIT


@pytest.fixture
def verified_runtime_identity() -> VerifiedRuntimeIdentity:
    return VerifiedRuntimeIdentity(
        deployment_label="compose",
        runtime_kind="microservices",
        deployment_git_revision=RUNNER_GIT_COMMIT,
        image_identities=(("query-service", "query@sha256:" + "1" * 64),),
        evidence_sha256="d" * 64,
        evidence_captured_at_utc="2026-01-01T00:00:00Z",
        revision_matches_runner=True,
        thesis_ready=True,
        observed_models=(
            ("embedding_model", "embedding"),
            ("embedding_model_revision", "revision"),
            ("embedding_template_version", "template"),
            ("llm_model", "llm"),
        ),
        verified_at_utc="2026-01-01T00:00:01Z",
    )
