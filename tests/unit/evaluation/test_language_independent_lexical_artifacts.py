import csv
import json

import pytest

from evaluation.io import sha256_file
from evaluation.language_independent_lexical_artifacts import build_artifacts


START_COMMIT = "b" * 40
SOURCE_COMMIT = "c" * 40


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _inputs(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    queries = tmp_path / "queries.json"
    snapshot = tmp_path / "snapshot.json"
    frozen = tmp_path / "frozen-runs.json"
    publications = [
        {
            "publication_id": "p1",
            "title": "Digitalni repozitorijumi",
            "abstract": "Otvorena nauka",
            "source_url": "https://example.test/p1",
        },
        {
            "publication_id": "p2",
            "title": "Other",
            "abstract": None,
            "source_url": None,
        },
    ]
    _write(queries, {"queries": [{"query_id": "q1", "text": "digitalni repozitorijum"}]})
    _write(snapshot, {"snapshot_format": "repo-search-corpus-v1", "publications": publications})
    frozen_rows = []
    for index, method in enumerate(("keyword", "vector_only", "full_pipeline"), start=1):
        frozen_rows.append(
            {
                "query_id": "q1",
                "method": method,
                "latency_ms": float(index),
                "parser_mode": "fallback" if method == "full_pipeline" else None,
                "results": [
                    {
                        "rank": 1,
                        "publication_id": "p2",
                        "score": 1.0 / index,
                        "title": publications[1]["title"],
                        "abstract": publications[1]["abstract"],
                        "source_url": publications[1]["source_url"],
                    }
                ],
            }
        )
    _write(frozen, {"runs": frozen_rows})
    return queries, snapshot, frozen, frozen_rows


def _build(tmp_path, name):
    queries, snapshot, frozen, frozen_rows = _inputs(tmp_path)
    output = tmp_path / name
    build_artifacts(
        queries_path=queries,
        corpus_snapshot_path=snapshot,
        frozen_runs_path=frozen,
        output_directory=output,
        expected_queries_sha256=sha256_file(queries),
        expected_corpus_sha256=sha256_file(snapshot),
        expected_frozen_runs_sha256=sha256_file(frozen),
        source_commit=SOURCE_COMMIT,
        starting_commit=START_COMMIT,
        limit=2,
        depth=1,
        seed=2026,
        generated_at="2026-08-09T12:00:00+00:00",
    )
    return output, frozen_rows


def test_artifacts_use_only_selected_lexical_and_unchanged_frozen_semantic_runs(tmp_path) -> None:
    output, frozen_rows = _build(tmp_path, "candidate")

    combined = json.loads((output / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert [row["method"] for row in combined] == [
        "language_independent_lexical",
        "vector_only",
        "full_pipeline",
    ]
    assert combined[1] == frozen_rows[1]
    assert combined[2] == frozen_rows[2]
    with (output / "candidates.csv").open(encoding="utf-8") as stream:
        candidates = list(csv.DictReader(stream))
    assert candidates
    assert all("method" not in row and "rank" not in row and "score" not in row for row in candidates)

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    method = metadata["selected_language_independent_lexical"]
    assert metadata["source_commit"] == SOURCE_COMMIT
    assert metadata["starting_commit"] == START_COMMIT
    assert metadata["methods"] == [
        "language_independent_lexical",
        "vector_only",
        "full_pipeline",
    ]
    assert method["method_version"] == "1.0"
    assert method["index_statistics"]["measurement"].startswith("logical analyzer output")
    assert method["cross_lingual_retrieval"] is False
    assert metadata["pool_seed"] == 2026
    assert metadata["pool_depth"] == 1
    assert metadata["generated_artifact_sha256"]["candidate_pool"] == sha256_file(
        output / "candidates.csv"
    )


def test_selected_lexical_result_order_and_scores_are_deterministic(tmp_path) -> None:
    first, _ = _build(tmp_path / "first", "out")
    second, _ = _build(tmp_path / "second", "out")

    first_runs = json.loads(
        (first / "language-independent-lexical-runs.json").read_text(encoding="utf-8")
    )["runs"]
    second_runs = json.loads(
        (second / "language-independent-lexical-runs.json").read_text(encoding="utf-8")
    )["runs"]
    for row in first_runs + second_runs:
        row.pop("latency_ms")
    assert first_runs == second_runs


def test_artifact_generation_rejects_bad_commit_and_existing_output(tmp_path) -> None:
    queries, snapshot, frozen, _ = _inputs(tmp_path)
    arguments = dict(
        queries_path=queries,
        corpus_snapshot_path=snapshot,
        frozen_runs_path=frozen,
        expected_queries_sha256=sha256_file(queries),
        expected_corpus_sha256=sha256_file(snapshot),
        expected_frozen_runs_sha256=sha256_file(frozen),
        starting_commit=START_COMMIT,
    )
    with pytest.raises(ValueError, match="source commit"):
        build_artifacts(
            **arguments,
            output_directory=tmp_path / "bad",
            source_commit="short",
        )

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="output already exists"):
        build_artifacts(
            **arguments,
            output_directory=existing,
            source_commit=SOURCE_COMMIT,
        )
