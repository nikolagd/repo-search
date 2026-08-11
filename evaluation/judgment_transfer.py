from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from evaluation.io import read_json, sha256_file, write_json_atomically


Pair = tuple[str, str]


class JudgmentTransferError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]):
        self.report = report
        super().__init__(message)


def _pair(row: Mapping[str, Any], *, label: str, row_number: int) -> Pair:
    query_id = row.get("query_id")
    publication_id = row.get("publication_id")
    if not isinstance(query_id, str) or not query_id.strip():
        raise ValueError(f"{label} row {row_number} has an invalid query_id")
    if not isinstance(publication_id, str) or not publication_id.strip():
        raise ValueError(f"{label} row {row_number} has an invalid publication_id")
    return query_id, publication_id


def _grade(value: Any, *, label: str, pair: Pair) -> int | None:
    if value is None or value == "":
        return None
    if type(value) is int and value in {0, 1, 2}:
        return value
    if isinstance(value, str) and value in {"0", "1", "2"}:
        return int(value)
    raise ValueError(f"{label} contains invalid relevance for pair {pair!r}")


def transfer_judgments(
    old_rows: Iterable[Mapping[str, Any]],
    new_rows: Iterable[Mapping[str, Any]],
    *,
    expected_old_judgments: int = 69,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Transfer grades only by stable ``(query_id, publication_id)`` pairs."""

    old_map: dict[Pair, int] = {}
    conflicts: list[dict[str, Any]] = []
    duplicate_errors: list[dict[str, Any]] = []
    invalid_scores: list[str] = []
    old_row_count = 0
    old_blank_row_count = 0
    for row_number, row in enumerate(old_rows, start=2):
        old_row_count += 1
        pair = _pair(row, label="old assessment", row_number=row_number)
        try:
            relevance = _grade(row.get("relevance"), label="old assessment", pair=pair)
        except ValueError as exc:
            invalid_scores.append(str(exc))
            continue
        if relevance is None:
            old_blank_row_count += 1
            continue
        if pair in old_map:
            detail = {
                "query_id": pair[0],
                "publication_id": pair[1],
                "first_relevance": old_map[pair],
                "duplicate_relevance": relevance,
            }
            if old_map[pair] == relevance:
                duplicate_errors.append(detail)
            else:
                conflicts.append(detail)
            continue
        old_map[pair] = relevance

    output_rows: list[dict[str, Any]] = []
    new_pairs: set[Pair] = set()
    transferred_pairs: set[Pair] = set()
    for row_number, source in enumerate(new_rows, start=2):
        pair = _pair(source, label="new pool", row_number=row_number)
        if pair in new_pairs:
            duplicate_errors.append(
                {"query_id": pair[0], "publication_id": pair[1], "source": "new pool"}
            )
            continue
        new_pairs.add(pair)
        try:
            existing = _grade(source.get("relevance"), label="new pool", pair=pair)
        except ValueError as exc:
            invalid_scores.append(str(exc))
            existing = None
        row = deepcopy(dict(source))
        if existing is not None:
            conflicts.append(
                {
                    "query_id": pair[0],
                    "publication_id": pair[1],
                    "reason": "new pool must be unjudged before transfer",
                    "new_relevance": existing,
                }
            )
        if pair in old_map:
            row["relevance"] = old_map[pair]
            transferred_pairs.add(pair)
        else:
            row["relevance"] = ""
        output_rows.append(row)

    unmatched = sorted(set(old_map) - new_pairs)
    unjudged = sorted(new_pairs - set(old_map))
    report = {
        "old_pool_row_count": old_row_count,
        "old_blank_row_count": old_blank_row_count,
        "expected_old_judgments": expected_old_judgments,
        "observed_old_judgments": len(old_map),
        "new_pool_row_count": len(new_pairs),
        "transferred_judgment_count": len(transferred_pairs),
        "unmatched_old_judgment_count": len(unmatched),
        "unmatched_old_judgments": [
            {"query_id": pair[0], "publication_id": pair[1], "relevance": old_map[pair]}
            for pair in unmatched
        ],
        "new_unjudged_pair_count": len(unjudged),
        "new_unjudged_pairs": [
            {"query_id": pair[0], "publication_id": pair[1]} for pair in unjudged
        ],
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "duplicate_pair_error_count": len(duplicate_errors),
        "duplicate_pair_errors": duplicate_errors,
        "invalid_score_error_count": len(invalid_scores),
        "invalid_score_errors": invalid_scores,
    }
    failures = conflicts or duplicate_errors or invalid_scores or len(old_map) != expected_old_judgments
    report["status"] = "error" if failures else "ok"
    if failures:
        raise JudgmentTransferError("judgment transfer validation failed", report)
    return output_rows, report


def write_transfer_report(
    output_directory: str | Path,
    report: Mapping[str, Any],
    *,
    old_workbook_sha256: str,
    new_workbook_sha256: str,
    overwrite: bool = False,
) -> None:
    for label, digest in (("old", old_workbook_sha256), ("new", new_workbook_sha256)):
        if len(digest) != 64 or any(character not in "0123456789abcdefABCDEF" for character in digest):
            raise ValueError(f"{label} workbook SHA-256 must contain 64 hexadecimal characters")
    directory = Path(output_directory)
    json_path = directory / "judgment-transfer-report.json"
    markdown_path = directory / "judgment-transfer-report.md"
    if not overwrite:
        existing = [path.name for path in (json_path, markdown_path) if path.exists()]
        if existing:
            raise ValueError(f"output already exists: {', '.join(existing)}")
    payload = {
        **dict(report),
        "old_workbook_sha256": old_workbook_sha256.lower(),
        "new_workbook_sha256": new_workbook_sha256.lower(),
    }
    write_json_atomically(json_path, payload, overwrite=overwrite)
    markdown = "\n".join(
        [
            "# IzveÅ¡taj o prenosu ocena relevantnosti",
            "",
            f"- OÄekivano starih ocena: {payload['expected_old_judgments']}",
            f"- UoÄeno starih ocena: {payload['observed_old_judgments']}",
            f"- Praznih redova u starom skupu: {payload['old_blank_row_count']}",
            f"- Redova u novom skupu: {payload['new_pool_row_count']}",
            f"- Preneto ocena: {payload['transferred_judgment_count']}",
            f"- Starih ocena bez para u novom skupu: {payload['unmatched_old_judgment_count']}",
            f"- Novih neocenjenih parova: {payload['new_unjudged_pair_count']}",
            f"- Konflikata: {payload['conflict_count']}",
            f"- GreÅ¡aka duplih parova: {payload['duplicate_pair_error_count']}",
            f"- GreÅ¡aka ocena: {payload['invalid_score_error_count']}",
            f"- SHA-256 starog radnog lista: `{payload['old_workbook_sha256']}`",
            f"- SHA-256 novog radnog lista: `{payload['new_workbook_sha256']}`",
            "",
            "Ocene su povezane iskljuÄivo stabilnim parom `(query_id, publication_id)`; nove ocene nisu izmiÅ¡ljene.",
            "",
        ]
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="transfer relevance judgments by stable pair")
    commands = parser.add_subparsers(dest="command", required=True)
    apply_command = commands.add_parser("apply")
    apply_command.add_argument("--old-rows", required=True)
    apply_command.add_argument("--new-rows", required=True)
    apply_command.add_argument("--output-rows", required=True)
    apply_command.add_argument("--output-report", required=True)
    apply_command.add_argument("--expected-old-judgments", type=int, required=True)
    report_command = commands.add_parser("report")
    report_command.add_argument("--transfer-report", required=True)
    report_command.add_argument("--output-dir", required=True)
    report_command.add_argument("--old-workbook", required=True)
    report_command.add_argument("--new-workbook", required=True)
    args = parser.parse_args(argv)
    if args.command == "apply":
        old_rows = read_json(args.old_rows)
        new_rows = read_json(args.new_rows)
        if not isinstance(old_rows, list) or not isinstance(new_rows, list):
            raise ValueError("row inputs must be JSON arrays")
        rows, report = transfer_judgments(
            old_rows,
            new_rows,
            expected_old_judgments=args.expected_old_judgments,
        )
        write_json_atomically(args.output_rows, rows)
        write_json_atomically(args.output_report, report)
        return 0
    report = read_json(args.transfer_report)
    if not isinstance(report, dict):
        raise ValueError("transfer report must be a JSON object")
    write_transfer_report(
        args.output_dir,
        report,
        old_workbook_sha256=sha256_file(args.old_workbook),
        new_workbook_sha256=sha256_file(args.new_workbook),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
