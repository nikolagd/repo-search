import csv
import json

import pytest

from evaluation.cli import main


def _write(path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _pool_inputs(tmp_path, *, methods=("keyword", "vector_only", "full_pipeline")):
    queries = tmp_path / "pool-queries.json"
    runs = tmp_path / "pool-runs.json"
    _write(queries, {"queries": [{"query_id": "q1", "text": "synthetic query"}]})
    _write(
        runs,
        {
            "runs": [
                {
                    "query_id": "q1",
                    "method": method,
                    "latency_ms": 1.0,
                    "parser_mode": "fallback" if method == "full_pipeline" else None,
                    "results": []
                    if method != "keyword"
                    else [{"rank": 1, "publication_id": "d1", "score": 1.0}],
                }
                for method in methods
            ]
        },
    )
    return queries, runs


def test_candidate_pool_and_report_commands_write_machine_readable_outputs(tmp_path) -> None:
    queries = tmp_path / "queries.json"
    judgments = tmp_path / "judgments.json"
    runs = tmp_path / "runs.json"
    candidates = tmp_path / "candidates.csv"
    report_dir = tmp_path / "report"
    _write(queries, {"queries": [{"query_id": "q1", "text": "synthetic query"}]})
    _write(judgments, {"judgments": [{"query_id": "q1", "publication_id": "d1", "relevance": 2}]})
    _write(
        runs,
        {
            "runs": [
                {
                    "query_id": "q1",
                    "method": "keyword",
                    "latency_ms": 1.0,
                    "parser_mode": None,
                    "results": [
                        {
                            "rank": 1,
                            "publication_id": "d1",
                            "score": 2.0,
                            "title": "Synthetic document",
                        }
                    ],
                },
                {
                    "query_id": "q1",
                    "method": "vector_only",
                    "latency_ms": 2.0,
                    "parser_mode": None,
                    "results": [
                        {
                            "rank": 1,
                            "publication_id": "d1",
                            "score": 0.9,
                            "title": "Synthetic document",
                        }
                    ],
                },
                {
                    "query_id": "q1",
                    "method": "full_pipeline",
                    "latency_ms": 3.0,
                    "parser_mode": "fallback",
                    "results": [],
                },
            ]
        },
    )

    assert (
        main(
            [
                "candidate-pool",
                "--queries",
                str(queries),
                "--runs",
                str(runs),
                "--output",
                str(candidates),
                "--depth",
                "5",
            ]
        )
        == 0
    )
    with candidates.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert "method" not in rows[0]
    assert rows[0]["query_text"] == "synthetic query"

    assert (
        main(
            [
                "report",
                "--queries",
                str(queries),
                "--judgments",
                str(judgments),
                "--runs",
                str(runs),
                "--output-dir",
                str(report_dir),
                "--corpus-size",
                "3",
                "--k",
                "1",
                "--embedding-model",
                "synthetic-model",
                "--ranking-config",
                '{"candidate_multiplier": 2}',
                "--git-commit",
                "test-commit",
            ]
        )
        == 0
    )
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["git_commit"] == "test-commit"
    assert report["metadata"]["query_count"] == 1


def test_candidate_pool_accepts_complete_matrix_including_empty_runs(tmp_path) -> None:
    queries, runs = _pool_inputs(tmp_path)
    output = tmp_path / "pool.csv"

    assert main(["candidate-pool", "--queries", str(queries), "--runs", str(runs), "--output", str(output)]) == 0
    assert output.is_file()


def test_candidate_pool_missing_matrix_entry_fails_before_writing(tmp_path) -> None:
    queries, runs = _pool_inputs(tmp_path, methods=("keyword", "vector_only"))
    output = tmp_path / "must-not-exist.csv"

    with pytest.raises(ValueError, match="incomplete comparison matrix"):
        main(["candidate-pool", "--queries", str(queries), "--runs", str(runs), "--output", str(output)])
    assert not output.exists()


def test_candidate_pool_unknown_method_fails(tmp_path) -> None:
    queries, runs = _pool_inputs(tmp_path, methods=("keyword", "vector_only", "other"))
    with pytest.raises(ValueError, match="unknown method"):
        main(["candidate-pool", "--queries", str(queries), "--runs", str(runs), "--output", str(tmp_path / "out.csv")])


def test_candidate_pool_duplicate_method_arguments_fail(tmp_path) -> None:
    queries, runs = _pool_inputs(tmp_path)
    with pytest.raises(ValueError, match="methods must be unique"):
        main(
            [
                "candidate-pool",
                "--queries",
                str(queries),
                "--runs",
                str(runs),
                "--output",
                str(tmp_path / "out.csv"),
                "--methods",
                "keyword",
                "keyword",
            ]
        )
