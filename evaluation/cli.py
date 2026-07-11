from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

from evaluation.io import load_judgments, load_queries, load_runs, validate_comparison_matrix, write_json
from evaluation.pooling import build_candidate_pool
from evaluation.reporting import build_report, write_report


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_candidates(path: Path, candidates: list[dict]) -> None:
    if path.suffix.lower() == ".csv":
        fieldnames = [
            "candidate_id",
            "query_text",
            "query_id",
            "publication_id",
            "title",
            "abstract",
            "source_url",
            "relevance",
        ]
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(candidates)
    else:
        write_json(path, {"candidates": candidates})


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
    report.add_argument("--judgments", required=True)
    report.add_argument("--runs", required=True)
    report.add_argument("--output-dir", required=True)
    report.add_argument("--corpus-size", required=True, type=int)
    report.add_argument("--k", nargs="+", type=int, default=[5, 10])
    report.add_argument("--embedding-model", required=True)
    report.add_argument("--methods", nargs="+", default=["keyword", "vector_only", "full_pipeline"])
    report.add_argument("--ranking-config", default="{}", help="JSON object or path to a JSON file")
    report.add_argument("--git-commit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "candidate-pool":
        queries = load_queries(args.queries)
        query_texts = {query.query_id: query.text for query in queries}
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
        git_commit=args.git_commit or _git_commit(),
        corpus_size=args.corpus_size,
        query_count=len(queries),
        k_values=args.k,
        embedding_model=args.embedding_model,
        ranking_configuration=ranking_config,
    )
    write_report(args.output_dir, report)
    return 0
