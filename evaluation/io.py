from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from evaluation.models import EvaluationQuery, Judgment, QueryRun, RetrievedItem


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_queries(path: str | Path) -> list[EvaluationQuery]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("queries"), list):
        raise ValueError("queries file must contain a queries array")
    for row in payload["queries"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"query_id", "text"}
            or not isinstance(row.get("query_id"), str)
            or not row["query_id"].strip()
            or not isinstance(row.get("text"), str)
            or not row["text"].strip()
        ):
            raise ValueError("query_id and text must be non-empty strings")
    queries = [EvaluationQuery(**row) for row in payload["queries"]]
    identifiers = [query.query_id for query in queries]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate query_id")
    return queries


def load_judgments(path: str | Path, known_query_ids: set[str] | None = None) -> list[Judgment]:
    judgments = [Judgment(**row) for row in read_json(path)["judgments"]]
    keys = [(item.query_id, item.publication_id) for item in judgments]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate judgment for query/publication pair")
    if known_query_ids is not None:
        unknown = sorted({item.query_id for item in judgments} - known_query_ids)
        if unknown:
            raise ValueError(f"judgments refer to unknown query IDs: {unknown}")
    return judgments


def load_runs(
    path: str | Path,
    known_query_ids: set[str] | None = None,
    expected_methods: set[str] | None = None,
) -> list[QueryRun]:
    records = read_json(path)["runs"]
    keys = [(str(row["query_id"]), str(row["method"])) for row in records]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate run for query/method pair")

    runs = []
    for row in records:
        query_id, method = str(row["query_id"]), str(row["method"])
        if known_query_ids is not None and query_id not in known_query_ids:
            raise ValueError(f"run refers to unknown query ID: {query_id}")
        if expected_methods is not None and method not in expected_methods:
            raise ValueError(f"run uses unknown method: {method}")
        latency_ms = row.get("latency_ms")
        if latency_ms is not None and (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or not math.isfinite(latency_ms)
            or latency_ms < 0
        ):
            raise ValueError("latency_ms must be finite and non-negative")
        rows = list(row.get("results", []))
        if any(isinstance(result["rank"], bool) or not isinstance(result["rank"], int) for result in rows):
            raise ValueError("run ranks must be integers")
        ranks = [result["rank"] for result in rows]
        if len(ranks) != len(set(ranks)):
            raise ValueError("duplicate ranks within a run")
        if sorted(ranks) != list(range(1, len(rows) + 1)):
            raise ValueError("run ranks must be contiguous and one-based")
        publication_ids = [str(result["publication_id"]) for result in rows]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("duplicate publication IDs within a run")
        if any(
            isinstance(result["score"], bool)
            or not isinstance(result["score"], (int, float))
            or not math.isfinite(result["score"])
            for result in rows
        ):
            raise ValueError("result scores must be finite")
        rows.sort(key=lambda result: result["rank"])
        items = [
            RetrievedItem(
                publication_id=str(row["publication_id"]),
                score=float(row["score"]),
                title=row.get("title"),
                abstract=row.get("abstract"),
                source_url=row.get("source_url"),
            )
            for row in rows
        ]
        runs.append(QueryRun(query_id, method, items, latency_ms, row.get("parser_mode")))
    return runs


def validate_comparison_matrix(
    runs: list[QueryRun],
    query_ids: set[str],
    expected_methods: set[str],
) -> None:
    actual = [(run.query_id, run.method) for run in runs]
    if len(actual) != len(set(actual)):
        raise ValueError("duplicate run for query/method pair")
    expected = {(query_id, method) for query_id in query_ids for method in expected_methods}
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    if missing or unexpected:
        raise ValueError(f"incomplete comparison matrix; missing={missing}, unexpected={unexpected}")
