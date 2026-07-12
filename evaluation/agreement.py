from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

from evaluation.artifacts import publish_directory
from evaluation.models import Judgment


GRADES = (0, 1, 2)


def _judgment_map(judgments: list[Judgment], *, label: str) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    for judgment in judgments:
        if (
            not isinstance(judgment.query_id, str)
            or not judgment.query_id.strip()
            or not isinstance(judgment.publication_id, str)
            or not judgment.publication_id.strip()
            or type(judgment.relevance) is not int
            or judgment.relevance not in GRADES
        ):
            raise ValueError(f"{label} contains an invalid judgment")
        key = (judgment.query_id, judgment.publication_id)
        if key in result:
            raise ValueError(f"{label} contains duplicate query/publication pairs")
        result[key] = judgment.relevance
    return result


def compare_judgments(
    assessor_a: list[Judgment],
    assessor_b: list[Judgment],
    *,
    input_sha256: dict[str, str] | None = None,
    git_commit: str | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    a = _judgment_map(assessor_a, label="assessor A")
    b = _judgment_map(assessor_b, label="assessor B")
    if not a or not b:
        raise ValueError("assessor agreement requires at least one judgment pair")
    missing = sorted(set(a) - set(b))
    additional = sorted(set(b) - set(a))
    if missing or additional:
        raise ValueError(
            f"assessor judgment pair mismatch; missing_from_b={missing}, additional_in_b={additional}"
        )

    matrix = [[0 for _ in GRADES] for _ in GRADES]
    disagreements = []
    for query_id, publication_id in sorted(a):
        grade_a = a[(query_id, publication_id)]
        grade_b = b[(query_id, publication_id)]
        matrix[grade_a][grade_b] += 1
        if grade_a != grade_b:
            disagreements.append(
                {
                    "query_id": query_id,
                    "publication_id": publication_id,
                    "assessor_a_relevance": grade_a,
                    "assessor_b_relevance": grade_b,
                }
            )

    total = len(a)
    exact_count = sum(matrix[grade][grade] for grade in GRADES)
    observed = Fraction(exact_count, total)
    row_totals = [sum(row) for row in matrix]
    column_totals = [sum(matrix[row][column] for row in GRADES) for column in GRADES]
    expected = sum(
        Fraction(row_totals[grade] * column_totals[grade], total * total) for grade in GRADES
    )
    unweighted_denominator = 1 - expected
    unweighted_kappa = (
        None
        if unweighted_denominator == 0
        else float((observed - expected) / unweighted_denominator)
    )

    observed_disagreement = sum(
        Fraction(matrix[row][column] * (row - column) ** 2, total * 4)
        for row in GRADES
        for column in GRADES
    )
    expected_disagreement = sum(
        Fraction(row_totals[row] * column_totals[column] * (row - column) ** 2, total * total * 4)
        for row in GRADES
        for column in GRADES
    )
    quadratic_kappa = (
        None
        if expected_disagreement == 0
        else float(1 - observed_disagreement / expected_disagreement)
    )

    return {
        "metadata": {
            "git_commit": git_commit,
            "evaluation_timestamp": evaluated_at or datetime.now(timezone.utc).isoformat(),
            "input_sha256": dict(sorted((input_sha256 or {}).items())),
            "grade_order": list(GRADES),
            "matrix_orientation": "rows=assessor_a, columns=assessor_b",
            "unweighted_kappa_formula": "(p_o - p_e) / (1 - p_e)",
            "quadratic_weight": "((assessor_a_grade - assessor_b_grade) / 2)^2",
            "quadratic_kappa_formula": "1 - observed_weighted_disagreement / expected_weighted_disagreement",
            "zero_denominator_policy": (
                "unweighted kappa is null when 1-p_e is zero; quadratic kappa is null when "
                "expected weighted disagreement is zero"
            ),
        },
        "pair_count": total,
        "disagreement_count": len(disagreements),
        "exact_agreement_count": exact_count,
        "exact_agreement_percentage": 100.0 * exact_count / total,
        "unweighted_cohen_kappa": unweighted_kappa,
        "quadratic_weighted_cohen_kappa": quadratic_kappa,
        "confusion_matrix": matrix,
        "disagreements": disagreements,
    }


def _write_agreement_files(output: Path, report: dict[str, Any]) -> None:
    (output / "agreement.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output / "confusion_matrix.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["assessor_a_grade", "assessor_b_0", "assessor_b_1", "assessor_b_2"])
        for grade, row in zip(GRADES, report["confusion_matrix"]):
            writer.writerow([grade, *row])
    with (output / "disagreements.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = [
            "query_id",
            "publication_id",
            "assessor_a_relevance",
            "assessor_b_relevance",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(report["disagreements"])
    lines = [
        "# Assessor Agreement Summary",
        "",
        f"- Compared pairs: {report['pair_count']}",
        f"- Exact agreement: {report['exact_agreement_percentage']:.4f}%",
        f"- Disagreements: {report['disagreement_count']}",
        f"- Unweighted Cohen's kappa: {report['unweighted_cohen_kappa']}",
        f"- Quadratic weighted Cohen's kappa: {report['quadratic_weighted_cohen_kappa']}",
        "",
        "Disagreements are preserved for adjudication and are not reconciled automatically.",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_agreement_report(
    output_directory: str | Path,
    report: dict[str, Any],
    *,
    overwrite: bool = False,
) -> None:
    publish_directory(
        output_directory,
        lambda temporary: _write_agreement_files(temporary, report),
        overwrite=overwrite,
    )
