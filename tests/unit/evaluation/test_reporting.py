import csv
import json

from evaluation.models import Judgment, QueryRun, RetrievedItem
from evaluation.reporting import build_report, write_report


def test_report_records_required_metadata_parser_counts_and_latency(tmp_path) -> None:
    runs = [
        QueryRun("q1", "full_pipeline", [RetrievedItem("d1", 0.9)], 12.5, "llm"),
        QueryRun("q2", "full_pipeline", [], 7.5, "fallback"),
    ]
    report = build_report(
        runs,
        [Judgment("q1", "d1", 2)],
        git_commit="abc123",
        corpus_size=50,
        query_count=2,
        k_values=[1, 5],
        embedding_model="model-a",
        ranking_configuration={"candidate_multiplier": 6},
        evaluated_at="2026-07-11T12:00:00+00:00",
    )

    assert report["metadata"] == {
        "git_commit": "abc123",
        "evaluation_timestamp": "2026-07-11T12:00:00+00:00",
        "corpus_size": 50,
        "query_count": 2,
        "k_values": [1, 5],
        "embedding_model": "model-a",
        "ranking_configuration": {"candidate_multiplier": 6},
        "parser_mode_counts": {"fallback": 1, "llm": 1},
    }
    aggregate = report["aggregate_metrics"][0]
    assert aggregate["mean_latency_ms"] == 10.0
    assert aggregate["queries_without_relevant_judgments"] == 1

    write_report(tmp_path, report)
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["metadata"]["git_commit"] == "abc123"
    with (tmp_path / "metrics.csv").open(encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))[0]["method"] == "full_pipeline"
    assert "Synthetic" not in (tmp_path / "summary.md").read_text(encoding="utf-8")
