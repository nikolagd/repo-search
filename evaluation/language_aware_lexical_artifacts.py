from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import random
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from evaluation.adapters import (
    LANGUAGE_AWARE_LEXICAL_METHOD,
    LANGUAGE_INDEPENDENT_LEXICAL_METHOD,
    LanguageAwareLexicalAdapter,
    language_aware_lexical_metadata,
)
from evaluation.bm25_artifacts import _require_hash, load_frozen_snapshot
from evaluation.collector import write_runs_atomically
from evaluation.io import (
    load_judgments,
    load_queries,
    load_query_metadata,
    load_runs,
    read_json,
    sha256_file,
    validate_comparison_matrix,
    write_json,
)
from evaluation.judgment_transfer import transfer_judgments
from evaluation.models import EvaluationQuery, QueryMetadata, QueryRun


REUSED_METHODS = (
    LANGUAGE_INDEPENDENT_LEXICAL_METHOD,
    "vector_only",
    "full_pipeline",
)
EXTENDED_FINAL_METHODS = (
    LANGUAGE_INDEPENDENT_LEXICAL_METHOD,
    LANGUAGE_AWARE_LEXICAL_METHOD,
    "vector_only",
    "full_pipeline",
)
LANGUAGE_AWARE_POOL_COLUMNS = (
    "candidate_id",
    "query_text",
    "query_id",
    "publication_id",
    "title",
    "abstract",
    "year",
    "source_url",
    "relevance",
)
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
YEAR_PATTERN = re.compile(r"(?<!\d)(\d{4})(?!\d)")


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


def _publication_year(publication: dict[str, Any]) -> int | None:
    value = publication.get("date")
    match = YEAR_PATTERN.search(str(value)) if value is not None else None
    return int(match.group(1)) if match else None


def _validate_frozen_runs(
    runs: list[QueryRun],
    queries: list[EvaluationQuery],
    publications: list[dict[str, Any]],
) -> None:
    query_ids = {query.query_id for query in queries}
    validate_comparison_matrix(runs, query_ids, set(REUSED_METHODS))
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


async def build_language_aware_lexical_runs(
    queries: list[EvaluationQuery],
    publications: list[dict[str, Any]],
    query_metadata: dict[str, QueryMetadata],
    *,
    limit: int,
) -> tuple[list[QueryRun], LanguageAwareLexicalAdapter, float]:
    if type(limit) is not int or limit <= 0 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    started = time.perf_counter()
    adapter = LanguageAwareLexicalAdapter(publications)
    build_runtime_ms = (time.perf_counter() - started) * 1000
    runs = [
        await adapter.retrieve(query, limit, query_metadata=query_metadata[query.query_id])
        for query in queries
    ]
    return runs, adapter, build_runtime_ms


