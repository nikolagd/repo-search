import csv
import json

import pytest

import evaluation.io as io_module
from evaluation.judgment_import import POOL_COLUMNS, import_judgments
from evaluation.models import EvaluationQuery


def _queries():
    return [
        EvaluationQuery("q1", "veštačka inteligencija"),
        EvaluationQuery("q2", "примена вештачке интелигенције"),
        EvaluationQuery("q3", "information retrieval"),
    ]


def _rows():
    return [
        {
            "candidate_id": "q1-C0001",
            "query_text": "veštačka inteligencija",
            "query_id": "q1",
            "publication_id": "d1",
            "title": "Veštačka inteligencija u obrazovanju",
            "abstract": "Č, ć, š, ž i đ ostaju neizmenjeni.",
            "source_url": "https://example.test/d1",
            "relevance": "",
        },
        {
            "candidate_id": "q2-C0001",
            "query_text": "примена вештачке интелигенције",
            "query_id": "q2",
            "publication_id": "d2",
            "title": "Примена вештачке интелигенције",
            "abstract": "Ћирилички садржај",
            "source_url": "",
            "relevance": "",
        },
        {
            "candidate_id": "q3-C0001",
            "query_text": "information retrieval",
            "query_id": "q3",
            "publication_id": "d3",
            "title": "Information retrieval",
            "abstract": "",
            "source_url": "https://example.test/d3",
            "relevance": "",
        },
    ]


def _write_csv(path, rows, fieldnames=POOL_COLUMNS, *, bom=False):
    encoding = "utf-8-sig" if bom else "utf-8"
    with path.open("w", encoding=encoding, newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(fieldnames),
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def _paths(tmp_path, assessment_rows=None):
    template = _write_csv(tmp_path / "template.csv", _rows())
    assessed = [dict(row) for row in (assessment_rows or _rows())]
    for row, grade in zip(assessed, ("0", "1", "2")):
        row["relevance"] = grade
    assessment = _write_csv(tmp_path / "assessment.csv", assessed, bom=True)
    return template, assessment


def test_valid_complete_assessment_writes_only_schema_judgments_and_preserves_utf8(tmp_path) -> None:
    template, assessment = _paths(tmp_path)
    output = tmp_path / "judgments.json"

    judgments = import_judgments(_queries(), template, assessment, output)

    assert [(item.query_id, item.publication_id, item.relevance) for item in judgments] == [
        ("q1", "d1", 0),
        ("q2", "d2", 1),
        ("q3", "d3", 2),
    ]
    raw = output.read_text(encoding="utf-8")
    assert "q2" in raw
    assert "candidate_id" not in raw
    assert "title" not in raw
    assert set(json.loads(raw)["judgments"][0]) == {"query_id", "publication_id", "relevance"}


def test_assessment_row_order_may_change_without_changing_identity(tmp_path) -> None:
    rows = list(reversed(_rows()))
    template, assessment = _paths(tmp_path, rows)
    output = tmp_path / "judgments.json"
    assert len(import_judgments(_queries(), template, assessment, output)) == 3


@pytest.mark.parametrize("value", ["", "true", "1.0", "1 relevant", "-1", "3", " 1", "1 "])
def test_invalid_relevance_is_rejected_without_output(tmp_path, value) -> None:
    rows = _rows()
    template = _write_csv(tmp_path / "template.csv", rows)
    assessment_rows = [dict(row) for row in rows]
    for row in assessment_rows:
        row["relevance"] = "1"
    assessment_rows[0]["relevance"] = value
    assessment = _write_csv(tmp_path / "assessment.csv", assessment_rows)
    output = tmp_path / "judgments.json"

    with pytest.raises(ValueError, match="exactly integer 0, 1, or 2"):
        import_judgments(_queries(), template, assessment, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".judgments.json.*.tmp"))


def test_duplicate_candidate_and_duplicate_pair_are_rejected(tmp_path) -> None:
    for field, message in (("candidate_id", "duplicate candidate"), ("publication_id", "duplicate query/publication")):
        rows = _rows()
        duplicate = dict(rows[0])
        duplicate["candidate_id"] = "other" if field == "publication_id" else rows[0]["candidate_id"]
        rows.insert(1, duplicate)
        template = _write_csv(tmp_path / f"{field}-template.csv", rows)
        assessment = _write_csv(tmp_path / f"{field}-assessment.csv", rows)
        with pytest.raises(ValueError, match=message):
            import_judgments(_queries(), template, assessment, tmp_path / f"{field}.json")


def test_missing_and_additional_candidates_are_rejected(tmp_path) -> None:
    template_rows = _rows()
    template = _write_csv(tmp_path / "template.csv", template_rows)
    missing = [dict(row, relevance="1") for row in template_rows[:-1]]
    with pytest.raises(ValueError, match="coverage mismatch"):
        import_judgments(
            _queries(), template, _write_csv(tmp_path / "missing.csv", missing), tmp_path / "missing.json"
        )

    additional = [dict(row, relevance="1") for row in template_rows]
    extra = dict(additional[-1], candidate_id="q3-C0002", publication_id="d4")
    additional.append(extra)
    with pytest.raises(ValueError, match="coverage mismatch"):
        import_judgments(
            _queries(),
            template,
            _write_csv(tmp_path / "additional.csv", additional),
            tmp_path / "additional.json",
        )


