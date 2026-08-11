from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from evaluation.adapters import (
    LANGUAGE_INDEPENDENT_LEXICAL_METHOD,
    LanguageIndependentLexicalAdapter,
    language_independent_lexical_metadata,
)
from evaluation.bm25_artifacts import (
    FROZEN_METHODS,
    _require_hash,
    load_frozen_snapshot,
    validate_frozen_runs,
)
from evaluation.collector import write_runs_atomically
from evaluation.io import (
    load_queries,
    load_runs,
    sha256_file,
    validate_comparison_matrix,
    write_json,
)
from evaluation.judgment_import import POOL_COLUMNS
from evaluation.models import EvaluationQuery, QueryRun
from evaluation.pooling import build_candidate_pool


FINAL_CANDIDATE_METHODS = (
    LANGUAGE_INDEPENDENT_LEXICAL_METHOD,
    "vector_only",
    "full_pipeline",
)
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{40}")


def _validate_commit(value: str, *, label: str) -> str:
    if not COMMIT_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a full 40-character Git commit")
    return value.lower()


def _record_hash(runs: list[QueryRun], method: str) -> str:
    records = [run.record() for run in runs if run.method == method]
    payload = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def build_language_independent_lexical_runs(
    queries: list[EvaluationQuery],
    publications: list[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[QueryRun], LanguageIndependentLexicalAdapter, float]:
    if type(limit) is not int or limit <= 0 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    started = time.perf_counter()
    adapter = LanguageIndependentLexicalAdapter(publications)
    build_runtime_ms = (time.perf_counter() - started) * 1000
    runs = [await adapter.retrieve(query, limit) for query in queries]
    return runs, adapter, build_runtime_ms


def build_artifacts(
    *,
    queries_path: str | Path,
    corpus_snapshot_path: str | Path,
    frozen_runs_path: str | Path,
    output_directory: str | Path,
    expected_queries_sha256: str,
    expected_corpus_sha256: str,
    expected_frozen_runs_sha256: str,
    source_commit: str,
    starting_commit: str,
    limit: int = 20,
    depth: int = 5,
    seed: int = 2026,
    generated_at: str | None = None,
) -> Path:
    if type(depth) is not int or depth <= 0:
        raise ValueError("depth must be a positive integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    source_commit = _validate_commit(source_commit, label="source commit")
    starting_commit = _validate_commit(starting_commit, label="starting commit")
    query_hash = _require_hash(queries_path, expected_queries_sha256, label="query set")
    corpus_hash = _require_hash(corpus_snapshot_path, expected_corpus_sha256, label="corpus snapshot")
    frozen_runs_hash = _require_hash(
        frozen_runs_path,
        expected_frozen_runs_sha256,
        label="frozen runs",
    )

    queries = load_queries(queries_path)
    publications = load_frozen_snapshot(corpus_snapshot_path)
    frozen_runs = load_runs(
        frozen_runs_path,
        {query.query_id for query in queries},
        FROZEN_METHODS,
    )
    validate_frozen_runs(frozen_runs, queries, publications)
    selected_runs, adapter, build_runtime_ms = asyncio.run(
        build_language_independent_lexical_runs(queries, publications, limit=limit)
    )
    frozen_by_pair = {(run.query_id, run.method): run for run in frozen_runs}
    selected_by_query = {run.query_id: run for run in selected_runs}
    combined = [
        selected_by_query[query.query_id]
        if method == LANGUAGE_INDEPENDENT_LEXICAL_METHOD
        else frozen_by_pair[(query.query_id, method)]
        for query in queries
        for method in FINAL_CANDIDATE_METHODS
    ]
    query_ids = {query.query_id for query in queries}
    validate_comparison_matrix(combined, query_ids, set(FINAL_CANDIDATE_METHODS))

    output = Path(output_directory)
    if output.exists():
        raise ValueError(f"output already exists: {output.name}")
    output.mkdir(parents=True)
    selected_path = output / "language-independent-lexical-runs.json"
    combined_path = output / "runs.json"
    candidates_path = output / "candidates.csv"
    write_runs_atomically(
        selected_path,
        selected_runs,
        queries,
        [LANGUAGE_INDEPENDENT_LEXICAL_METHOD],
    )
    write_runs_atomically(combined_path, combined, queries, FINAL_CANDIDATE_METHODS)

    reloaded = load_runs(combined_path, query_ids, set(FINAL_CANDIDATE_METHODS))
    for method in ("vector_only", "full_pipeline"):
        if _record_hash(reloaded, method) != _record_hash(frozen_runs, method):
            raise ValueError(f"reused {method} run records changed during artifact generation")

    candidates = build_candidate_pool(
        combined,
        depth,
        seed,
        {query.query_id: query.text for query in queries},
    )
    with candidates_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(POOL_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(candidates)

    latencies = [run.latency_ms for run in selected_runs if run.latency_ms is not None]
    timestamp = generated_at or datetime.now(UTC).isoformat()
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("generated_at must be an ISO-8601 timestamp") from exc
    write_json(
        output / "metadata.json",
        {
            "artifact_schema_version": 1,
            "methods": list(FINAL_CANDIDATE_METHODS),
            "selected_language_independent_lexical": language_independent_lexical_metadata(
                index_statistics=adapter.index_statistics
            ),
            "source_commit": source_commit,
            "starting_commit": starting_commit,
            "source_branch": "feat/multilingual-lexical-baseline",
            "corpus_size": len(publications),
            "query_count": len(queries),
            "corpus_snapshot_sha256": corpus_hash,
            "query_set_sha256": query_hash,
            "frozen_runs_sha256": frozen_runs_hash,
            "reused_run_record_sha256": {
                method: _record_hash(frozen_runs, method)
                for method in ("vector_only", "full_pipeline")
            },
            "generated_artifact_sha256": {
                "language_independent_lexical_runs": sha256_file(selected_path),
                "combined_runs": sha256_file(combined_path),
                "candidate_pool": sha256_file(candidates_path),
            },
            "top_k": limit,
            "pool_depth": depth,
            "pool_seed": seed,
            "candidate_pool_row_count": len(candidates),
            "runtime": {
                "index_build_ms": build_runtime_ms,
                "query_latency_ms_total": sum(latencies),
                "query_latency_ms_median": median(latencies) if latencies else None,
                "query_latency_definition": "local lexical analysis, scoring, ranking, and RRF only",
            },
            "generation_timestamp_utc": timestamp,
            "judgment_policy": {
                "transfer_key": ["query_id", "publication_id"],
                "pool_blinded": True,
                "automatic_relevance_labels": False,
                "effectiveness_metrics_deferred_until_pool_complete": True,
            },
            "description": (
                "Language-independent strictly lexical baseline for Serbian/English records "
                "using word and character "
                "4-gram BM25 with fixed reciprocal-rank fusion"
            ),
        },
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="build isolated language-independent lexical evaluation artifacts"
    )
    parser.add_argument("--queries", required=True)
    parser.add_argument("--corpus-snapshot", required=True)
    parser.add_argument("--frozen-runs", required=True)
    parser.add_argument("--output-directory", "--output-dir", dest="output_directory", required=True)
    parser.add_argument("--expected-queries-sha256", required=True)
    parser.add_argument("--expected-corpus-sha256", required=True)
    parser.add_argument("--expected-frozen-runs-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--starting-commit", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    output = build_artifacts(
        queries_path=args.queries,
        corpus_snapshot_path=args.corpus_snapshot,
        frozen_runs_path=args.frozen_runs,
        output_directory=args.output_directory,
        expected_queries_sha256=args.expected_queries_sha256,
        expected_corpus_sha256=args.expected_corpus_sha256,
        expected_frozen_runs_sha256=args.expected_frozen_runs_sha256,
        source_commit=args.source_commit,
        starting_commit=args.starting_commit,
        limit=args.limit,
        depth=args.depth,
        seed=args.seed,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
