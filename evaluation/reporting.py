from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

from evaluation.metrics import evaluate_run
from evaluation.models import Judgment, QueryRun


def build_report(
    runs: list[QueryRun],
    judgments: list[Judgment],
    *,
    git_commit: str,
    corpus_size: int,
    query_count: int,
    k_values: list[int],
    embedding_model: str,
    ranking_configuration: dict[str, Any],
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    judgment_groups: dict[str, list[Judgment]] = defaultdict(list)
    for judgment in judgments:
        judgment_groups[judgment.query_id].append(judgment)

    per_query = []
    for run in runs:
        metrics = evaluate_run(run.results, judgment_groups[run.query_id], k_values)
        per_query.append(
            {
                "query_id": run.query_id,
                "method": run.method,
                "latency_ms": run.latency_ms,
                "parser_mode": run.parser_mode,
                **metrics,
            }
        )

    aggregates = []
    for method in sorted({row["method"] for row in per_query}):
        rows = [row for row in per_query if row["method"] == method]
        metric_names = ["mrr", *[name for k in sorted(set(k_values)) for name in (f"precision@{k}", f"recall@{k}", f"ndcg@{k}")]]
        aggregate = {
            "method": method,
            "evaluated_queries": len(rows),
            "queries_without_relevant_judgments": sum(not row["has_relevant_judgments"] for row in rows),
            "mean_latency_ms": fmean(
                row["latency_ms"] for row in rows if row["latency_ms"] is not None
            )
            if any(row["latency_ms"] is not None for row in rows)
            else None,
        }
        aggregate.update({name: fmean(float(row[name]) for row in rows) if rows else 0.0 for name in metric_names})
        aggregates.append(aggregate)

    parser_modes = Counter(run.parser_mode for run in runs if run.parser_mode)
    return {
        "metadata": {
            "git_commit": git_commit,
            "evaluation_timestamp": evaluated_at or datetime.now(timezone.utc).isoformat(),
            "corpus_size": corpus_size,
            "query_count": query_count,
            "k_values": sorted(set(k_values)),
            "embedding_model": embedding_model,
            "ranking_configuration": ranking_configuration,
            "parser_mode_counts": dict(sorted(parser_modes.items())),
        },
        "aggregate_metrics": aggregates,
        "per_query_metrics": per_query,
    }


def write_report(output_directory: str | Path, report: dict[str, Any]) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    aggregates = report["aggregate_metrics"]
    metric_fields = [
        "method",
        "evaluated_queries",
        "queries_without_relevant_judgments",
        "mean_latency_ms",
        "mrr",
        *[
            name
            for k in report["metadata"]["k_values"]
            for name in (f"precision@{k}", f"recall@{k}", f"ndcg@{k}")
        ],
    ]
    with (output / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=metric_fields)
        writer.writeheader()
        if aggregates:
            writer.writerows(aggregates)

    metadata = report["metadata"]
    lines = [
        "# Search Evaluation Summary",
        "",
        f"- Git commit: `{metadata['git_commit']}`",
        f"- Evaluation timestamp: `{metadata['evaluation_timestamp']}`",
        f"- Corpus size: {metadata['corpus_size']}",
        f"- Query count: {metadata['query_count']}",
        f"- k values: {', '.join(map(str, metadata['k_values']))}",
        f"- Embedding model: `{metadata['embedding_model']}`",
        "",
        "## Aggregate Metrics",
        "",
    ]
    if not aggregates:
        lines.append("No evaluated runs were supplied.")
    else:
        for row in aggregates:
            lines.append(
                f"- `{row['method']}`: MRR={row['mrr']:.4f}; "
                f"evaluated queries={row['evaluated_queries']}; "
                f"queries without relevant judgments={row['queries_without_relevant_judgments']}"
            )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
