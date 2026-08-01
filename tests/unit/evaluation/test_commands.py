import csv
import json

import pytest

import evaluation.cli as cli_module
from evaluation.cli import main
from evaluation.judgment_import import POOL_COLUMNS


def _write(path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _pool_inputs(tmp_path, *, methods=("bm25", "vector_only", "full_pipeline")):
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
                    if method != "bm25"
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
    query_metadata = tmp_path / "query-metadata.json"
    runs = tmp_path / "runs.json"
    candidates = tmp_path / "candidates.csv"
    report_dir = tmp_path / "report"
    _write(queries, {"queries": [{"query_id": "q1", "text": "synthetic query"}]})
    _write(judgments, {"judgments": [{"query_id": "q1", "publication_id": "d1", "relevance": 2}]})
    _write(
        query_metadata,
        {
            "query_metadata": [
                {
                    "query_id": "q1",
                    "language": "sr",
                    "script": "latin",
                    "category": "synthetic",
                    "topic": "synthetic topic",
                }
            ]
        },
    )
    _write(
        runs,
        {
            "runs": [
                {
                    "query_id": "q1",
                    "method": "bm25",
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
                "--query-metadata",
                str(query_metadata),
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
    assert "embedding_model_revision" not in report["metadata"]
    assert "embedding_template_version" not in report["metadata"]


def test_import_judgments_and_agreement_commands_write_validated_outputs(tmp_path) -> None:
    queries = tmp_path / "queries.json"
    template = tmp_path / "template.csv"
    assessment = tmp_path / "assessment.csv"
    judgments = tmp_path / "judgments.json"
    _write(queries, {"queries": [{"query_id": "q1", "text": "veštačka inteligencija"}]})
    candidate = {
        "candidate_id": "q1-C0001",
        "query_text": "veštačka inteligencija",
        "query_id": "q1",
        "publication_id": "d1",
        "title": "Veštačka inteligencija",
        "abstract": "Sadržaj",
        "source_url": "https://example.test/d1",
        "relevance": "",
    }
    for path, grade in ((template, ""), (assessment, "2")):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(POOL_COLUMNS), lineterminator="\n")
            writer.writeheader()
            writer.writerow({**candidate, "relevance": grade})

    assert main(
        [
            "import-judgments",
            "--queries",
            str(queries),
            "--pool-template",
            str(template),
            "--assessment",
            str(assessment),
            "--output",
            str(judgments),
        ]
    ) == 0
    assert json.loads(judgments.read_text(encoding="utf-8"))["judgments"][0]["relevance"] == 2

    judgments_b = tmp_path / "judgments-b.json"
    judgments_b.write_bytes(judgments.read_bytes())
    agreement = tmp_path / "agreement"
    assert main(
        [
            "agreement",
            "--judgments-a",
            str(judgments),
            "--judgments-b",
            str(judgments_b),
            "--output-dir",
            str(agreement),
        ]
    ) == 0
    assert json.loads((agreement / "agreement.json").read_text(encoding="utf-8"))[
        "exact_agreement_percentage"
    ] == 100.0

    with pytest.raises(ValueError, match="distinct source files"):
        main(
            [
                "agreement",
                "--judgments-a",
                str(judgments),
                "--judgments-b",
                str(judgments),
                "--output-dir",
                str(tmp_path / "same-file"),
            ]
        )


def test_agreement_output_directory_cannot_contain_source_judgments(tmp_path) -> None:
    output = tmp_path / "agreement"
    output.mkdir()
    source_a = output / "a.json"
    source_b = output / "b.json"
    payload = {"judgments": [{"query_id": "q1", "publication_id": "d1", "relevance": 1}]}
    _write(source_a, payload)
    _write(source_b, payload)

    with pytest.raises(ValueError, match="contain an input"):
        main(
            [
                "agreement",
                "--judgments-a",
                str(source_a),
                "--judgments-b",
                str(source_b),
                "--output-dir",
                str(output),
                "--overwrite",
            ]
        )
    assert source_a.is_file() and source_b.is_file()


def test_candidate_pool_accepts_complete_matrix_including_empty_runs(tmp_path) -> None:
    queries, runs = _pool_inputs(tmp_path)
    output = tmp_path / "pool.csv"

    assert main(["candidate-pool", "--queries", str(queries), "--runs", str(runs), "--output", str(output)]) == 0
    assert output.is_file()


def test_candidate_pool_missing_matrix_entry_fails_before_writing(tmp_path) -> None:
    queries, runs = _pool_inputs(tmp_path, methods=("bm25", "vector_only"))
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


def test_collect_runs_cli_rejects_missing_or_whitespace_secrets_by_variable_name(
    tmp_path, monkeypatch, capsys
) -> None:
    queries = tmp_path / "queries.json"
    _write(queries, {"queries": [{"query_id": "q1", "text": "query"}]})
    monkeypatch.delenv("COLLECTOR_DB", raising=False)
    monkeypatch.setenv("COLLECTOR_TOKEN", "   ")

    with pytest.raises(SystemExit, match="COLLECTOR_DB") as error:
        main(
            [
                "collect-runs",
                "--queries",
                str(queries),
                "--output",
                str(tmp_path / "runs.json"),
                "--database-url-env",
                "COLLECTOR_DB",
                "--api-token-env",
                "COLLECTOR_TOKEN",
                "--embedding-service-url",
                "http://embedding.test",
                "--full-pipeline-url",
                "http://gateway.test/api/search",
                "--embedding-model",
                "model",
                "--expected-corpus-size",
                "1",
                "--expected-snapshot-hash",
                "a" * 64,
            ]
        )
    captured = capsys.readouterr()
    combined = str(error.value) + captured.out + captured.err
    assert "COLLECTOR_DB" in combined
    assert "sentinel-password" not in combined


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_collect_runs_cli_rejects_invalid_timeout_before_runtime_setup(tmp_path, timeout) -> None:
    queries = tmp_path / "queries.json"
    _write(queries, {"queries": [{"query_id": "q1", "text": "query"}]})
    with pytest.raises(SystemExit, match="finite and positive"):
        main(
            [
                "collect-runs",
                "--queries",
                str(queries),
                "--output",
                str(tmp_path / "runs.json"),
                "--request-timeout",
                timeout,
                "--embedding-service-url",
                "http://embedding.test",
                "--full-pipeline-url",
                "http://gateway.test/api/search",
                "--embedding-model",
                "model",
                "--expected-corpus-size",
                "1",
                "--expected-snapshot-hash",
                "a" * 64,
            ]
        )


def test_collect_runs_cli_passes_protected_configuration_without_printing_secrets(
    tmp_path, monkeypatch, capsys
) -> None:
    queries = tmp_path / "queries.json"
    output = tmp_path / "runs.json"
    _write(queries, {"queries": [{"query_id": "q1", "text": "veštačka inteligencija"}]})
    monkeypatch.setenv("COLLECTOR_DB", "postgresql://user:sentinel-password@database/eval")
    monkeypatch.setenv("COLLECTOR_TOKEN", "sentinel-token")
    captured_arguments = {}

    class Client:
        def __init__(self, **kwargs):
            captured_arguments["client"] = kwargs

    async def fake_run_collection(**kwargs):
        captured_arguments["run"] = kwargs
        output.write_text('{"runs": []}', encoding="utf-8")

    monkeypatch.setattr(cli_module, "EvaluationServiceClient", Client)
    monkeypatch.setattr(cli_module, "run_collection", fake_run_collection)

    assert (
        main(
            [
                "collect-runs",
                "--queries",
                str(queries),
                "--output",
                str(output),
                "--database-url-env",
                "COLLECTOR_DB",
                "--api-token-env",
                "COLLECTOR_TOKEN",
                "--embedding-service-url",
                "http://embedding.test",
                "--full-pipeline-url",
                "http://gateway.test/api/search",
                "--embedding-model",
                "model",
                "--expected-corpus-size",
                "1",
                "--expected-snapshot-hash",
                "a" * 64,
            ]
        )
        == 0
    )
    combined = capsys.readouterr().out + output.read_text(encoding="utf-8")
    assert "sentinel-password" not in combined
    assert "sentinel-token" not in combined
    assert captured_arguments["client"]["api_token"] == "sentinel-token"
    assert captured_arguments["run"]["queries"][0].text == "veštačka inteligencija"
