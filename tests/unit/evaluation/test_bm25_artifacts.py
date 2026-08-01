import csv
import json

import pytest

from evaluation.bm25_artifacts import build_artifacts
from evaluation.io import sha256_file


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_bm25_artifacts_reuse_frozen_runs_and_build_blinded_final_pool(tmp_path) -> None:
    queries = tmp_path / "queries.json"
    snapshot = tmp_path / "snapshot.json"
    frozen = tmp_path / "frozen-runs.json"
    output = tmp_path / "bm25-final"
    _write(queries, {"queries": [{"query_id": "q1", "text": "retka tema"}]})
    publications = [
        {
            "publication_id": "p1",
            "title": "Retka tema",
            "abstract": "Sazetak",
            "source_url": "https://example.test/p1",
        },
        {
            "publication_id": "p2",
            "title": "Drugo",
            "abstract": "Tema",
            "source_url": None,
        },
    ]
    _write(snapshot, {"snapshot_format": "repo-search-corpus-v1", "publications": publications})
    frozen_rows = []
    for method in ("keyword", "vector_only", "full_pipeline"):
        frozen_rows.append(
            {
                "query_id": "q1",
                "method": method,
                "latency_ms": 1.0,
                "parser_mode": "fallback" if method == "full_pipeline" else None,
                "results": [
                    {
                        "rank": 1,
                        "publication_id": "p1",
                        "score": 1.0,
                        "title": publications[0]["title"],
                        "abstract": publications[0]["abstract"],
                        "source_url": publications[0]["source_url"],
                    }
                ],
            }
        )
    _write(frozen, {"runs": frozen_rows})

    build_artifacts(
        queries_path=queries,
        corpus_snapshot_path=snapshot,
        frozen_runs_path=frozen,
        output_directory=output,
        expected_queries_sha256=sha256_file(queries),
        expected_corpus_sha256=sha256_file(snapshot),
        expected_frozen_runs_sha256=sha256_file(frozen),
        limit=2,
        depth=1,
        seed=2026,
    )

    combined = json.loads((output / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert [run["method"] for run in combined] == ["bm25", "vector_only", "full_pipeline"]
    assert combined[1] == frozen_rows[1]
    assert combined[2] == frozen_rows[2]
    with (output / "candidates.csv").open(encoding="utf-8") as stream:
        candidates = list(csv.DictReader(stream))
    assert candidates and "method" not in candidates[0]
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["bm25"]["implementation_version"] == "0.3.10"
    assert metadata["bm25"]["k1"] == 1.2
    assert metadata["pool_seed"] == 2026


def test_bm25_artifact_generation_rejects_unverified_inputs(tmp_path) -> None:
    source = tmp_path / "source.json"
    _write(source, {})
    with pytest.raises(ValueError, match="query set hash mismatch"):
        build_artifacts(
            queries_path=source,
            corpus_snapshot_path=source,
            frozen_runs_path=source,
            output_directory=tmp_path / "out",
            expected_queries_sha256="0" * 64,
            expected_corpus_sha256="0" * 64,
            expected_frozen_runs_sha256="0" * 64,
        )
