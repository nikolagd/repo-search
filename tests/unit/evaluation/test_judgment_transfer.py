import json

import pytest

from evaluation.judgment_transfer import (
    JudgmentTransferError,
    transfer_judgments,
    write_transfer_report,
)


def _row(query_id, publication_id, relevance=""):
    return {
        "candidate_id": f"{query_id}-{publication_id}",
        "query_id": query_id,
        "publication_id": publication_id,
        "relevance": relevance,
    }


def test_judgments_transfer_by_stable_pair_not_candidate_id() -> None:
    old = [_row("q1", "p1", 2), _row("q1", "p2", 0)]
    new = [
        {**_row("q1", "p2"), "candidate_id": "new-C1"},
        {**_row("q1", "p3"), "candidate_id": "new-C2"},
        {**_row("q1", "p1"), "candidate_id": "new-C3"},
    ]

    rows, report = transfer_judgments(old, new, expected_old_judgments=2)

    assert [row["relevance"] for row in rows] == [0, "", 2]
    assert report["transferred_judgment_count"] == 2
    assert report["new_unjudged_pairs"] == [{"query_id": "q1", "publication_id": "p3"}]
    assert report["unmatched_old_judgments"] == []


def test_transfer_reports_unmatched_old_judgments() -> None:
    rows, report = transfer_judgments(
        [_row("q1", "old-only", 1)],
        [_row("q1", "new-only")],
        expected_old_judgments=1,
    )

    assert rows[0]["relevance"] == ""
    assert report["unmatched_old_judgments"] == [
        {"query_id": "q1", "publication_id": "old-only", "relevance": 1}
    ]
    assert report["new_unjudged_pair_count"] == 1


@pytest.mark.parametrize(
    "old_rows,new_rows,error_key",
    [
        ([_row("q1", "p1", 1), _row("q1", "p1", 2)], [_row("q1", "p1")], "conflict_count"),
        ([_row("q1", "p1", 1), _row("q1", "p1", 1)], [_row("q1", "p1")], "duplicate_pair_error_count"),
        ([_row("q1", "p1", 3)], [_row("q1", "p1")], "invalid_score_error_count"),
        ([_row("q1", "p1", 1)], [_row("q1", "p1"), _row("q1", "p1")], "duplicate_pair_error_count"),
    ],
)
def test_conflicting_duplicate_or_invalid_scores_are_rejected(old_rows, new_rows, error_key) -> None:
    with pytest.raises(JudgmentTransferError) as error:
        transfer_judgments(old_rows, new_rows, expected_old_judgments=1)
    assert error.value.report[error_key] > 0


def test_transfer_report_records_counts_and_workbook_hashes(tmp_path) -> None:
    _, report = transfer_judgments([_row("q1", "p1", 2)], [_row("q1", "p1")], expected_old_judgments=1)
    write_transfer_report(
        tmp_path,
        report,
        old_workbook_sha256="a" * 64,
        new_workbook_sha256="b" * 64,
    )

    payload = json.loads((tmp_path / "judgment-transfer-report.json").read_text(encoding="utf-8"))
    assert payload["transferred_judgment_count"] == 1
    assert payload["old_workbook_sha256"] == "a" * 64
    assert payload["new_workbook_sha256"] == "b" * 64
    assert "Preneto ocena: 1" in (tmp_path / "judgment-transfer-report.md").read_text(encoding="utf-8")
