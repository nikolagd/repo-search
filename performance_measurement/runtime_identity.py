from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from performance_measurement.common import (
    EXPECTED_MODEL_FIELDS,
    MeasurementError,
    VerifiedRuntimeIdentity,
    format_utc,
    parse_timestamp,
    require_nonblank,
    require_positive_float,
    utc_now,
    validate_common_config,
    validate_no_secrets,
    validate_url,
)


RUNTIME_KINDS = {"microservices", "legacy_monolith"}


def _full_git_revision(value: Any, field: str) -> str:
    revision = require_nonblank(value, field).casefold()
    if len(revision) not in {40, 64} or any(character not in "0123456789abcdef" for character in revision):
        raise MeasurementError(f"{field} must be a full hexadecimal Git revision")
    return revision


def _sha256(value: Any, field: str) -> str:
    digest = require_nonblank(value, field).casefold()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MeasurementError(f"{field} must be a 64-character hexadecimal digest")
    return digest


def _model_values(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(EXPECTED_MODEL_FIELDS):
        raise MeasurementError(f"{field} must contain exactly the required runtime model fields")
    return {
        name: require_nonblank(value.get(name), f"{field}.{name}")
        for name in EXPECTED_MODEL_FIELDS
    }


def _image_identities(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise MeasurementError("deployment_evidence.image_identities must be a nonempty JSON object")
    normalized = {}
    for name, identity in value.items():
        normalized_name = require_nonblank(name, "deployment evidence image name")
        normalized_identity = require_nonblank(identity, f"deployment_evidence.image_identities.{normalized_name}")
        marker = normalized_identity.casefold().rfind("sha256:")
        digest = normalized_identity[marker + len("sha256:") :] if marker >= 0 else ""
        if len(digest) != 64 or any(character not in "0123456789abcdefABCDEF" for character in digest):
            raise MeasurementError("deployment image identities must contain an immutable sha256 digest")
        normalized[normalized_name] = normalized_identity
    return dict(sorted(normalized.items()))


def _status_url(value: Any, field: str) -> str:
    url = validate_url(value, field)
    if not urlsplit(url).path.endswith("/model/status"):
        raise MeasurementError(f"{field} must target a /model/status endpoint")
    return url


def _runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    common = validate_common_config(config)
    section = config["runtime_identity"]
    runtime_kind = require_nonblank(section.get("runtime_kind"), "runtime_identity.runtime_kind")
    if runtime_kind not in RUNTIME_KINDS:
        raise MeasurementError("runtime_identity.runtime_kind must be microservices or legacy_monolith")
    thesis_ready = section.get("thesis_ready")
    if not isinstance(thesis_ready, bool):
        raise MeasurementError("runtime_identity.thesis_ready must be a boolean")
    if runtime_kind == "microservices":
        allowed = {
            "runtime_kind",
            "thesis_ready",
            "api_token_env",
            "query_model_status_url",
            "embedding_model_status_url",
            "request_timeout_seconds",
        }
        if set(section) != allowed:
            raise MeasurementError("microservices runtime identity configuration is incomplete or contains unsupported fields")
        timeout = require_positive_float(
            section["request_timeout_seconds"],
            "runtime_identity.request_timeout_seconds",
        )
        return {
            **common,
            "runtime_kind": runtime_kind,
            "thesis_ready": thesis_ready,
            "api_token_env": require_nonblank(section["api_token_env"], "runtime_identity.api_token_env"),
            "query_model_status_url": _status_url(
                section["query_model_status_url"],
                "runtime_identity.query_model_status_url",
            ),
            "embedding_model_status_url": _status_url(
                section["embedding_model_status_url"],
                "runtime_identity.embedding_model_status_url",
            ),
            "request_timeout_seconds": timeout,
        }
    if set(section) != {"runtime_kind", "thesis_ready"}:
        raise MeasurementError("legacy monolith runtime identity must rely exclusively on equivalent external evidence")
    return {**common, "runtime_kind": runtime_kind, "thesis_ready": thesis_ready}


def _evidence(
    evidence: dict[str, Any],
    *,
    expected_deployment_label: str,
    expected_runtime_kind: str,
    verified_at: datetime,
) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise MeasurementError("external deployment evidence is required")
    validate_no_secrets(evidence, "deployment_evidence")
    base_fields = {
        "deployment_label",
        "runtime_kind",
        "deployment_git_revision",
        "image_identities",
        "captured_at_utc",
        "source",
    }
    required = base_fields | ({"observed_runtime_models"} if expected_runtime_kind == "legacy_monolith" else set())
    if set(evidence) != required:
        raise MeasurementError("deployment evidence is incomplete or contains unsupported fields")
    if require_nonblank(evidence["deployment_label"], "deployment_evidence.deployment_label") != expected_deployment_label:
        raise MeasurementError("deployment evidence label does not match the configured deployment")
    if require_nonblank(evidence["runtime_kind"], "deployment_evidence.runtime_kind") != expected_runtime_kind:
        raise MeasurementError("deployment evidence runtime kind does not match configuration")
    captured_at = parse_timestamp(evidence["captured_at_utc"], "deployment_evidence.captured_at_utc")
    if captured_at > verified_at:
        raise MeasurementError("deployment evidence capture timestamp is in the future")
    return {
        "deployment_git_revision": _full_git_revision(
            evidence["deployment_git_revision"],
            "deployment_evidence.deployment_git_revision",
        ),
        "image_identities": _image_identities(evidence["image_identities"]),
        "captured_at": captured_at,
        "source": require_nonblank(evidence["source"], "deployment_evidence.source"),
        "observed_runtime_models": (
            _model_values(evidence["observed_runtime_models"], "deployment_evidence.observed_runtime_models")
            if expected_runtime_kind == "legacy_monolith"
            else None
        ),
    }


def _json_status(response: httpx.Response, label: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        raise MeasurementError(f"{label} returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise MeasurementError(f"{label} returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise MeasurementError(f"{label} must return a JSON object")
    return payload


def _observed_microservice_models(
    config: dict[str, Any],
    *,
    api_token: str,
    client: httpx.Client,
) -> dict[str, str]:
    token = require_nonblank(api_token, "runtime identity API token environment variable")
    headers = {"X-API-Key": token}
    try:
        query_response = client.get(
            config["query_model_status_url"],
            headers=headers,
            timeout=config["request_timeout_seconds"],
        )
        embedding_response = client.get(
            config["embedding_model_status_url"],
            headers=headers,
            timeout=config["request_timeout_seconds"],
        )
    except httpx.TimeoutException as exc:
        raise MeasurementError("runtime model identity preflight timed out") from exc
    except httpx.RequestError as exc:
        raise MeasurementError("runtime model identity preflight connection failed") from exc
    query_payload = _json_status(query_response, "Query Service model status")
    embedding_payload = _json_status(embedding_response, "Embedding Service model status")
    return _model_values(
        {
            "llm_model": query_payload.get("llm_model"),
            "embedding_model": embedding_payload.get("embedding_model"),
            "embedding_model_revision": embedding_payload.get("embedding_model_revision"),
            "embedding_template_version": embedding_payload.get("embedding_template_version"),
        },
        "observed runtime model identity",
    )


def verify_runtime_identity(
    config: dict[str, Any],
    deployment_evidence: dict[str, Any],
    *,
    runner_git_commit: str,
    deployment_evidence_sha256: str,
    api_token: str | None,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> VerifiedRuntimeIdentity:
    validated = _runtime_config(config)
    runner_revision = _full_git_revision(runner_git_commit, "runner_git_commit")
    evidence_hash = _sha256(deployment_evidence_sha256, "deployment_evidence_sha256")
    own_client = client is None
    active_client = client or httpx.Client(timeout=validated.get("request_timeout_seconds", 30.0))
    try:
        if validated["runtime_kind"] == "microservices":
            observed_models = _observed_microservice_models(
                validated,
                api_token=api_token or "",
                client=active_client,
            )
        else:
            observed_models = None
    finally:
        if own_client:
            active_client.close()
    verified_at = clock()
    evidence = _evidence(
        deployment_evidence,
        expected_deployment_label=validated["deployment_label"],
        expected_runtime_kind=validated["runtime_kind"],
        verified_at=verified_at,
    )
    if observed_models is None:
        observed_models = evidence["observed_runtime_models"]
    assert observed_models is not None
    expected_models = validated["expected_models"]
    mismatches = [
        name for name in EXPECTED_MODEL_FIELDS if observed_models[name] != expected_models[name]
    ]
    if mismatches:
        raise MeasurementError(
            "runtime model identity does not match configured expectations: " + ", ".join(mismatches)
        )
    revision_matches = evidence["deployment_git_revision"] == runner_revision
    if validated["thesis_ready"] and not revision_matches:
        raise MeasurementError("thesis-ready deployment Git revision does not match runner_git_commit")
    return VerifiedRuntimeIdentity(
        deployment_label=validated["deployment_label"],
        runtime_kind=validated["runtime_kind"],
        deployment_git_revision=evidence["deployment_git_revision"],
        image_identities=tuple(evidence["image_identities"].items()),
        evidence_sha256=evidence_hash,
        evidence_captured_at_utc=format_utc(evidence["captured_at"]),
        revision_matches_runner=revision_matches,
        thesis_ready=validated["thesis_ready"],
        observed_models=tuple((name, observed_models[name]) for name in EXPECTED_MODEL_FIELDS),
        verified_at_utc=format_utc(verified_at),
    )
