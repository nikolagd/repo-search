import csv
import json
import math
from pathlib import Path

import pytest

import evaluation.reporting as reporting_module
from evaluation.metrics import evaluate_run
from evaluation.models import Judgment, QueryMetadata, QueryRun, RetrievedItem
from evaluation.reporting import build_report, nearest_rank_percentile, write_report


METHODS = ("keyword", "language_aware_lexical", "vector_only", "full_pipeline")


def _metadata():
    return [
        QueryMetadata("q1", "sr", "latin", "conceptual", "veštačka inteligencija"),
        QueryMetadata("q2", "en", "latin", "temporal", "information retrieval"),
    ]


def _runs():
    rows = []
    for query_id in ("q1", "q2"):
        for index, method in enumerate(METHODS, start=1):
            results = []
            if query_id == "q1" and method == "keyword":
                results = [RetrievedItem("d1", 2.0), RetrievedItem("unjudged", 1.0)]
            elif query_id == "q1" and method == "full_pipeline":
                results = [RetrievedItem("d2", 0.8)]
            rows.append(
                QueryRun(
                    query_id,
                    method,
                    results,
                    latency_ms=float(index + (10 if query_id == "q2" else 0)),
                    parser_mode="llm" if method == "full_pipeline" else None,
                )
            )
    return rows


def _report(
    runs=None,
    metadata=None,
    ranking_configuration=None,
    *,
    embedding_model_revision=None,
    embedding_template_version=None,
):
    return build_report(
        runs or _runs(),
        [Judgment("q1", "d1", 2), Judgment("q1", "d2", 0), Judgment("q2", "d3", 0)],
        metadata or _metadata(),
        git_commit="abc123",
        corpus_size=50,
        k_values=[1, 5],
        embedding_model="model-a",
        embedding_model_revision=embedding_model_revision,
        embedding_template_version=embedding_template_version,
        ranking_configuration=(
            {"candidate_multiplier": 6}
            if ranking_configuration is None
            else ranking_configuration
        ),
        input_sha256={"runs": "a" * 64},
        evaluated_at="2026-07-12T12:00:00+00:00",
    )


def test_per_query_metrics_reuse_existing_formulas_and_include_metadata() -> None:
    report = _report()
    row = next(
        item for item in report["per_query_metrics"] if item["query_id"] == "q1" and item["method"] == "keyword"
    )
    expected = evaluate_run(
        [RetrievedItem("d1", 2.0), RetrievedItem("unjudged", 1.0)],
        [Judgment("q1", "d1", 2), Judgment("q1", "d2", 0)],
        [1, 5],
    )
    for name in ("mrr", "mrr@5", "precision@1", "recall@5", "ndcg@5"):
        assert row[name] == expected[name]
    assert row["language"] == "sr"
    assert row["script"] == "latin"
    assert row["category"] == "conceptual"
    assert row["topic"] == "veštačka inteligencija"
    assert row["retrieved_result_count"] == 2
    assert row["positively_judged_document_count"] == 1
    assert row["no_positive_judgments"] is False


def test_grouped_metrics_have_equal_method_coverage_and_zero_positive_queries() -> None:
    report = _report()
    assert {row["grouping_dimension"] for row in report["grouped_metrics"]} == {
        "language",
        "script",
        "category",
    }
    for dimension in ("language", "script", "category"):
        for group in {row["group_value"] for row in report["grouped_metrics"] if row["grouping_dimension"] == dimension}:
            rows = [
                row
                for row in report["grouped_metrics"]
                if row["grouping_dimension"] == dimension and row["group_value"] == group
            ]
            assert {row["method"] for row in rows} == set(METHODS)
            assert len({row["query_count"] for row in rows}) == 1
    q2_rows = [row for row in report["per_query_metrics"] if row["query_id"] == "q2"]
    assert all(row["no_positive_judgments"] for row in q2_rows)
    assert all(row["mrr"] == 0 and row["recall@5"] == 0 and row["ndcg@5"] == 0 for row in q2_rows)
    latin_keyword = next(
        row
        for row in report["grouped_metrics"]
        if row["grouping_dimension"] == "script"
        and row["group_value"] == "latin"
        and row["method"] == "keyword"
    )
    aggregate_keyword = next(row for row in report["aggregate_metrics"] if row["method"] == "keyword")
    assert latin_keyword["mrr"] == aggregate_keyword["mrr"]
    assert latin_keyword["precision@1"] == aggregate_keyword["precision@1"]


