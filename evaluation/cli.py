from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import subprocess
from pathlib import Path

from evaluation.agreement import compare_judgments, write_agreement_report
from evaluation.io import (
    load_judgments,
    load_queries,
    load_query_metadata,
    load_runs,
    sha256_file,
    validate_comparison_matrix,
    write_json,
)
from evaluation.judgment_import import POOL_COLUMNS
from evaluation.judgment_import import import_judgments
from evaluation.collector import CollectorError, EvaluationServiceClient, run_collection, validate_methods
from evaluation.pooling import build_candidate_pool
from evaluation.reporting import build_report, write_report
from microservices.common.embedding_provenance import (
    DEFAULT_EMBEDDING_MODEL_REVISION,
    DOCUMENT_TEMPLATE_VERSION,
)


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_candidates(path: Path, candidates: list[dict]) -> None:
    if path.suffix.lower() == ".csv":
        fieldnames = list(POOL_COLUMNS)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(candidates)
    else:
        write_json(path, {"candidates": candidates})


def _reject_output_alias(
    output: str | Path,
    inputs: list[str | Path],
    *,
    output_is_directory: bool = False,
) -> None:
    resolved_output = Path(output).resolve()
    for input_path in inputs:
        resolved_input = Path(input_path).resolve()
        if resolved_output == resolved_input or (
            output_is_directory and resolved_output in resolved_input.parents
        ):
            raise ValueError("output path must not replace or contain an input file")


