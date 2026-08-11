import json

import pytest

from evaluation.io import load_query_metadata


def _write(path, rows, *, top_extra=False):
    payload = {"query_metadata": rows}
    if top_extra:
        payload["extra"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _rows():
    return [
        {"query_id": "q1", "language": "sr", "script": "latin", "category": "koncept", "topic": "veštačka inteligencija"},
        {"query_id": "q2", "language": "sr", "script": "ћирилица", "category": "питање", "topic": "образовање"},
        {"query_id": "q3", "language": "en", "script": "latin", "category": "control", "topic": "information retrieval"},
        {"query_id": "q4", "language": "mixed", "script": "latin", "category": "technical", "topic": "NLP obrada"},
    ]


def test_valid_complete_query_metadata_preserves_utf8(tmp_path) -> None:
    path = _write(tmp_path / "metadata.json", list(reversed(_rows())))
    records = load_query_metadata(path, {"q1", "q2", "q3", "q4"})
    assert {record.query_id for record in records} == {"q1", "q2", "q3", "q4"}
    assert next(record for record in records if record.query_id == "q2").topic == "образовање"


def test_duplicate_missing_and_unknown_query_metadata_fail(tmp_path) -> None:
    rows = _rows()
    with pytest.raises(ValueError, match="duplicate"):
        load_query_metadata(_write(tmp_path / "duplicate.json", [rows[0], rows[0]]), {"q1"})
    with pytest.raises(ValueError, match="missing"):
        load_query_metadata(_write(tmp_path / "missing.json", rows[:-1]), {row["query_id"] for row in rows})
    unknown = [*rows, {**rows[0], "query_id": "unknown"}]
    with pytest.raises(ValueError, match="unknown"):
        load_query_metadata(_write(tmp_path / "unknown.json", unknown), {row["query_id"] for row in rows})


@pytest.mark.parametrize("field", ["query_id", "language", "script", "category", "topic"])
def test_blank_query_metadata_value_fails(tmp_path, field) -> None:
    rows = _rows()
    rows[0][field] = "   "
    with pytest.raises(ValueError, match="nonblank"):
        load_query_metadata(_write(tmp_path / f"blank-{field}.json", rows), {row["query_id"] for row in _rows()})


def test_extra_or_non_string_metadata_fields_fail(tmp_path) -> None:
    rows = _rows()
    rows[0]["extra"] = "x"
    with pytest.raises(ValueError, match="exactly"):
        load_query_metadata(_write(tmp_path / "extra-row.json", rows), {row["query_id"] for row in _rows()})
    with pytest.raises(ValueError, match="only"):
        load_query_metadata(_write(tmp_path / "extra-top.json", _rows(), top_extra=True), {row["query_id"] for row in _rows()})
    rows = _rows()
    rows[0]["topic"] = 123
    with pytest.raises(ValueError, match="nonblank"):
        load_query_metadata(_write(tmp_path / "type.json", rows), {row["query_id"] for row in _rows()})
