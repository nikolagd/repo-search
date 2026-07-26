from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from performance_measurement.common import MeasurementError


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _contains_secret(payload: str, secrets: list[str]) -> bool:
    return any(secret and secret in payload for secret in secrets)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if value is None:
        return ""
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    if not fieldnames:
        fieldnames = ["no_samples"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _format_metric(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _search_markdown(report: dict[str, Any]) -> list[str]:
    summary = report["summary"]
    lines = [
        "## Search latency",
        "",
        "Warm-up samples are preserved in the raw artifact but excluded from every measured statistic.",
        f"Cold evidence maximum age: {report['metadata']['cold_evidence_max_age_seconds']} seconds.",
        "",
        "| Attempted | Successful | Failed | Mean ms | Median ms | Min ms | Max ms | p50 ms | p95 ms |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        "| "
        + " | ".join(
            _format_metric(summary[key])
            for key in (
                "attempted_sample_count",
                "sample_count",
                "failed_sample_count",
                "mean_ms",
                "median_ms",
                "minimum_ms",
                "maximum_ms",
                "p50_ms",
                "p95_ms",
            )
        )
        + " |",
        "",
        "| Classification | Attempted | Successful | Failed | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["summary_by_classification"]:
        lines.append(
            f"| {row['classification']} | {row['attempted_sample_count']} | {row['sample_count']} | "
            f"{row['failed_sample_count']} | {_format_metric(row['p50_ms'])} | {_format_metric(row['p95_ms'])} |"
        )
    return lines


def _resource_markdown(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Resource metrics",
        "",
        "Unavailable metrics are represented by null summary values; absence is never converted to zero.",
        "",
        "| Name | Type | Availability | Series | Labels | Samples | Unit | Mean | Min | Max | p95 | Reason |",
        "|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in report["metric_summaries"]:
        lines.append(
            f"| {row['name']} | {row['metric_type']} | {row['availability']} | {_format_metric(row['series_count'])} | "
            f"{json.dumps(row['accepted_labels'], sort_keys=True) if row['accepted_labels'] is not None else 'unavailable'} | "
            f"{row['sample_count']} | {row['unit']} | {_format_metric(row['mean'])} | {_format_metric(row['minimum'])} | "
            f"{_format_metric(row['maximum'])} | {_format_metric(row['p95'])} | {row['unavailable_reason'] or ''} |"
        )
    return lines


def _backfill_markdown(report: dict[str, Any]) -> list[str]:
    job = report["job"]
    return [
        "## Embedding backfill",
        "",
        "The measurement records the existing Job Service lifecycle. It does not create stale corpus data.",
        "",
        f"- Job ID: `{job['id']}`",
        f"- Status: `{job['status']}`",
        f"- Queued at UTC: `{job['queued_at_utc']}`",
        f"- Started at UTC: `{job['started_at_utc'] or 'unavailable'}`",
        f"- Finished at UTC: `{job['finished_at_utc'] or 'unavailable'}`",
        f"- Attempts: {job['attempts']}",
        f"- Processed records: {_format_metric(job['processed_records'])}",
        f"- Service duration seconds: {_format_metric(job['service_duration_seconds'])}",
        f"- Observed duration seconds: {_format_metric(job['observed_duration_seconds'])}",
        f"- Records per second: {_format_metric(job['records_per_second'])}",
    ]


def _render_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    deployment = metadata["verified_deployment_identity"]
    observed_models = metadata["observed_runtime_model_identity"]
    lines = [
        "# Runtime Performance Measurement",
        "",
        f"- Measurement type: `{metadata['measurement_type']}`",
        f"- Deployment: `{metadata['deployment_label']}`",
        f"- Runner Git commit: `{metadata['runner_git_commit']}`",
        f"- Verified deployment Git revision: `{deployment['deployment_git_revision']}`",
        f"- Runtime kind: `{deployment['runtime_kind']}`",
        f"- Thesis-ready identity: `{deployment['thesis_ready']}`",
        f"- Runtime identity verified UTC: `{observed_models['verified_at_utc']}`",
        f"- Started UTC: `{metadata['started_at_utc']}`",
        f"- Finished UTC: `{metadata['finished_at_utc']}`",
        f"- Percentiles: `{metadata['percentile_convention']}`",
        "",
    ]
    renderers = {
        "search_latency": _search_markdown,
        "resources": _resource_markdown,
        "embedding_backfill": _backfill_markdown,
    }
    try:
        lines.extend(renderers[metadata["measurement_type"]](report))
    except KeyError as exc:
        raise MeasurementError("unsupported measurement report type") from exc
    lines.extend(
        [
            "",
            "This artifact is measurement evidence only after its environment and inputs are independently reviewed.",
        ]
    )
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report_files(output: Path, report: dict[str, Any], secrets: list[str]) -> None:
    payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
    markdown = _render_markdown(report)
    samples = report.get("samples")
    if not isinstance(samples, list) or any(not isinstance(row, dict) for row in samples):
        raise MeasurementError("measurement report samples must be an array of objects")
    if _contains_secret(payload + markdown, secrets):
        raise MeasurementError("measurement output contains a configured secret")
    (output / "measurement.json").write_text(payload, encoding="utf-8")
    _write_csv(output / "samples.csv", samples)
    (output / "summary.md").write_text(markdown, encoding="utf-8")
    for path in (output / "samples.csv",):
        if _contains_secret(path.read_text(encoding="utf-8"), secrets):
            raise MeasurementError("measurement output contains a configured secret")
    artifact_names = ["measurement.json", "samples.csv", "summary.md"]
    manifest = "".join(f"{_sha256(output / name)}  {name}\n" for name in artifact_names)
    (output / "SHA256SUMS").write_text(manifest, encoding="utf-8")


def write_report(
    output_directory: str | Path,
    report: dict[str, Any],
    *,
    overwrite: bool = False,
    secrets: list[str] | None = None,
) -> None:
    output = Path(output_directory)
    if output.exists() and not overwrite:
        raise MeasurementError(f"output directory already exists: {output.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temporary = output.with_name(f".{output.name}.{token}.tmp")
    backup = output.with_name(f".{output.name}.{token}.backup")
    temporary.mkdir()
    published = False
    try:
        _write_report_files(temporary, report, secrets or [])
        if output.exists():
            if not overwrite:
                raise MeasurementError(f"output directory already exists: {output.name}")
            os.replace(output, backup)
        try:
            os.replace(temporary, output)
            published = True
        except Exception:
            if backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            _remove_path(backup)
    finally:
        if temporary.exists():
            _remove_path(temporary)
        if backup.exists() and not published:
            if not output.exists():
                os.replace(backup, output)
            else:
                _remove_path(backup)
