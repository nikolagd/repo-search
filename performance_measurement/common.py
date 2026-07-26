from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any
from urllib.parse import parse_qsl, urlsplit


PERCENTILE_CONVENTION = "nearest-rank: sorted_values[ceil(p*n)-1]"
_ALLOWED_SECRET_REFERENCE_KEYS = {"api_token_env"}
_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "bearer_token",
)
_SENSITIVE_QUERY_PARTS = (
    "password",
    "secret",
    "credential",
    "authorization",
    "token",
    "api_key",
    "apikey",
    "key",
)


class MeasurementError(ValueError):
    """Raised when measurement inputs or observed data are unsafe or invalid."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MeasurementError("UTC timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MeasurementError(f"{field} must be a nonblank timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MeasurementError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MeasurementError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeasurementError(f"{label} is not readable valid JSON") from exc
    if not isinstance(payload, dict):
        raise MeasurementError(f"{label} must contain a JSON object")
    return payload


def nearest_rank_percentile(values: list[float], percentile: float) -> float | None:
    if not 0 < percentile <= 1:
        raise MeasurementError("percentile must be in the interval (0, 1]")
    if not values:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise MeasurementError("percentile samples must be finite numbers")
    ordered = sorted(float(value) for value in values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def summarize_values(values: list[float], *, attempted_count: int | None = None) -> dict[str, Any]:
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise MeasurementError("summary samples must be finite numbers")
    converted = [float(value) for value in values]
    attempted = len(converted) if attempted_count is None else attempted_count
    if isinstance(attempted, bool) or not isinstance(attempted, int) or attempted < len(converted):
        raise MeasurementError("attempted sample count is invalid")
    return {
        "attempted_sample_count": attempted,
        "sample_count": len(converted),
        "failed_sample_count": attempted - len(converted),
        "mean": fmean(converted) if converted else None,
        "median": median(converted) if converted else None,
        "minimum": min(converted) if converted else None,
        "maximum": max(converted) if converted else None,
        "p50": nearest_rank_percentile(converted, 0.50),
        "p95": nearest_rank_percentile(converted, 0.95),
    }


def validate_no_secrets(value: Any, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise MeasurementError(f"{path} keys must be strings")
            normalized = key.casefold().replace("-", "_")
            if normalized not in _ALLOWED_SECRET_REFERENCE_KEYS and any(
                part in normalized for part in _SENSITIVE_KEY_PARTS
            ):
                raise MeasurementError(f"{path} contains a prohibited credential field")
            validate_no_secrets(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            validate_no_secrets(nested, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().casefold().startswith(
        ("http://", "https://", "postgres://", "postgresql://")
    ):
        parsed = urlsplit(value.strip())
        if parsed.username is not None or parsed.password is not None:
            raise MeasurementError(f"{path} must not contain a credential-bearing URL")
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = name.casefold().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_QUERY_PARTS):
                raise MeasurementError(f"{path} must not contain a credential-bearing URL")


def validate_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MeasurementError(f"{field} must be a nonblank URL")
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MeasurementError(f"{field} must be an HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise MeasurementError(f"{field} must not contain credentials")
    if parsed.fragment:
        raise MeasurementError(f"{field} must not contain a URL fragment")
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = name.casefold().replace("-", "_")
        if any(part in normalized for part in _SENSITIVE_QUERY_PARTS):
            raise MeasurementError(f"{field} must not contain credential query parameters")
    return candidate


def require_nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MeasurementError(f"{field} must be a nonblank string")
    return value.strip()


def require_positive_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise MeasurementError(f"{field} must be a {qualifier} integer")
    return value


def require_positive_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise MeasurementError(f"{field} must be finite and positive")
    return float(value)


def validate_common_config(config: dict[str, Any]) -> dict[str, Any]:
    validate_no_secrets(config)
    if set(config) - {"deployment_label", "models", "corpus", "search", "prometheus", "backfill"}:
        raise MeasurementError("measurement configuration contains unsupported top-level fields")
    deployment_label = require_nonblank(config.get("deployment_label"), "deployment_label")
    models = config.get("models")
    if not isinstance(models, dict):
        raise MeasurementError("models must be a JSON object")
    required_model_fields = (
        "embedding_model",
        "embedding_model_revision",
        "embedding_template_version",
        "llm_model",
    )
    if set(models) != set(required_model_fields):
        raise MeasurementError("models must contain exactly the required provenance fields")
    normalized_models = {
        field: require_nonblank(models.get(field), f"models.{field}")
        for field in required_model_fields
    }
    corpus = config.get("corpus")
    normalized_corpus = None
    if corpus is not None:
        if not isinstance(corpus, dict):
            raise MeasurementError("corpus must be a JSON object")
        normalized_corpus = {}
        if "size" in corpus:
            normalized_corpus["size"] = require_positive_int(corpus["size"], "corpus.size", allow_zero=True)
        if "sha256" in corpus:
            digest = require_nonblank(corpus["sha256"], "corpus.sha256").casefold()
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise MeasurementError("corpus.sha256 must be a 64-character hexadecimal digest")
            normalized_corpus["sha256"] = digest
        unknown = set(corpus) - {"size", "sha256"}
        if unknown:
            raise MeasurementError("corpus contains unsupported fields")
    return {
        "deployment_label": deployment_label,
        "models": normalized_models,
        "corpus": normalized_corpus,
    }


def build_metadata(
    config: dict[str, Any],
    *,
    measurement_type: str,
    git_commit: str,
    started_at: datetime,
    finished_at: datetime,
    input_sha256: dict[str, str],
    repetitions: dict[str, int] | None = None,
) -> dict[str, Any]:
    common = validate_common_config(config)
    commit = require_nonblank(git_commit, "git_commit")
    if finished_at < started_at:
        raise MeasurementError("measurement finish timestamp precedes its start timestamp")
    normalized_hashes = {}
    for name, digest in input_sha256.items():
        normalized_name = require_nonblank(name, "input hash name")
        normalized_digest = require_nonblank(digest, f"input_sha256.{normalized_name}").casefold()
        if len(normalized_digest) != 64 or any(character not in "0123456789abcdef" for character in normalized_digest):
            raise MeasurementError("input SHA-256 values must be 64-character hexadecimal digests")
        normalized_hashes[normalized_name] = normalized_digest
    normalized_repetitions = None
    if repetitions is not None:
        normalized_repetitions = {
            require_nonblank(name, "repetition name"): require_positive_int(value, f"repetitions.{name}", allow_zero=True)
            for name, value in repetitions.items()
        }
    metadata = {
        "schema_version": 1,
        "measurement_type": require_nonblank(measurement_type, "measurement_type"),
        "git_commit": commit,
        "started_at_utc": format_utc(started_at),
        "finished_at_utc": format_utc(finished_at),
        "deployment_label": common["deployment_label"],
        "models": common["models"],
        "input_sha256": dict(sorted(normalized_hashes.items())),
        "percentile_convention": PERCENTILE_CONVENTION,
    }
    if common["corpus"] is not None:
        metadata["corpus"] = common["corpus"]
    if normalized_repetitions is not None:
        metadata["repetitions"] = dict(sorted(normalized_repetitions.items()))
    return metadata
