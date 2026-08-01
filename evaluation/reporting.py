from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any

from evaluation.artifacts import publish_directory
from evaluation.io import validate_comparison_matrix
from evaluation.metrics import evaluate_run
from evaluation.models import Judgment, QueryMetadata, QueryRun


GROUPING_DIMENSIONS = ("language", "script", "category")
VALIDATION_ASSUMPTIONS = (
    "Every selected method has exactly one run for every evaluated query, including zero-result runs.",
    "Relevance grades are integers 0, 1, or 2; grades 1 and 2 are positive relevance.",
    "Retrieved documents without a judgment are treated as nonrelevant.",
    "Queries without positive judgments remain in macro averages and receive zero recall, MRR, and nDCG.",
    "Effectiveness metrics and latency are reported separately and are not combined into an overall score.",
)
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "authorization",
    "database_url",
    "db_url",
    "dsn",
    "admin",
)


def _metric_names(k_values: list[int]) -> list[str]:
    return [
        "mrr",
        *[
            name
            for k in sorted(set(k_values))
            for name in (f"mrr@{k}", f"precision@{k}", f"recall@{k}", f"ndcg@{k}")
        ],
    ]


def nearest_rank_percentile(values: list[float], percentile: float) -> float | None:
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in the interval (0, 1]")
    if not values:
        return None
    if any(not math.isfinite(value) for value in values):
        raise ValueError("percentile values must be finite")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _validate_ranking_configuration(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("ranking configuration keys must be strings")
            normalized = key.casefold().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                raise ValueError("ranking configuration contains a prohibited sensitive key")
            _validate_ranking_configuration(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_ranking_configuration(nested)
    elif isinstance(value, str) and value.casefold().startswith(("postgresql://", "postgres://")):
        raise ValueError("ranking configuration must not contain a database URL")


def _validate_inputs(
    runs: list[QueryRun],
    judgments: list[Judgment],
    query_metadata: list[QueryMetadata],
    k_values: list[int],
) -> tuple[dict[str, QueryMetadata], list[str]]:
    if not k_values or any(type(k) is not int or k <= 0 for k in k_values):
        raise ValueError("k values must be positive integers")
    metadata_by_query = {item.query_id: item for item in query_metadata}
    if len(metadata_by_query) != len(query_metadata) or not metadata_by_query:
        raise ValueError("query metadata must contain unique records for at least one query")
    for item in query_metadata:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (item.query_id, item.language, item.script, item.category, item.topic)
        ):
            raise ValueError("query metadata values must be nonblank strings")
    query_ids = set(metadata_by_query)
    methods = sorted({run.method for run in runs})
    if not methods:
        raise ValueError("at least one retrieval method is required")
    validate_comparison_matrix(runs, query_ids, set(methods))
    judgment_keys = [(item.query_id, item.publication_id) for item in judgments]
    if len(judgment_keys) != len(set(judgment_keys)):
        raise ValueError("duplicate judgment for query/publication pair")
    unknown_judgments = sorted({item.query_id for item in judgments} - query_ids)
    if unknown_judgments:
        raise ValueError(f"judgments refer to unknown query IDs: {unknown_judgments}")
    for run in runs:
        if run.latency_ms is not None and (
            isinstance(run.latency_ms, bool)
            or not isinstance(run.latency_ms, (int, float))
            or not math.isfinite(run.latency_ms)
            or run.latency_ms < 0
        ):
            raise ValueError("latency_ms must be finite and non-negative")
        if run.method != "full_pipeline" and run.parser_mode is not None:
            raise ValueError("parser_mode is not applicable to keyword or vector-only runs")
        if run.method == "full_pipeline" and run.parser_mode is not None and (
            not isinstance(run.parser_mode, str) or not run.parser_mode.strip()
        ):
            raise ValueError("full-pipeline parser_mode must be null or a nonblank string")
    return metadata_by_query, methods


def build_report(
    runs: list[QueryRun],
    judgments: list[Judgment],
    query_metadata: list[QueryMetadata],
    *,
    git_commit: str,
    corpus_size: int,
    k_values: list[int],
    embedding_model: str,
    ranking_configuration: dict[str, Any],
    embedding_model_revision: str | None = None,
    embedding_template_version: str | None = None,
    input_sha256: dict[str, str] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    if corpus_size <= 0:
        raise ValueError("corpus size must be positive")
    if not isinstance(ranking_configuration, dict):
        raise ValueError("ranking configuration must be a JSON object")
    for name, value in (
        ("embedding_model_revision", embedding_model_revision),
        ("embedding_template_version", embedding_template_version),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be null or a nonblank string")
    _validate_ranking_configuration(ranking_configuration)
    metadata_by_query, methods = _validate_inputs(runs, judgments, query_metadata, k_values)
    k_values = sorted(set(k_values))
    metric_names = _metric_names(k_values)
    judgment_groups: dict[str, list[Judgment]] = defaultdict(list)
    for judgment in judgments:
        judgment_groups[judgment.query_id].append(judgment)

    per_query = []
    for run in sorted(runs, key=lambda item: (item.query_id, item.method)):
        metadata = metadata_by_query[run.query_id]
        metrics = evaluate_run(run.results, judgment_groups[run.query_id], k_values)
        positive_count = metrics["relevant_documents"]
        per_query.append(
            {
                "query_id": run.query_id,
                "method": run.method,
                "language": metadata.language,
                "script": metadata.script,
                "category": metadata.category,
                "topic": metadata.topic,
                "retrieved_result_count": len(run.results),
                "positively_judged_document_count": positive_count,
                "no_positive_judgments": positive_count == 0,
                "latency_ms": run.latency_ms,
                "parser_mode": run.parser_mode,
                **metrics,
            }
        )

    aggregates = []
    for method in methods:
        rows = [row for row in per_query if row["method"] == method]
        aggregate = {
            "method": method,
            "evaluated_queries": len(rows),
            "queries_without_relevant_judgments": sum(row["no_positive_judgments"] for row in rows),
            "mean_latency_ms": (
                fmean(row["latency_ms"] for row in rows if row["latency_ms"] is not None)
                if any(row["latency_ms"] is not None for row in rows)
                else None
            ),
        }
        aggregate.update({name: fmean(float(row[name]) for row in rows) for name in metric_names})
        aggregates.append(aggregate)

    grouped = []
    for dimension in GROUPING_DIMENSIONS:
        for group_value in sorted({getattr(item, dimension) for item in query_metadata}):
            group_query_ids = {
                item.query_id for item in query_metadata if getattr(item, dimension) == group_value
            }
            for method in methods:
                rows = [
                    row
                    for row in per_query
                    if row["method"] == method and row["query_id"] in group_query_ids
                ]
                if {row["query_id"] for row in rows} != group_query_ids:
                    raise ValueError("grouped metrics require equal method/query coverage")
                result = {
                    "method": method,
                    "grouping_dimension": dimension,
                    "group_value": group_value,
                    "query_count": len(rows),
                    "queries_without_positive_judgments": sum(
                        row["no_positive_judgments"] for row in rows
                    ),
                }
                result.update({name: fmean(float(row[name]) for row in rows) for name in metric_names})
                grouped.append(result)

    latency_summary = []
    for method in methods:
        method_runs = [run for run in runs if run.method == method]
        values = [float(run.latency_ms) for run in method_runs if run.latency_ms is not None]
        latency_summary.append(
            {
                "method": method,
                "run_count": len(method_runs),
                "measured_run_count": len(values),
                "missing_latency_count": len(method_runs) - len(values),
                "mean_ms": fmean(values) if values else None,
                "median_ms": median(values) if values else None,
                "minimum_ms": min(values) if values else None,
                "maximum_ms": max(values) if values else None,
                "p95_ms": nearest_rank_percentile(values, 0.95),
            }
        )

    parser_mode_summary = []
    for method in methods:
        method_runs = [run for run in runs if run.method == method]
        total = len(method_runs)
        if method != "full_pipeline":
            parser_mode_summary.append(
                {
                    "method": method,
                    "parser_mode": None,
                    "applicability": "not_applicable",
                    "run_count": total,
                    "percentage": 100.0,
                }
            )
            continue
        counts = Counter(run.parser_mode for run in method_runs)
        for mode in sorted(counts, key=lambda value: (value is None, value or "")):
            parser_mode_summary.append(
                {
                    "method": method,
                    "parser_mode": mode,
                    "applicability": "reported" if mode is not None else "unreported",
                    "run_count": counts[mode],
                    "percentage": 100.0 * counts[mode] / total,
                }
            )

    grade_counts = Counter(judgment.relevance for judgment in judgments)
    queries_without_positive = sum(
        not any(item.relevance > 0 for item in judgment_groups[query_id])
        for query_id in metadata_by_query
    )
    parser_mode_counts = Counter(
        run.parser_mode for run in runs if run.method == "full_pipeline" and run.parser_mode is not None
    )
    report_metadata = {
        "git_commit": git_commit,
        "evaluation_timestamp": evaluated_at or datetime.now(timezone.utc).isoformat(),
        "corpus_size": corpus_size,
        "query_count": len(metadata_by_query),
        "methods": methods,
        "k_values": k_values,
        "embedding_model": embedding_model,
        "ranking_configuration": ranking_configuration,
        "input_sha256": dict(sorted((input_sha256 or {}).items())),
        "judgment_grade_counts": {str(grade): grade_counts[grade] for grade in (0, 1, 2)},
        "queries_without_positive_judgments": queries_without_positive,
        "parser_mode_counts": dict(sorted(parser_mode_counts.items())),
        "validation_assumptions": list(VALIDATION_ASSUMPTIONS),
        "latency_percentile_convention": "nearest-rank: sorted_values[ceil(0.95*n)-1]",
    }
    if embedding_model_revision is not None:
        report_metadata["embedding_model_revision"] = embedding_model_revision
    if embedding_template_version is not None:
        report_metadata["embedding_template_version"] = embedding_template_version

    return {
        "metadata": report_metadata,
        "aggregate_metrics": aggregates,
        "per_query_metrics": per_query,
        "grouped_metrics": grouped,
        "latency_summary": latency_summary,
        "parser_mode_summary": parser_mode_summary,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_report_files(output: Path, report: dict[str, Any]) -> None:
    metadata = report["metadata"]
    metric_names = _metric_names(metadata["k_values"])
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        output / "metrics.csv",
        report["aggregate_metrics"],
        [
            "method",
            "evaluated_queries",
            "queries_without_relevant_judgments",
            "mean_latency_ms",
            *metric_names,
        ],
    )
    _write_csv(
        output / "per_query_metrics.csv",
        report["per_query_metrics"],
        [
            "query_id",
            "method",
            "language",
            "script",
            "category",
            "topic",
            "retrieved_result_count",
            "positively_judged_document_count",
            "no_positive_judgments",
            "latency_ms",
            "parser_mode",
            "judged_documents",
            "relevant_documents",
            "has_relevant_judgments",
            *metric_names,
        ],
    )
    _write_csv(
        output / "grouped_metrics.csv",
        report["grouped_metrics"],
        [
            "method",
            "grouping_dimension",
            "group_value",
            "query_count",
            "queries_without_positive_judgments",
            *metric_names,
        ],
    )
    _write_csv(
        output / "latency_summary.csv",
        report["latency_summary"],
        [
            "method",
            "run_count",
            "measured_run_count",
            "missing_latency_count",
            "mean_ms",
            "median_ms",
            "minimum_ms",
            "maximum_ms",
            "p95_ms",
        ],
    )
    _write_csv(
        output / "parser_mode_summary.csv",
        report["parser_mode_summary"],
        ["method", "parser_mode", "applicability", "run_count", "percentage"],
    )

    lines = [
        "# Search Evaluation Summary",
        "",
        f"- Git commit: `{metadata['git_commit']}`",
        f"- Evaluation timestamp: `{metadata['evaluation_timestamp']}`",
        f"- Corpus size: {metadata['corpus_size']}",
        f"- Query count: {metadata['query_count']}",
        f"- Methods: {', '.join(metadata['methods'])}",
        f"- k values: {', '.join(map(str, metadata['k_values']))}",
        f"- Embedding model: `{metadata['embedding_model']}`",
        f"- Embedding model revision: `{metadata.get('embedding_model_revision', 'not recorded')}`",
        f"- Embedding template version: `{metadata.get('embedding_template_version', 'not recorded')}`",
        "",
        "Effectiveness and latency are reported separately; no synthetic overall score is calculated.",
        "",
        "## Aggregate Effectiveness",
        "",
        "| Method | Queries | No positive judgments | MRR |",
        "|---|---:|---:|---:|",
    ]
    for row in report["aggregate_metrics"]:
        lines.append(
            f"| {row['method']} | {row['evaluated_queries']} | "
            f"{row['queries_without_relevant_judgments']} | {row['mrr']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Latency",
            "",
            "| Method | Measured runs | Mean ms | Median ms | P95 ms |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["latency_summary"]:
        values = [row["mean_ms"], row["median_ms"], row["p95_ms"]]
        formatted = ["" if value is None else f"{value:.3f}" for value in values]
        lines.append(
            f"| {row['method']} | {row['measured_run_count']} | "
            f"{formatted[0]} | {formatted[1]} | {formatted[2]} |"
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(
    output_directory: str | Path,
    report: dict[str, Any],
    *,
    overwrite: bool = False,
) -> None:
    publish_directory(
        output_directory,
        lambda temporary: _write_report_files(temporary, report),
        overwrite=overwrite,
    )
