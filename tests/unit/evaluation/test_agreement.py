import csv
import json

import pytest

import evaluation.agreement as agreement_module
from evaluation.agreement import compare_judgments, write_agreement_report
from evaluation.models import Judgment


def _judgments(grades):
    return [Judgment("q1", f"d{index}", grade) for index, grade in enumerate(grades, start=1)]


def test_perfect_agreement_has_identity_matrix_and_unit_kappas() -> None:
    report = compare_judgments(_judgments([0, 1, 2]), _judgments([0, 1, 2]))
    assert report["exact_agreement_percentage"] == 100.0
    assert report["confusion_matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert report["unweighted_cohen_kappa"] == pytest.approx(1.0)
    assert report["quadratic_weighted_cohen_kappa"] == pytest.approx(1.0)
    assert report["disagreements"] == []


def test_partial_agreement_formulas_and_disagreement_export(tmp_path) -> None:
    report = compare_judgments(
        _judgments([0, 0, 1, 1, 2, 2]),
        _judgments([0, 1, 1, 2, 2, 2]),
        input_sha256={"a": "a" * 64, "b": "b" * 64},
    )
    assert report["exact_agreement_percentage"] == pytest.approx(100 * 4 / 6)
    assert report["unweighted_cohen_kappa"] == pytest.approx(0.5)
    assert report["quadratic_weighted_cohen_kappa"] == pytest.approx(0.75)
    assert report["confusion_matrix"] == [[1, 1, 0], [0, 1, 1], [0, 0, 2]]

    write_agreement_report(tmp_path / "agreement", report)
    payload = json.loads((tmp_path / "agreement" / "agreement.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["input_sha256"]["a"] == "a" * 64
    with (tmp_path / "agreement" / "disagreements.csv").open(encoding="utf-8") as stream:
        disagreements = list(csv.DictReader(stream))
    assert [(row["publication_id"], row["assessor_a_relevance"], row["assessor_b_relevance"]) for row in disagreements] == [
        ("d2", "0", "1"),
        ("d4", "1", "2"),
    ]


def test_complete_opposition_has_negative_unit_kappas() -> None:
    report = compare_judgments(_judgments([0, 0, 2, 2]), _judgments([2, 2, 0, 0]))
    assert report["exact_agreement_percentage"] == 0
    assert report["unweighted_cohen_kappa"] == pytest.approx(-1.0)
    assert report["quadratic_weighted_cohen_kappa"] == pytest.approx(-1.0)


def test_zero_denominator_kappas_are_null_not_fabricated() -> None:
    report = compare_judgments(_judgments([1, 1]), _judgments([1, 1]))
    assert report["exact_agreement_percentage"] == 100.0
    assert report["unweighted_cohen_kappa"] is None
    assert report["quadratic_weighted_cohen_kappa"] is None


def test_pair_mismatch_duplicates_and_empty_inputs_fail() -> None:
    with pytest.raises(ValueError, match="pair mismatch"):
        compare_judgments(_judgments([0, 1]), _judgments([0]))
    duplicate = [Judgment("q1", "d1", 0), Judgment("q1", "d1", 1)]
    with pytest.raises(ValueError, match="duplicate"):
        compare_judgments(duplicate, _judgments([0]))
    with pytest.raises(ValueError, match="at least one"):
        compare_judgments([], [])


def test_agreement_output_is_atomic_and_existing_directory_is_preserved(tmp_path, monkeypatch) -> None:
    output = tmp_path / "agreement"
    output.mkdir()
    marker = output / "marker.txt"
    marker.write_text("existing", encoding="utf-8")
    report = compare_judgments(_judgments([0, 1]), _judgments([0, 1]))
    with pytest.raises(ValueError, match="already exists"):
        write_agreement_report(output, report)
    assert marker.read_text(encoding="utf-8") == "existing"

    monkeypatch.setattr(
        agreement_module,
        "_write_agreement_files",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic write failure")),
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        write_agreement_report(output, report, overwrite=True)
    assert marker.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".agreement.*.tmp"))