def test_zero_result_runs_remain_and_parser_modes_are_not_fabricated() -> None:
    report = _report()
    zero = next(
        row for row in report["per_query_metrics"] if row["query_id"] == "q1" and row["method"] == "vector_only"
    )
    assert zero["retrieved_result_count"] == 0
    assert zero["precision@1"] == 0
    parser = report["parser_mode_summary"]
    assert next(row for row in parser if row["method"] == "keyword") == {
        "method": "keyword",
        "parser_mode": None,
        "applicability": "not_applicable",
        "run_count": 2,
        "percentage": 100.0,
    }
    assert next(row for row in parser if row["method"] == "vector_only")["parser_mode"] is None
    full = [row for row in parser if row["method"] == "full_pipeline"]
    assert full == [
        {
            "method": "full_pipeline",
            "parser_mode": "llm",
            "applicability": "reported",
            "run_count": 2,
            "percentage": 100.0,
        }
    ]


def test_parser_mode_split_percentages_and_unreported_mode_are_explicit() -> None:
    runs = _runs()
    runs = [
        QueryRun(run.query_id, run.method, run.results, run.latency_ms, None)
        if run.query_id == "q2" and run.method == "full_pipeline"
        else run
        for run in runs
    ]
    full = [row for row in _report(runs=runs)["parser_mode_summary"] if row["method"] == "full_pipeline"]
    assert full == [
        {
            "method": "full_pipeline",
            "parser_mode": "llm",
            "applicability": "reported",
            "run_count": 1,
            "percentage": 50.0,
        },
        {
            "method": "full_pipeline",
            "parser_mode": None,
            "applicability": "unreported",
            "run_count": 1,
            "percentage": 50.0,
        },
    ]


def test_latency_summary_and_nearest_rank_p95_are_deterministic() -> None:
    assert nearest_rank_percentile([float(value) for value in range(1, 21)], 0.95) == 19.0
    report = _report()
    keyword = next(row for row in report["latency_summary"] if row["method"] == "keyword")
    assert keyword == {
        "method": "keyword",
        "run_count": 2,
        "measured_run_count": 2,
        "missing_latency_count": 0,
        "mean_ms": 6.0,
        "median_ms": 6.0,
        "minimum_ms": 1.0,
        "maximum_ms": 11.0,
        "p95_ms": 11.0,
    }
    runs = _runs()
    runs[0] = QueryRun("q1", "keyword", runs[0].results, latency_ms=None)
    missing = next(
        row for row in _report(runs=runs)["latency_summary"] if row["method"] == "keyword"
    )
    assert missing["run_count"] == 2
    assert missing["measured_run_count"] == 1
    assert missing["missing_latency_count"] == 1
    assert missing["mean_ms"] == missing["p95_ms"] == 11.0


def test_report_metadata_and_output_files_are_machine_readable_and_deterministic(tmp_path) -> None:
    report = _report(runs=list(reversed(_runs())), metadata=list(reversed(_metadata())))
    assert report["metadata"]["methods"] == sorted(METHODS)
    assert report["metadata"]["input_sha256"] == {"runs": "a" * 64}
    assert report["metadata"]["judgment_grade_counts"] == {"0": 2, "1": 0, "2": 1}
    assert report["metadata"]["queries_without_positive_judgments"] == 1
    assert [(row["query_id"], row["method"]) for row in report["per_query_metrics"]] == sorted(
        (row["query_id"], row["method"]) for row in report["per_query_metrics"]
    )

    output = tmp_path / "report"
    write_report(output, report)
    assert set(path.name for path in output.iterdir()) == {
        "report.json",
        "metrics.csv",
        "per_query_metrics.csv",
        "grouped_metrics.csv",
        "latency_summary.csv",
        "parser_mode_summary.csv",
        "summary.md",
    }
    payload = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["git_commit"] == "abc123"
    with (output / "grouped_metrics.csv").open(encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))[0]["grouping_dimension"] == "language"
    summary = (output / "summary.md").read_text(encoding="utf-8")
    assert "synthetic overall score" in summary