def _top_five_rows(
    runs: list[QueryRun],
    queries: list[EvaluationQuery],
    publications: list[dict[str, Any]],
    *,
    depth: int,
    seed: int,
) -> list[dict[str, Any]]:
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    query_texts = {query.query_id: query.text for query in queries}
    corpus = {str(publication["id"]): publication for publication in publications}
    rows: list[dict[str, Any]] = []
    randomizer = random.Random(seed)
    for run in sorted(runs, key=lambda item: item.query_id):
        query_rows: list[dict[str, Any]] = []
        for item in run.results[:depth]:
            publication = corpus.get(item.publication_id)
            if publication is None:
                raise ValueError(f"new run refers to an unknown publication: {item.publication_id}")
            pair_key = f"{run.query_id}\0{item.publication_id}".encode("utf-8")
            query_rows.append(
                {
                    "candidate_id": f"pair-{hashlib.sha256(pair_key).hexdigest()}",
                    "query_text": query_texts[run.query_id],
                    "query_id": run.query_id,
                    "publication_id": item.publication_id,
                    "title": item.title or "",
                    "abstract": item.abstract or "",
                    "year": _publication_year(publication),
                    "source_url": item.source_url or "",
                    "relevance": "",
                }
            )
        randomizer.shuffle(query_rows)
        rows.extend(query_rows)
    pairs = [(row["query_id"], row["publication_id"]) for row in rows]
    if len(pairs) != len(set(pairs)):
        raise ValueError("new language-aware top-five results contain duplicate query/publication pairs")
    return rows


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(LANGUAGE_AWARE_POOL_COLUMNS),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _dimension_counts(
    rows: list[dict[str, Any]],
    metadata_by_query: dict[str, QueryMetadata],
    *,
    dimension: str,
    judged_pairs: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[getattr(metadata_by_query[row["query_id"]], dimension)].append(row)
    output = []
    for value in sorted(grouped):
        group_rows = grouped[value]
        new_rows = [
            row
            for row in group_rows
            if (row["query_id"], row["publication_id"]) not in judged_pairs
        ]
        output.append(
            {
                "value": value,
                "top_five_positions": len(group_rows),
                "already_judged_positions": len(group_rows) - len(new_rows),
                "new_unique_pairs": len({(row["query_id"], row["publication_id"]) for row in new_rows}),
            }
        )
    return output


def _build_overlap_report(
    rows: list[dict[str, Any]],
    metadata_by_query: dict[str, QueryMetadata],
    judged_pairs: set[tuple[str, str]],
    transfer_report: dict[str, Any],
) -> dict[str, Any]:
    new_rows = [
        row
        for row in rows
        if (row["query_id"], row["publication_id"]) not in judged_pairs
    ]
    by_query = []
    for query_id in sorted(metadata_by_query):
        query_rows = [row for row in rows if row["query_id"] == query_id]
        query_new = [
            row
            for row in query_rows
            if (row["query_id"], row["publication_id"]) not in judged_pairs
        ]
        metadata = metadata_by_query[query_id]
        by_query.append(
            {
                "query_id": query_id,
                "language": metadata.language,
                "script": metadata.script,
                "category": metadata.category,
                "top_five_positions": len(query_rows),
                "already_judged_positions": len(query_rows) - len(query_new),
                "new_unique_pairs": len({(row["query_id"], row["publication_id"]) for row in query_new}),
            }
        )
    return {
        "top_five_positions": len(rows),
        "unique_top_five_pairs": len({(row["query_id"], row["publication_id"]) for row in rows}),
        "already_judged_positions": sum(
            (row["query_id"], row["publication_id"]) in judged_pairs for row in rows
        ),
        "already_judged_unique_pairs": len(
            {
                (row["query_id"], row["publication_id"])
                for row in rows
                if (row["query_id"], row["publication_id"]) in judged_pairs
            }
        ),
        "new_unique_pairs": len({(row["query_id"], row["publication_id"]) for row in new_rows}),
        "conflicts": transfer_report["conflict_count"],
        "duplicate_pair_errors": transfer_report["duplicate_pair_error_count"],
        "invalid_score_errors": transfer_report["invalid_score_error_count"],
        "counts_by_query": by_query,
        "counts_by_language": _dimension_counts(
            rows, metadata_by_query, dimension="language", judged_pairs=judged_pairs
        ),
        "counts_by_script": _dimension_counts(
            rows, metadata_by_query, dimension="script", judged_pairs=judged_pairs
        ),
        "counts_by_category": _dimension_counts(
            rows, metadata_by_query, dimension="category", judged_pairs=judged_pairs
        ),
        "new_unjudged_pairs": [
            {"candidate_id": row["candidate_id"], "query_id": row["query_id"], "publication_id": row["publication_id"]}
            for row in new_rows
        ],
    }


def build_artifacts(
    *,
    queries_path: str | Path,
    query_metadata_path: str | Path,
    corpus_snapshot_path: str | Path,
    frozen_runs_path: str | Path,
    judgments_path: str | Path,
    output_directory: str | Path,
    expected_queries_sha256: str,
    expected_query_metadata_sha256: str,
    expected_corpus_sha256: str,
    expected_frozen_runs_sha256: str,
    expected_judgments_sha256: str,
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
    query_metadata_hash = _require_hash(
        query_metadata_path,
        expected_query_metadata_sha256,
        label="query metadata",
    )
    corpus_hash = _require_hash(corpus_snapshot_path, expected_corpus_sha256, label="corpus snapshot")
    frozen_runs_hash = _require_hash(
        frozen_runs_path,
        expected_frozen_runs_sha256,
        label="frozen runs",
    )
    judgments_hash = _require_hash(judgments_path, expected_judgments_sha256, label="judgments")

    queries = load_queries(queries_path)
    query_ids = {query.query_id for query in queries}
    query_metadata = {
        item.query_id: item for item in load_query_metadata(query_metadata_path, query_ids)
    }
    publications = load_frozen_snapshot(corpus_snapshot_path)
    frozen_runs = load_runs(frozen_runs_path, query_ids, set(REUSED_METHODS))
    _validate_frozen_runs(frozen_runs, queries, publications)
    judgments = load_judgments(judgments_path, query_ids)
    judged_pairs = {(judgment.query_id, judgment.publication_id) for judgment in judgments}

    selected_runs, adapter, build_runtime_ms = asyncio.run(
        build_language_aware_lexical_runs(
            queries,
            publications,
            query_metadata,
            limit=limit,
        )
    )
    frozen_by_pair = {(run.query_id, run.method): run for run in frozen_runs}
    selected_by_query = {run.query_id: run for run in selected_runs}
    combined = [
        selected_by_query[query.query_id]
        if method == LANGUAGE_AWARE_LEXICAL_METHOD
        else frozen_by_pair[(query.query_id, method)]
        for query in queries
        for method in EXTENDED_FINAL_METHODS
    ]
    validate_comparison_matrix(combined, query_ids, set(EXTENDED_FINAL_METHODS))
    top_five_rows = _top_five_rows(
        selected_runs,
        queries,
        publications,
        depth=depth,
        seed=seed,
    )
    transfer_source_rows = [
        {
            "candidate_id": f"old-{judgment.query_id}-{judgment.publication_id}",
            "query_id": judgment.query_id,
            "publication_id": judgment.publication_id,
            "relevance": judgment.relevance,
        }
        for judgment in judgments
    ]
    transferred_rows, transfer_report = transfer_judgments(
        transfer_source_rows,
        top_five_rows,
        expected_old_judgments=len(judgments),
    )
    overlap_report = _build_overlap_report(
        top_five_rows,
        query_metadata,
        judged_pairs,
        transfer_report,
    )
    if overlap_report["top_five_positions"] != len(queries) * depth:
        raise ValueError("language-aware top-five output does not contain one complete depth per query")
    if overlap_report["conflicts"] != 0:
        raise ValueError("judgment transfer produced conflicts")

    output = Path(output_directory)
    if output.exists():
        raise ValueError(f"output already exists: {output.name}")
    output.mkdir(parents=True)
    selected_path = output / "language-aware-lexical-runs.json"
    combined_path = output / "runs.json"
    top_five_path = output / "top-five-candidates.csv"
    transferred_path = output / "transferred-top-five-candidates.csv"
    new_unjudged_path = output / "new-unjudged-candidates.csv"
    overlap_path = output / "overlap-report.json"
    transfer_path = output / "judgment-transfer.json"
    write_runs_atomically(
        selected_path,
        selected_runs,
        queries,
        [LANGUAGE_AWARE_LEXICAL_METHOD],
    )
    write_runs_atomically(combined_path, combined, queries, EXTENDED_FINAL_METHODS)
    reloaded = load_runs(combined_path, query_ids, set(EXTENDED_FINAL_METHODS))
    for method in REUSED_METHODS:
        if _record_hash(reloaded, method) != _record_hash(frozen_runs, method):
            raise ValueError(f"reused {method} run records changed during artifact generation")
    _write_rows(top_five_path, top_five_rows)
    _write_rows(transferred_path, transferred_rows)
    new_unjudged_rows = [row for row in transferred_rows if row["relevance"] == ""]
    _write_rows(new_unjudged_path, new_unjudged_rows)
    write_json(overlap_path, overlap_report)
    write_json(transfer_path, transfer_report)

    latencies = [run.latency_ms for run in selected_runs if run.latency_ms is not None]
    timestamp = generated_at or datetime.now(UTC).isoformat()
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("generated_at must be an ISO-8601 timestamp") from exc
    write_json(
        output / "metadata.json",
        {
            "artifact_schema_version": 2,
            "methods": list(EXTENDED_FINAL_METHODS),
            "selected_language_aware_lexical": language_aware_lexical_metadata(
                index_statistics=adapter.index_statistics
            ),
            "source_commit": source_commit,
            "starting_commit": starting_commit,
            "source_branch": "eval/language-aware-lexical",
            "corpus_size": len(publications),
            "query_count": len(queries),
            "corpus_snapshot_sha256": corpus_hash,
            "query_set_sha256": query_hash,
            "query_metadata_sha256": query_metadata_hash,
            "frozen_runs_sha256": frozen_runs_hash,
            "judgments_sha256": judgments_hash,
            "reused_run_record_sha256": {
                method: _record_hash(frozen_runs, method) for method in REUSED_METHODS
            },
            "generated_artifact_sha256": {
                "language_aware_lexical_runs": sha256_file(selected_path),
                "combined_runs": sha256_file(combined_path),
                "top_five_candidates": sha256_file(top_five_path),
                "transferred_top_five_candidates": sha256_file(transferred_path),
                "new_unjudged_candidates": sha256_file(new_unjudged_path),
                "overlap_report": sha256_file(overlap_path),
                "judgment_transfer": sha256_file(transfer_path),
            },
            "top_k": limit,
            "pool_depth": depth,
            "pool_seed": seed,
            "pool_order_policy": (
                "query groups remain sorted by query_id; rows within each query are deterministically "
                "shuffled with pool_seed so row order does not expose retrieval rank"
            ),
            "candidate_id_policy": (
                "opaque SHA-256 pair id derived from query_id and publication_id; no method, rank, or score"
            ),
            "top_five_position_count": overlap_report["top_five_positions"],
            "already_judged_count": overlap_report["already_judged_unique_pairs"],
            "new_unjudged_pair_count": overlap_report["new_unique_pairs"],
            "conflict_count": overlap_report["conflicts"],
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
                "new_workbook_contains_only_unjudged_pairs": True,
                "effectiveness_metrics_deferred_until_new_pool_complete": True,
            },
            "description": (
                "Language-aware strictly lexical extension using the unchanged precise word and "
                "character channels plus one fixed language-aware comparison channel"
            ),
        },
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="build isolated language-aware lexical evaluation artifacts"
    )
    parser.add_argument("--queries", required=True)
    parser.add_argument("--query-metadata", required=True)
    parser.add_argument("--corpus-snapshot", required=True)
    parser.add_argument("--frozen-runs", required=True)
    parser.add_argument("--judgments", required=True)
    parser.add_argument("--output-directory", "--output-dir", dest="output_directory", required=True)
    parser.add_argument("--expected-queries-sha256", required=True)
    parser.add_argument("--expected-query-metadata-sha256", required=True)
    parser.add_argument("--expected-corpus-sha256", required=True)
    parser.add_argument("--expected-frozen-runs-sha256", required=True)
    parser.add_argument("--expected-judgments-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--starting-commit", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    output = build_artifacts(
        queries_path=args.queries,
        query_metadata_path=args.query_metadata,
        corpus_snapshot_path=args.corpus_snapshot,
        frozen_runs_path=args.frozen_runs,
        judgments_path=args.judgments,
        output_directory=args.output_directory,
        expected_queries_sha256=args.expected_queries_sha256,
        expected_query_metadata_sha256=args.expected_query_metadata_sha256,
        expected_corpus_sha256=args.expected_corpus_sha256,
        expected_frozen_runs_sha256=args.expected_frozen_runs_sha256,
        expected_judgments_sha256=args.expected_judgments_sha256,
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
