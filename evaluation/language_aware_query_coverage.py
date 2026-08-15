from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from evaluation.adapters import (
    LANGUAGE_AWARE_LEXICAL_METHOD,
    LANGUAGE_AWARE_QUERY_COVERAGE_VERSION,
    LANGUAGE_AWARE_STOP_WORD_HASH,
    build_language_aware_query_concepts,
    distinct_language_aware_query_concepts,
    language_aware_document_word_tokens,
    language_route,
    matched_language_aware_concepts,
    required_language_aware_concept_matches,
)
from evaluation.bm25_artifacts import load_frozen_snapshot
from evaluation.io import (
    load_queries,
    load_query_metadata,
    load_runs,
    read_json,
    sha256_file,
    write_json,
)
from evaluation.models import EvaluationQuery, QueryMetadata, QueryRun


def diagnose_top_five_coverage(
    runs: Iterable[QueryRun],
    queries: list[EvaluationQuery],
    query_metadata: list[QueryMetadata],
    publications: list[dict[str, Any]],
    *,
    depth: int = 5,
) -> dict[str, Any]:
    """Audit word-concept coverage without loading any grading data."""

    if type(depth) is not int or depth <= 0:
        raise ValueError("depth must be a positive integer")
    query_by_id = {query.query_id: query for query in queries}
    metadata_by_id = {metadata.query_id: metadata for metadata in query_metadata}
    corpus_by_id = {str(publication["id"]): publication for publication in publications}
    run_list = sorted(runs, key=lambda run: run.query_id)
    rows: list[dict[str, Any]] = []
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for run in run_list:
        if run.method != LANGUAGE_AWARE_LEXICAL_METHOD:
            raise ValueError("coverage audit accepts only language-aware lexical runs")
        query = query_by_id[run.query_id]
        metadata = metadata_by_id[run.query_id]
        route = language_route(metadata)
        concepts = distinct_language_aware_query_concepts(
            build_language_aware_query_concepts(query.text, route)
        )
        for rank, item in enumerate(run.results[:depth], start=1):
            publication = corpus_by_id.get(item.publication_id, {})
            document_tokens = language_aware_document_word_tokens(
                publication.get("title", item.title),
                publication.get("abstract", item.abstract),
                route,
            )
            matched_keys = matched_language_aware_concepts(concepts, document_tokens)
            matched_concepts = [
                {
                    "key": concept.key,
                    "source_token": concept.source_token,
                }
                for concept in concepts
                if concept.key in matched_keys
            ]
            row = {
                "query_id": run.query_id,
                "query_text": query.text,
                "language": metadata.language,
                "script": metadata.script,
                "category": metadata.category,
                "route": route,
                "rank": rank,
                "publication_id": item.publication_id,
                "title": item.title or publication.get("title") or "",
                "score": item.score,
                "content_concept_count": len(concepts),
                "matched_concept_count": len(matched_keys),
                "required_matches": required_language_aware_concept_matches(len(concepts)),
                "meets_fixed_threshold": len(matched_keys)
                >= required_language_aware_concept_matches(len(concepts)),
                "matched_concepts": matched_concepts,
                "coverage_uses_character_ngrams": False,
            }
            rows.append(row)
            by_query[run.query_id].append(row)

    matched_distribution = Counter(row["matched_concept_count"] for row in rows)
    one_match_rows = [row for row in rows if row["matched_concept_count"] == 1]
    query_summary = []
    for query_id in sorted(by_query):
        query_rows = by_query[query_id]
        query_summary.append(
            {
                "query_id": query_id,
                "language": query_rows[0]["language"],
                "script": query_rows[0]["script"],
                "category": query_rows[0]["category"],
                "top_five_positions": len(query_rows),
                "one_content_concept_result_count": sum(
                    row["matched_concept_count"] == 1 for row in query_rows
                ),
                "matched_concept_count_distribution": dict(
                    sorted(Counter(row["matched_concept_count"] for row in query_rows).items())
                ),
            }
        )

    return {
        "schema_version": 1,
        "audit": "label_blind_language_aware_query_concept_coverage",
        "method": LANGUAGE_AWARE_LEXICAL_METHOD,
        "query_coverage_version": LANGUAGE_AWARE_QUERY_COVERAGE_VERSION,
        "stop_word_hash": LANGUAGE_AWARE_STOP_WORD_HASH,
        "depth": depth,
        "query_count": len(queries),
        "top_five_positions": len(rows),
        "unique_top_five_pairs": len({(row["query_id"], row["publication_id"]) for row in rows}),
        "matched_concept_count_distribution": dict(sorted(matched_distribution.items())),
        "one_content_concept_result_count": len(one_match_rows),
        "one_content_concept_results": one_match_rows,
        "q10_top_five_case": [row for row in rows if row["query_id"] == "q10"],
        "counts_by_query": query_summary,
        "rows": rows,
        "methodological_note": (
            "Coverage is computed from distinct route-normalized word concept groups only. "
            "Character 4-grams, scores, and any grading data are excluded from concept matching."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="write a label-blind language-aware lexical concept coverage audit"
    )
    parser.add_argument("--queries", required=True)
    parser.add_argument("--query-metadata", required=True)
    parser.add_argument("--corpus-snapshot", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--depth", type=int, default=5)
    args = parser.parse_args(argv)

    queries = load_queries(args.queries)
    query_ids = {query.query_id for query in queries}
    metadata = load_query_metadata(args.query_metadata, query_ids)
    publications = load_frozen_snapshot(args.corpus_snapshot)
    runs = load_runs(args.runs, query_ids, {LANGUAGE_AWARE_LEXICAL_METHOD})
    report = diagnose_top_five_coverage(
        runs,
        queries,
        metadata,
        publications,
        depth=args.depth,
    )
    report["input_sha256"] = {
        "queries": sha256_file(args.queries),
        "query_metadata": sha256_file(args.query_metadata),
        "corpus_snapshot": sha256_file(args.corpus_snapshot),
        "runs": sha256_file(args.runs),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