@pytest.mark.parametrize("field", ["query_text", "publication_id", "title", "abstract", "source_url"])
def test_altered_candidate_metadata_is_rejected(tmp_path, field) -> None:
    rows = _rows()
    template = _write_csv(tmp_path / "template.csv", rows)
    assessed = [dict(row, relevance="1") for row in rows]
    assessed[0][field] += " changed"
    message = "query text mismatch" if field == "query_text" else f"changed {field}"
    with pytest.raises(ValueError, match=message):
        import_judgments(
            _queries(), template, _write_csv(tmp_path / "assessment.csv", assessed), tmp_path / "out.json"
        )


def test_swapped_candidate_identity_and_unknown_query_are_rejected(tmp_path) -> None:
    rows = _rows()
    template = _write_csv(tmp_path / "template.csv", rows)
    assessed = [dict(row, relevance="1") for row in rows]
    assessed[0]["candidate_id"], assessed[1]["candidate_id"] = (
        assessed[1]["candidate_id"],
        assessed[0]["candidate_id"],
    )
    with pytest.raises(ValueError, match="changed"):
        import_judgments(
            _queries(), template, _write_csv(tmp_path / "swapped.csv", assessed), tmp_path / "out.json"
        )

    unknown = _rows()
    unknown[0]["query_id"] = "unknown"
    unknown_template = _write_csv(tmp_path / "unknown-template.csv", unknown)
    with pytest.raises(ValueError, match="unknown query"):
        import_judgments(
            _queries(), unknown_template, _write_csv(tmp_path / "unknown.csv", unknown), tmp_path / "unknown.json"
        )


def test_exact_columns_and_existing_output_protection(tmp_path) -> None:
    rows = _rows()
    wrong_columns = [*POOL_COLUMNS, "extra"]
    wrong_rows = [dict(row, extra="x") for row in rows]
    template = _write_csv(tmp_path / "wrong.csv", wrong_rows, wrong_columns)
    assessment = _write_csv(tmp_path / "assessment.csv", wrong_rows, wrong_columns)
    with pytest.raises(ValueError, match="exact expected columns"):
        import_judgments(_queries(), template, assessment, tmp_path / "out.json")

    template, assessment = _paths(tmp_path)
    output = tmp_path / "existing.json"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        import_judgments(_queries(), template, assessment, output)
    assert output.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize(
    "fieldnames",
    [
        POOL_COLUMNS[:-1],
        (POOL_COLUMNS[1], POOL_COLUMNS[0], *POOL_COLUMNS[2:]),
        (*POOL_COLUMNS[:-1], "source_url"),
    ],
)
def test_missing_reordered_or_duplicate_headers_fail(tmp_path, fieldnames) -> None:
    template = _write_csv(tmp_path / "template.csv", _rows(), fieldnames)
    assessment = _write_csv(tmp_path / "assessment.csv", _rows(), fieldnames)
    with pytest.raises(ValueError, match="exact expected columns"):
        import_judgments(_queries(), template, assessment, tmp_path / "out.json")


@pytest.mark.parametrize(("label", "field"), [("template", "candidate_id"), ("assessment", "query_id")])
def test_whitespace_only_identity_values_fail(tmp_path, label, field) -> None:
    template_rows = _rows()
    assessment_rows = [dict(row, relevance="1") for row in _rows()]
    target = template_rows if label == "template" else assessment_rows
    target[0][field] = "   "
    template = _write_csv(tmp_path / "template.csv", template_rows)
    assessment = _write_csv(tmp_path / "assessment.csv", assessment_rows)
    with pytest.raises(ValueError, match="blank"):
        import_judgments(_queries(), template, assessment, tmp_path / "out.json")


def test_malformed_unterminated_csv_quote_fails(tmp_path) -> None:
    template = _write_csv(tmp_path / "template.csv", _rows())
    header = ",".join(POOL_COLUMNS)
    values = ["c1", "veštačka inteligencija", "q1", "d1", "title", "abstract", "url", '"1']
    assessment = tmp_path / "malformed.csv"
    assessment.write_text(header + "\n" + ",".join(values), encoding="utf-8")
    with pytest.raises(ValueError, match="malformed CSV"):
        import_judgments(_queries(), template, assessment, tmp_path / "out.json")


def test_atomic_publish_failure_preserves_existing_output_and_cleans_temp(tmp_path, monkeypatch) -> None:
    template, assessment = _paths(tmp_path)
    output = tmp_path / "judgments.json"
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(
        io_module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic replace failure")),
    )
    with pytest.raises(OSError, match="synthetic"):
        import_judgments(_queries(), template, assessment, output, overwrite=True)
    assert output.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".judgments.json.*.tmp"))


def test_no_overwrite_race_does_not_clobber_new_destination(tmp_path, monkeypatch) -> None:
    template, assessment = _paths(tmp_path)
    output = tmp_path / "judgments.json"

    def concurrent_create(_temporary, destination):
        destination.write_text("concurrent", encoding="utf-8")
        raise FileExistsError

    monkeypatch.setattr(io_module.os, "link", concurrent_create)
    with pytest.raises(ValueError, match="already exists"):
        import_judgments(_queries(), template, assessment, output)
    assert output.read_text(encoding="utf-8") == "concurrent"
    assert not list(tmp_path.glob(".judgments.json.*.tmp"))


def test_invalid_assessment_with_overwrite_preserves_existing_output(tmp_path) -> None:
    template, _assessment = _paths(tmp_path)
    output = tmp_path / "judgments.json"
    output.write_text("existing", encoding="utf-8")

    invalid_rows = _rows()
    for row in invalid_rows:
        row["relevance"] = ""
    invalid = _write_csv(tmp_path / "invalid.csv", invalid_rows)
    with pytest.raises(ValueError):
        import_judgments(_queries(), template, invalid, output, overwrite=True)
    assert output.read_text(encoding="utf-8") == "existing"
