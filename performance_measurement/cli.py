from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from performance_measurement.backfill import run_backfill_measurement
from performance_measurement.common import MeasurementError, load_json_object, sha256_file
from performance_measurement.prometheus import collect_resources
from performance_measurement.reporting import write_report
from performance_measurement.search_latency import load_queries, run_search_measurement


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _token_from_env(config: dict, section: str, *, required: bool) -> str | None:
    section_config = config.get(section)
    if not isinstance(section_config, dict):
        raise MeasurementError(f"{section} must be a JSON object")
    variable = section_config.get("api_token_env")
    if variable is None and not required:
        return None
    if not isinstance(variable, str) or not variable.strip():
        raise MeasurementError(f"{section}.api_token_env must be a nonblank environment variable name")
    token = os.getenv(variable, "").strip()
    if not token:
        raise MeasurementError(f"required API token environment variable is not set: {variable}")
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="repo-search runtime-performance measurement tools")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="measure sequential search latency")
    search.add_argument("--config", required=True)
    search.add_argument("--queries", required=True)
    search.add_argument("--cold-evidence")
    search.add_argument("--output-dir", required=True)
    search.add_argument("--overwrite", action="store_true")
    resources = commands.add_parser("resources", help="collect configured Prometheus metrics")
    resources.add_argument("--config", required=True)
    resources.add_argument("--output-dir", required=True)
    resources.add_argument("--overwrite", action="store_true")
    backfill = commands.add_parser("backfill", help="create and poll an embedding-backfill job")
    backfill.add_argument("--config", required=True)
    backfill.add_argument("--output-dir", required=True)
    backfill.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_json_object(args.config, "measurement config")
        config_hash = sha256_file(args.config)
        git_commit = _git_commit()
        token = None
        if args.command == "search":
            token = _token_from_env(config, "search", required=True)
            evidence = load_json_object(args.cold_evidence, "cold evidence") if args.cold_evidence else None
            report = run_search_measurement(
                config,
                load_queries(args.queries),
                api_token=token or "",
                git_commit=git_commit,
                config_sha256=config_hash,
                query_sha256=sha256_file(args.queries),
                cold_evidence=evidence,
                cold_evidence_sha256=sha256_file(args.cold_evidence) if args.cold_evidence else None,
            )
        elif args.command == "resources":
            token = _token_from_env(config, "prometheus", required=False)
            report = collect_resources(
                config,
                api_token=token,
                git_commit=git_commit,
                config_sha256=config_hash,
            )
        else:
            token = _token_from_env(config, "backfill", required=True)
            report = run_backfill_measurement(
                config,
                api_token=token or "",
                git_commit=git_commit,
                config_sha256=config_hash,
            )
        write_report(args.output_dir, report, overwrite=args.overwrite, secrets=[token] if token else [])
    except MeasurementError as exc:
        raise SystemExit(str(exc)) from None
    print(Path(args.output_dir))
    if args.command == "backfill" and report["job"]["status"] != "succeeded":
        return 2
    return 0
