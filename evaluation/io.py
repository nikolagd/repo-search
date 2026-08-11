from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evaluation.models import EvaluationQuery, Judgment, QueryMetadata, QueryRun, RetrievedItem


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_json_atomically(
    path: str | Path,
    value: Any,
    *,
    overwrite: bool = False,
    validator: Callable[[Path], None] | None = None,
) -> None:
    output = Path(path)
    if output.exists() and not overwrite:
        raise ValueError(f"output already exists: {output.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_json(temporary, value)
        if validator is not None:
            validator(temporary)
        if overwrite:
            os.replace(temporary, output)
        else:
            try:
                os.link(temporary, output)
            except FileExistsError:
                raise ValueError(f"output already exists: {output.name}") from None
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    payload = read_json(path)
    if not isinstance(payload, dict) or set(payload) != {"judgments"} or not isinstance(payload["judgments"], list):
        raise ValueError("judgments file must contain only a judgments array")
    judgments = []
    for row in payload["judgments"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"query_id", "publication_id", "relevance"}
            or not isinstance(row.get("query_id"), str)
            or not row["query_id"].strip()
            or not isinstance(row.get("publication_id"), str)
            or not row["publication_id"].strip()
            or isinstance(row.get("relevance"), bool)
            or not isinstance(row.get("relevance"), int)
        ):
            raise ValueError("judgments must contain query_id, publication_id, and integer relevance")
        judgments.append(Judgment(**row))
    keys = [(item.query_id, item.publication_id) for item in judgments]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate judgment for query/publication pair")
    if known_query_ids is not None:
        unknown = sorted({item.query_id for item in judgments} - known_query_ids)
        if unknown:
            raise ValueError(f"judgments refer to unknown query IDs: {unknown}")
    return judgments


def load_query_metadata(
    path: str | Path,
    known_query_ids: set[str],
) -> list[QueryMetadata]:
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"query_metadata"}
        or not isinstance(payload["query_metadata"], list)
    ):
        raise ValueError("query metadata file must contain only a query_metadata array")
    required = {"query_id", "language", "script", "category", "topic"}
    records = []
    for row in payload["query_metadata"]:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("query metadata records must contain exactly the supported fields")
        if any(not isinstance(row[field], str) or not row[field].strip() for field in required):
            raise ValueError("query metadata values must be nonblank strings")
        records.append(QueryMetadata(**row))
    identifiers = [record.query_id for record in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate query_id in query metadata")
    actual = set(identifiers)
    missing = sorted(known_query_ids - actual)
    unknown = sorted(actual - known_query_ids)
    if missing or unknown:
        raise ValueError(f"query metadata coverage mismatch; missing={missing}, unknown={unknown}")
    return records


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
        parser_mode = row.get("parser_mode")
        if method == "full_pipeline":
            if parser_mode is not None and (
                not isinstance(parser_mode, str) or not parser_mode.strip()
            ):
                raise ValueError("full-pipeline parser_mode must be null or a nonblank string")
        elif parser_mode is not None:
            raise ValueError("parser_mode is not applicable to non-pipeline methods")
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
        runs.append(QueryRun(query_id, method, items, latency_ms, parser_mode))
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
