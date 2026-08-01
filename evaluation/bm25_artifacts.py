from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.adapters import BM25BaselineAdapter, bm25_metadata
from evaluation.collector import FINAL_METHODS, write_runs_atomically
from evaluation.io import (
    load_queries,
    load_runs,
    read_json,
    sha256_file,
    validate_comparison_matrix,
    write_json,
)
from evaluation.judgment_import import POOL_COLUMNS
from evaluation.models import EvaluationQuery, QueryRun
from evaluation.pooling import build_candidate_pool


FROZEN_METHODS = {"keyword", "vector_only", "full_pipeline"}


def _require_hash(path: str | Path, expected: str, *, label: str) -> str:
    observed = sha256_file(path)
    if observed.lower() != expected.lower():
        raise ValueError(f"{label} hash mismatch: expected {expected.lower()}, observed {observed}")
    return observed


def load_frozen_snapshot(path: str | Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("snapshot_format") != "repo-search-corpus-v1":
        raise ValueError("unsupported frozen corpus snapshot format")
    publications = payload.get("publications")
    if not isinstance(publications, list) or not publications:
        raise ValueError("frozen corpus snapshot must contain publications")
    normalized = []
    identifiers = set()
    for publication in publications:
        if not isinstance(publication, dict):
            raise ValueError("frozen corpus snapshot contains an invalid publication")
        identifier = publication.get("publication_id")
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier):
            raise ValueError("frozen corpus snapshot contains an invalid publication ID")
        identifier = str(identifier)
        if identifier in identifiers:
            raise ValueError("frozen corpus snapshot contains duplicate publication IDs")
        identifiers.add(identifier)
        normalized.append({**publication, "id": identifier})
    return normalized


def validate_frozen_runs(
    runs: list[QueryRun],
    queries: list[EvaluationQuery],
    publications: list[dict[str, Any]],
) -> None:
    query_ids = {query.query_id for query in queries}
    validate_comparison_matrix(runs, query_ids, FROZEN_METHODS)
    corpus = {str(publication["id"]): publication for publication in publications}
    for run in runs:
        for item in run.results:
            publication = corpus.get(item.publication_id)
            if publication is None:
                raise ValueError("frozen run refers to a publication outside the frozen corpus")
            for field in ("title", "abstract", "source_url"):
                if getattr(item, field) != publication.get(field):
                    raise ValueError(
                        f"frozen run metadata mismatch for publication {item.publication_id!r}: {field}"
                    )


async def build_bm25_runs(
    queries: list[EvaluationQuery],
    publications: list[dict[str, Any]],
    *,
    limit: int,
) -> list[QueryRun]:
    if limit <= 0 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    adapter = BM25BaselineAdapter(publications)
    return [await adapter.retrieve(query, limit) for query in queries]


def build_artifacts(
    *,
    queries_path: str | Path,
    corpus_snapshot_path: str | Path,
    frozen_runs_path: str | Path,
    output_directory: str | Path,
    expected_queries_sha256: str,
    expected_corpus_sha256: str,
    expected_frozen_runs_sha256: str,
    limit: int = 20,
    depth: int = 10,
    seed: int = 2026,
) -> Path:
    if depth <= 0:
        raise ValueError("depth must be positive")
    query_hash = _require_hash(queries_path, expected_queries_sha256, label="query set")
    corpus_hash = _require_hash(corpus_snapshot_path, expected_corpus_sha256, label="corpus snapshot")
    frozen_runs_hash = _require_hash(frozen_runs_path, expected_frozen_runs_sha256, label="frozen runs")
    queries = load_queries(queries_path)
    publications = load_frozen_snapshot(corpus_snapshot_path)
    frozen_runs = load_runs(frozen_runs_path, {query.query_id for query in queries}, FROZEN_METHODS)
    validate_frozen_runs(frozen_runs, queries, publications)
    bm25_runs = asyncio.run(build_bm25_runs(queries, publications, limit=limit))
    frozen_by_pair = {(run.query_id, run.method): run for run in frozen_runs}
    bm25_by_query = {run.query_id: run for run in bm25_runs}
    combined = [
        bm25_by_query[query.query_id]
        if method == "bm25"
        else frozen_by_pair[(query.query_id, method)]
        for query in queries
        for method in FINAL_METHODS
    ]
    validate_comparison_matrix(combined, {query.query_id for query in queries}, set(FINAL_METHODS))

    output = Path(output_directory)
    if output.exists():
        raise ValueError(f"output already exists: {output.name}")
    output.mkdir(parents=True)
    write_runs_atomically(output / "bm25-runs.json", bm25_runs, queries, ["bm25"])
    write_runs_atomically(output / "runs.json", combined, queries, FINAL_METHODS)
    candidates = build_candidate_pool(
        combined,
        depth,
        seed,
        {query.query_id: query.text for query in queries},
    )
    with (output / "candidates.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(POOL_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(candidates)
    write_json(
        output / "metadata.json",
        {
            "methods": list(FINAL_METHODS),
            "bm25": bm25_metadata(),
            "corpus_size": len(publications),
            "query_count": len(queries),
            "corpus_snapshot_sha256": corpus_hash,
            "query_set_sha256": query_hash,
            "frozen_runs_sha256": frozen_runs_hash,
            "top_k": limit,
            "pool_depth": depth,
            "pool_seed": seed,
            "run_timestamp_utc": datetime.now(UTC).isoformat(),
            "description": "Reproducible Solr/Lucene-style lexical baseline over the frozen local corpus",
        },
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build isolated BM25 final-evaluation artifacts")
    parser.add_argument("--queries", required=True)
    parser.add_argument("--corpus-snapshot", required=True)
    parser.add_argument("--frozen-runs", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-queries-sha256", required=True)
    parser.add_argument("--expected-corpus-sha256", required=True)
    parser.add_argument("--expected-frozen-runs-sha256", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    output = build_artifacts(
        queries_path=args.queries,
        corpus_snapshot_path=args.corpus_snapshot,
        frozen_runs_path=args.frozen_runs,
        output_directory=args.output_dir,
        expected_queries_sha256=args.expected_queries_sha256,
        expected_corpus_sha256=args.expected_corpus_sha256,
        expected_frozen_runs_sha256=args.expected_frozen_runs_sha256,
        limit=args.limit,
        depth=args.depth,
        seed=args.seed,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
