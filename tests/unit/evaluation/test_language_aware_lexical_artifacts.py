import csv
import json

from evaluation.io import sha256_file
from evaluation.language_aware_lexical_artifacts import build_artifacts


START_COMMIT = "b" * 40
SOURCE_COMMIT = "c" * 40


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _inputs(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    queries = tmp_path / "queries.json"
    metadata = tmp_path / "query-metadata.json"
    snapshot = tmp_path / "snapshot.json"
    frozen = tmp_path / "frozen-runs.json"
    judgments = tmp_path / "judgments.json"
    publications = [
        {
            "publication_id": "p1",
            "title": "Application",
            "abstract": None,
            "date": "2022-01-01T00:00:00",
            "source_url": "https://example.test/p1",
        },
        {
            "publication_id": "p2",
            "title": "Učenje",
            "abstract": None,
            "date": "2021-01-01T00:00:00",
            "source_url": "https://example.test/p2",
        },
        {
            "publication_id": "p3",
            "title": "Unrelated",
            "abstract": None,
            "date": "2020-01-01T00:00:00",
            "source_url": "https://example.test/p3",
        },
    ]
    _write(
        queries,
        {
            "queries": [
                {"query_id": "q1", "text": "applications"},
                {"query_id": "q2", "text": "učenja"},
            ]
        },
    )
    _write(
        metadata,
        {
            "query_metadata": [
                {
                    "query_id": "q1",
                    "language": "English",
                    "script": "Latin",
                    "category": "control",
                    "topic": "application",
                },
                {
                    "query_id": "q2",
                    "language": "Serbian",
                    "script": "Latin",
                    "category": "inflection",
                    "topic": "učenje",
                },
            ]
        },
    )
    _write(
        snapshot,
        {"snapshot_format": "repo-search-corpus-v1", "publications": publications},
    )
    frozen_rows = []
    for query_id in ("q1", "q2"):
        for index, method in enumerate(
            ("language_independent_lexical", "vector_only", "full_pipeline"),
            start=1,
        ):
            frozen_rows.append(
                {
                    "query_id": query_id,
                    "method": method,
                    "latency_ms": float(index),
                    "parser_mode": "fallback" if method == "full_pipeline" else None,
                    "results": [
                        {
                            "rank": 1,
                            "publication_id": "p3",
                            "score": 1.0 / index,
                            "title": publications[2]["title"],
                            "abstract": publications[2]["abstract"],
                            "source_url": publications[2]["source_url"],
                        }
                    ],
                }
            )
    _write(frozen, {"runs": frozen_rows})
    _write(judgments, {"judgments": [{"query_id": "q1", "publication_id": "p1", "relevance": 2}]})
    return queries, metadata, snapshot, frozen, judgments


def _build(tmp_path, name):
    queries, metadata, snapshot, frozen, judgments = _inputs(tmp_path)
    output = tmp_path / name
    build_artifacts(
        queries_path=queries,
        query_metadata_path=metadata,
        corpus_snapshot_path=snapshot,
        frozen_runs_path=frozen,
        judgments_path=judgments,
        output_directory=output,
        expected_queries_sha256=sha256_file(queries),
        expected_query_metadata_sha256=sha256_file(metadata),
        expected_corpus_sha256=sha256_file(snapshot),
        expected_frozen_runs_sha256=sha256_file(frozen),
        expected_judgments_sha256=sha256_file(judgments),
        source_commit=SOURCE_COMMIT,
        starting_commit=START_COMMIT,
        limit=2,
        depth=1,
        seed=2026,
        generated_at="2026-08-15T12:00:00+00:00",
    )
    return output


def test_artifacts_generate_only_new_rankings_and_transfer_by_stable_pair(tmp_path) -> None:
    output = _build(tmp_path, "language-aware")

    with (output / "new-unjudged-candidates.csv").open(encoding="utf-8") as stream:
        new_rows = list(csv.DictReader(stream))
    assert new_rows
    assert all(row["relevance"] == "" for row in new_rows)
    assert all(row["year"] for row in new_rows)
    assert all("method" not in row and "rank" not in row and "score" not in row for row in new_rows)

    overlap = json.loads((output / "overlap-report.json").read_text(encoding="utf-8"))
    assert overlap["top_five_positions"] == 2
    assert overlap["already_judged_positions"] == 1
    assert overlap["new_unique_pairs"] == 1
    assert overlap["conflicts"] == 0
    assert {row["query_id"] for row in new_rows} == {"q2"}

    combined = json.loads((output / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert {row["method"] for row in combined} == {
        "language_independent_lexical",
        "language_aware_lexical",
        "vector_only",
        "full_pipeline",
    }
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["selected_language_aware_lexical"]["stemmer_library"] == "snowballstemmer"
    assert metadata["new_unjudged_pair_count"] == 1
    assert metadata["generated_artifact_sha256"]["new_unjudged_candidates"] == sha256_file(
        output / "new-unjudged-candidates.csv"
    )


def test_language_aware_artifact_generation_is_deterministic_except_latency(tmp_path) -> None:
    first = _build(tmp_path / "first", "out")
    second = _build(tmp_path / "second", "out")
    first_runs = json.loads((first / "language-aware-lexical-runs.json").read_text(encoding="utf-8"))
    second_runs = json.loads((second / "language-aware-lexical-runs.json").read_text(encoding="utf-8"))
    for payload in (first_runs, second_runs):
        for row in payload["runs"]:
            row.pop("latency_ms")
    assert first_runs == second_runs
