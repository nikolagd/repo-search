from __future__ import annotations

import csv
import re
from pathlib import Path

from evaluation.io import load_judgments, write_json_atomically
from evaluation.models import EvaluationQuery, Judgment


POOL_COLUMNS = (
    "candidate_id",
    "query_text",
    "query_id",
    "publication_id",
    "title",
    "abstract",
    "source_url",
    "relevance",
)
IDENTITY_COLUMNS = POOL_COLUMNS[:-1]
RELEVANCE_PATTERN = re.compile(r"[012]")


def _read_csv(path: str | Path, *, label: str) -> list[dict[str, str]]:
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != list(POOL_COLUMNS):
                raise ValueError(f"{label} must contain the exact expected columns in order")
            rows = list(reader)
    except csv.Error as exc:
        raise ValueError(f"{label} contains malformed CSV: {exc}") from None
    if any(set(row) != set(POOL_COLUMNS) or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{label} contains a malformed CSV row")
    return rows


def _validate_unique_rows(rows: list[dict[str, str]], *, label: str) -> None:
    candidate_ids = [row["candidate_id"] for row in rows]
    if any(not candidate_id.strip() for candidate_id in candidate_ids):
        raise ValueError(f"{label} contains a blank candidate_id")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"{label} contains duplicate candidate_id values")
    if any(not row["query_id"].strip() or not row["publication_id"].strip() for row in rows):
        raise ValueError(f"{label} contains a blank query_id or publication_id")
    pairs = [(row["query_id"], row["publication_id"]) for row in rows]
    if len(pairs) != len(set(pairs)):
        raise ValueError(f"{label} contains duplicate query/publication pairs")


def build_judgments_from_assessment(
    queries: list[EvaluationQuery],
    pool_template_path: str | Path,
    assessment_path: str | Path,
) -> list[Judgment]:
    query_texts = {query.query_id: query.text for query in queries}
    template_rows = _read_csv(pool_template_path, label="pool template")
    assessment_rows = _read_csv(assessment_path, label="assessment")
    _validate_unique_rows(template_rows, label="pool template")
    _validate_unique_rows(assessment_rows, label="assessment")

    for label, rows in (("pool template", template_rows), ("assessment", assessment_rows)):
        for row in rows:
            if row["query_id"] not in query_texts:
                raise ValueError(f"{label} refers to unknown query ID: {row['query_id']}")
            if row["query_text"] != query_texts[row["query_id"]]:
                raise ValueError(f"{label} query text mismatch for query ID: {row['query_id']}")
    for row in template_rows:
        if row["relevance"] != "":
            raise ValueError("pool template relevance values must be blank")

    template_by_candidate = {row["candidate_id"]: row for row in template_rows}
    assessment_by_candidate = {row["candidate_id"]: row for row in assessment_rows}
    missing = sorted(set(template_by_candidate) - set(assessment_by_candidate))
    additional = sorted(set(assessment_by_candidate) - set(template_by_candidate))
    if missing or additional:
        raise ValueError(f"assessment candidate coverage mismatch; missing={missing}, additional={additional}")

    judgments = []
    for template in template_rows:
        assessment = assessment_by_candidate[template["candidate_id"]]
        for field in IDENTITY_COLUMNS:
            if assessment[field] != template[field]:
                raise ValueError(
                    f"assessment changed {field} for candidate_id {template['candidate_id']}"
                )
        relevance = assessment["relevance"]
        if RELEVANCE_PATTERN.fullmatch(relevance) is None:
            raise ValueError(
                f"assessment relevance must be exactly integer 0, 1, or 2 for candidate_id "
                f"{template['candidate_id']}"
            )
        judgments.append(
            Judgment(
                query_id=template["query_id"],
                publication_id=template["publication_id"],
                relevance=int(relevance),
            )
        )
    return sorted(judgments, key=lambda item: (item.query_id, item.publication_id))


def import_judgments(
    queries: list[EvaluationQuery],
    pool_template_path: str | Path,
    assessment_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> list[Judgment]:
    source_paths = {
        Path(pool_template_path).resolve(),
        Path(assessment_path).resolve(),
    }
    output = Path(output_path)
    if output.resolve() in source_paths:
        raise ValueError("output path must not replace an assessment source file")
    judgments = build_judgments_from_assessment(queries, pool_template_path, assessment_path)
    write_json_atomically(
        output_path,
        {
            "judgments": [
                {
                    "query_id": judgment.query_id,
                    "publication_id": judgment.publication_id,
                    "relevance": judgment.relevance,
                }
                for judgment in judgments
            ]
        },
        overwrite=overwrite,
        validator=lambda path: load_judgments(path, {query.query_id for query in queries}),
    )
    return judgments
