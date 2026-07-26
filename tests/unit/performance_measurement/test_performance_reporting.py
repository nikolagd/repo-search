from __future__ import annotations

import hashlib
import os

import pytest

import performance_measurement.reporting as reporting_module
from performance_measurement.common import MeasurementError
from performance_measurement.reporting import write_report


pytestmark = pytest.mark.unit


def _report():
    return {
        "metadata": {
            "measurement_type": "search_latency",
            "deployment_label": "compose",
            "git_commit": "abc",
            "started_at_utc": "2026-01-01T00:00:00Z",
            "finished_at_utc": "2026-01-01T00:01:00Z",
            "percentile_convention": "nearest-rank",
        },
        "summary": {
            "attempted_sample_count": 1,
            "sample_count": 1,
            "failed_sample_count": 0,
            "mean_ms": 1.0,
            "median_ms": 1.0,
            "minimum_ms": 1.0,
            "maximum_ms": 1.0,
            "p50_ms": 1.0,
            "p95_ms": 1.0,
        },
        "summary_by_classification": [
            {
                "classification": "warm",
                "attempted_sample_count": 1,
                "sample_count": 1,
                "failed_sample_count": 0,
                "p50_ms": 1.0,
                "p95_ms": 1.0,
            }
        ],
        "samples": [{"query_id": "q", "latency_ms": 1.0, "labels": {"a": "b"}}],
    }


def test_output_is_atomic_protected_and_has_verifiable_hashes(tmp_path) -> None:
    output = tmp_path / "run"
    write_report(output, _report())
    assert {path.name for path in output.iterdir()} == {"measurement.json", "samples.csv", "summary.md", "SHA256SUMS"}
    manifest = {}
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        manifest[name] = digest
    assert manifest == {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in ("measurement.json", "samples.csv", "summary.md")
    }
    with pytest.raises(MeasurementError, match="already exists"):
        write_report(output, _report())


def test_secret_is_rejected_and_not_published(tmp_path) -> None:
    report = _report()
    report["samples"][0]["error"] = "sentinel-token"
    output = tmp_path / "run"
    with pytest.raises(MeasurementError, match="secret") as error:
        write_report(output, report, secrets=["sentinel-token"])
    assert "sentinel-token" not in str(error.value)
    assert not output.exists()
    assert not list(tmp_path.glob(".run.*.tmp"))


def test_existing_output_is_restored_if_atomic_replace_fails(tmp_path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    marker = output / "old.txt"
    marker.write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = []

    def fail_publish(source, destination):
        calls.append((source, destination))
        if len(calls) == 2:
            raise OSError("synthetic publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(reporting_module.os, "replace", fail_publish)
    with pytest.raises(OSError, match="synthetic"):
        write_report(output, _report(), overwrite=True)
    assert marker.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".run.*.tmp"))
    assert not list(tmp_path.glob(".run.*.backup"))