def _validate_evaluation_methods(methods: list[str]) -> None:
    try:
        validate_methods(methods)
    except CollectorError as exc:
        raise ValueError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="repo-search retrieval evaluation tools")
    commands = parser.add_subparsers(dest="command", required=True)

    pool = commands.add_parser("candidate-pool", help="combine and blind top retrieval candidates")
    pool.add_argument("--queries", required=True)
    pool.add_argument("--runs", required=True)
    pool.add_argument("--output", required=True)
    pool.add_argument("--depth", type=int, default=10)
    pool.add_argument("--seed", type=int, default=2026)
    pool.add_argument("--methods", nargs="+", default=["keyword", "vector_only", "full_pipeline"])

    report = commands.add_parser("report", help="calculate metrics and write JSON, CSV, and Markdown")
    report.add_argument("--queries", required=True)
    report.add_argument("--query-metadata", required=True)
    report.add_argument("--judgments", required=True)
    report.add_argument("--runs", required=True)
    report.add_argument("--output-dir", required=True)
    report.add_argument("--corpus-size", required=True, type=int)
    report.add_argument("--k", nargs="+", type=int, default=[5, 10])
    report.add_argument("--embedding-model", required=True)
    report.add_argument("--embedding-model-revision")
    report.add_argument("--embedding-template-version")
    report.add_argument("--methods", nargs="+", default=["keyword", "vector_only", "full_pipeline"])
    report.add_argument("--ranking-config", default="{}", help="JSON object or path to a JSON file")
    report.add_argument("--git-commit")
    report.add_argument("--overwrite", action="store_true")

    import_command = commands.add_parser(
        "import-judgments", help="validate a completed blinded assessment and write judgments JSON"
    )
    import_command.add_argument("--queries", required=True)
    import_command.add_argument("--pool-template", required=True)
    import_command.add_argument("--assessment", required=True)
    import_command.add_argument("--output", required=True)
    import_command.add_argument("--overwrite", action="store_true")

    agreement = commands.add_parser("agreement", help="compare two assessor judgment files")
    agreement.add_argument("--judgments-a", required=True)
    agreement.add_argument("--judgments-b", required=True)
    agreement.add_argument("--output-dir", required=True)
    agreement.add_argument("--overwrite", action="store_true")

    collect = commands.add_parser("collect-runs", help="collect keyword, vector-only, and full-pipeline runs")
    collect.add_argument("--queries", required=True)
    collect.add_argument("--output", required=True)
    collect.add_argument("--methods", nargs="+", default=["keyword", "vector_only", "full_pipeline"])
    collect.add_argument("--limit", type=int, default=20)
    collect.add_argument("--database-url-env", default="EVALUATION_DATABASE_URL")
    collect.add_argument("--api-token-env", default="EVALUATION_API_TOKEN")
    collect.add_argument("--embedding-service-url", required=True)
    collect.add_argument("--full-pipeline-url", required=True)
    collect.add_argument("--request-timeout", type=float, default=180.0)
    collect.add_argument("--embedding-model", required=True)
    collect.add_argument("--embedding-model-revision", default=DEFAULT_EMBEDDING_MODEL_REVISION)
    collect.add_argument("--embedding-template-version", default=DOCUMENT_TEMPLATE_VERSION)
    collect.add_argument("--expected-corpus-size", type=int, required=True)
    collect.add_argument("--expected-snapshot-hash", required=True)
    collect.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect-runs":
        try:
            queries = load_queries(args.queries)
            validate_methods(args.methods)
        except (CollectorError, ValueError) as exc:
            raise SystemExit(str(exc)) from None
        if args.limit <= 0 or args.limit > 50:
            raise SystemExit("--limit must be between 1 and 50")
        if args.expected_corpus_size <= 0:
            raise SystemExit("--expected-corpus-size must be positive")
        if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
            raise SystemExit("--request-timeout must be finite and positive")
        if Path(args.output).exists() and not args.overwrite:
            raise SystemExit(f"output already exists: {Path(args.output).name}")
        database_url = os.getenv(args.database_url_env, "").strip()
        api_token = os.getenv(args.api_token_env, "").strip()
        if not database_url:
            raise SystemExit(f"required database environment variable is not set: {args.database_url_env}")
        if not api_token:
            raise SystemExit(f"required API token environment variable is not set: {args.api_token_env}")
        import psycopg2

        service_client = EvaluationServiceClient(
            embedding_service_url=args.embedding_service_url,
            full_pipeline_url=args.full_pipeline_url,
            api_token=api_token,
            timeout_seconds=args.request_timeout,
            expected_embedding_model=args.embedding_model,
            expected_embedding_model_revision=args.embedding_model_revision,
            expected_embedding_template_version=args.embedding_template_version,
        )
        try:
            asyncio.run(
                run_collection(
                    queries=queries,
                    methods=args.methods,
                    limit=args.limit,
                    output_path=args.output,
                    connection_factory=lambda: psycopg2.connect(
                        database_url,
                        connect_timeout=max(1, min(int(args.request_timeout), 60)),
                    ),
                    expected_corpus_size=args.expected_corpus_size,
                    expected_snapshot_hash=args.expected_snapshot_hash,
                    embedding_model=args.embedding_model,
                    embedding_model_revision=args.embedding_model_revision,
                    embedding_template_version=args.embedding_template_version,
                    service_client=service_client,
                    overwrite=args.overwrite,
                )
            )
        except CollectorError as exc:
            raise SystemExit(str(exc)) from None
        print(Path(args.output))
        return 0
    if args.command == "import-judgments":
        inputs = [Path(args.queries), Path(args.pool_template), Path(args.assessment)]
        _reject_output_alias(args.output, inputs)
        import_judgments(
            load_queries(args.queries),
            args.pool_template,
            args.assessment,
            args.output,
            overwrite=args.overwrite,
        )
        return 0
    if args.command == "agreement":
        if Path(args.judgments_a).resolve() == Path(args.judgments_b).resolve():
            raise ValueError("assessor A and assessor B must be distinct source files")
        _reject_output_alias(
            args.output_dir,
            [args.judgments_a, args.judgments_b],
            output_is_directory=True,
        )
        judgments_a = load_judgments(args.judgments_a)
        judgments_b = load_judgments(args.judgments_b)
        report = compare_judgments(
            judgments_a,
            judgments_b,
            git_commit=_git_commit(),
            input_sha256={
                "judgments_a": sha256_file(args.judgments_a),
                "judgments_b": sha256_file(args.judgments_b),
            },
        )
        write_agreement_report(args.output_dir, report, overwrite=args.overwrite)
        return 0
    if args.command == "candidate-pool":
        queries = load_queries(args.queries)
        query_texts = {query.query_id: query.text for query in queries}
        _validate_evaluation_methods(args.methods)
        expected_methods = set(args.methods)
        if len(expected_methods) != len(args.methods):
            raise ValueError("expected methods must be unique")
        runs = load_runs(args.runs, set(query_texts), expected_methods)
        validate_comparison_matrix(runs, set(query_texts), expected_methods)
        _write_candidates(
            Path(args.output),
            build_candidate_pool(runs, args.depth, args.seed, query_texts),
        )
        return 0

    queries = load_queries(args.queries)
    query_ids = {query.query_id for query in queries}
    query_metadata = load_query_metadata(args.query_metadata, query_ids)
    _validate_evaluation_methods(args.methods)
    expected_methods = set(args.methods)
    if len(expected_methods) != len(args.methods):
        raise ValueError("expected methods must be unique")
    ranking_argument = Path(args.ranking_config)
    ranking_config = (
        json.loads(ranking_argument.read_text(encoding="utf-8"))
        if ranking_argument.is_file()
        else json.loads(args.ranking_config)
    )
    runs = load_runs(args.runs, query_ids, expected_methods)
    validate_comparison_matrix(runs, query_ids, expected_methods)
    report = build_report(
        runs,
        load_judgments(args.judgments, query_ids),
        query_metadata,
        git_commit=args.git_commit or _git_commit(),
        corpus_size=args.corpus_size,
        k_values=args.k,
        embedding_model=args.embedding_model,
        embedding_model_revision=args.embedding_model_revision,
        embedding_template_version=args.embedding_template_version,
        ranking_configuration=ranking_config,
        input_sha256={
            "queries": sha256_file(args.queries),
            "query_metadata": sha256_file(args.query_metadata),
            "judgments": sha256_file(args.judgments),
            "runs": sha256_file(args.runs),
            **(
                {"ranking_configuration": sha256_file(ranking_argument)}
                if ranking_argument.is_file()
                else {}
            ),
        },
    )
    report_inputs = [args.queries, args.query_metadata, args.judgments, args.runs]
    if ranking_argument.is_file():
        report_inputs.append(ranking_argument)
    _reject_output_alias(args.output_dir, report_inputs, output_is_directory=True)
    write_report(args.output_dir, report, overwrite=args.overwrite)
    return 0