def _assert_provenance_metadata_schema_compatible(report) -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "evaluation" / "schemas" / "report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    metadata_schema = schema["properties"]["metadata"]
    metadata = report["metadata"]
    assert set(metadata_schema["required"]) <= set(metadata)
    assert set(metadata) <= set(metadata_schema["properties"])
    for name in ("embedding_model_revision", "embedding_template_version"):
        if name in metadata:
            assert isinstance(metadata[name], str) and len(metadata[name]) >= 1


def test_historical_report_does_not_claim_current_provenance_and_remains_readable(tmp_path) -> None:
    legacy_report = _report()
    _assert_provenance_metadata_schema_compatible(legacy_report)

    output = tmp_path / "legacy-report"
    write_report(output, legacy_report)

    loaded = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert loaded["metadata"]["embedding_model"] == "model-a"
    assert "embedding_model_revision" not in loaded["metadata"]
    assert "embedding_template_version" not in loaded["metadata"]
    summary = (output / "summary.md").read_text(encoding="utf-8")
    assert summary.count("not recorded") >= 2


def test_explicit_provenance_is_retained_valid_and_rendered(tmp_path) -> None:
    report = _report(
        embedding_model_revision="verified-revision",
        embedding_template_version="verified-template-v1",
    )
    _assert_provenance_metadata_schema_compatible(report)
    assert report["metadata"]["embedding_model_revision"] == "verified-revision"
    assert report["metadata"]["embedding_template_version"] == "verified-template-v1"

    output = tmp_path / "new-report"
    write_report(output, report)
    loaded = json.loads((output / "report.json").read_text(encoding="utf-8"))
    _assert_provenance_metadata_schema_compatible(loaded)
    summary = (output / "summary.md").read_text(encoding="utf-8")
    assert "`verified-revision`" in summary
    assert "`verified-template-v1`" in summary


def test_incomplete_matrix_and_non_finite_values_fail_defensively() -> None:
    with pytest.raises(ValueError, match="incomplete comparison matrix"):
        _report(runs=_runs()[:-1])
    runs = _runs()
    runs[0] = QueryRun("q1", "keyword", [], latency_ms=float("nan"))
    with pytest.raises(ValueError, match="latency"):
        _report(runs=runs)
    runs = _runs()
    runs[0] = QueryRun("q1", "keyword", [RetrievedItem("d1", float("inf"))], latency_ms=1.0)
    with pytest.raises(ValueError, match="finite"):
        _report(runs=runs)


@pytest.mark.parametrize("parser_mode", [True, 1, "   "])
def test_invalid_full_pipeline_parser_mode_fails(parser_mode) -> None:
    runs = _runs()
    index = next(index for index, run in enumerate(runs) if run.method == "full_pipeline")
    run = runs[index]
    runs[index] = QueryRun(run.query_id, run.method, run.results, run.latency_ms, parser_mode)
    with pytest.raises(ValueError, match="parser_mode"):
        _report(runs=runs)


@pytest.mark.parametrize(
    "ranking_configuration",
    [
        {"api_token": "sentinel-token"},
        {"nested": {"database_url": "postgresql://user:password@db/name"}},
        {"endpoint": "postgresql://user:password@db/name"},
    ],
)
def test_sensitive_ranking_configuration_is_rejected(ranking_configuration) -> None:
    with pytest.raises(ValueError, match="sensitive|database URL") as error:
        _report(ranking_configuration=ranking_configuration)
    assert "sentinel-token" not in str(error.value)
    assert "password@db" not in str(error.value)


def test_report_directory_publication_is_atomic(tmp_path, monkeypatch) -> None:
    output = tmp_path / "report"
    output.mkdir()
    marker = output / "marker.txt"
    marker.write_text("existing", encoding="utf-8")
    report = _report()
    with pytest.raises(ValueError, match="already exists"):
        write_report(output, report)
    assert marker.read_text(encoding="utf-8") == "existing"

    monkeypatch.setattr(
        reporting_module,
        "_write_report_files",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        write_report(output, report, overwrite=True)
    assert marker.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".report.*.tmp"))


def test_report_directory_can_be_replaced_only_with_explicit_overwrite(tmp_path) -> None:
    output = tmp_path / "report"
    output.mkdir()
    (output / "old.txt").write_text("old", encoding="utf-8")
    write_report(output, _report(), overwrite=True)
    assert not (output / "old.txt").exists()
    assert json.loads((output / "report.json").read_text(encoding="utf-8"))["metadata"][
        "git_commit"
    ] == "abc123"
    assert not list(tmp_path.glob(".report.*.backup"))
