from __future__ import annotations

import json
from datetime import datetime, timezone

from evaluation.corpus_audit import (
    NOT_RECORDED,
    build_audit,
    build_snapshot,
    classify_metadata_quality,
    duplicate_statistics,
    write_audit_outputs,
)
from microservices.common.embedding_provenance import (
    DEFAULT_EMBEDDING_MODEL_REVISION,
    DOCUMENT_TEMPLATE_VERSION,
    document_source_hash,
)


MODEL = "synthetic-model"


def _publication(publication_id: int, **overrides):
    value = {
        "id": publication_id,
        "repository_id": 1,
        "oai_identifier": f"oai:synthetic:{publication_id}",
        "title": f"Title {publication_id}",
        "abstract": f"Abstract {publication_id}",
        "date": datetime(2024, 1, publication_id, tzinfo=timezone.utc),
        "source_url": f"https://example.test/{publication_id}",
        "authors": ["Synthetic Author"],
        "has_embedding": False,
        "embedding_model": None,
        "embedding_model_revision": None,
        "embedding_template_version": None,
        "embedding_dimension": None,
        "embedding_generated_at": None,
        "embedding_source_hash": None,
    }
    value.update(overrides)
    return value


def test_metadata_quality_counts_missing_blank_authors_and_embedding_states() -> None:
    current = _publication(
        1,
        has_embedding=True,
        embedding_model=MODEL,
        embedding_model_revision=DEFAULT_EMBEDDING_MODEL_REVISION,
        embedding_template_version=DOCUMENT_TEMPLATE_VERSION,
        embedding_dimension=1024,
        embedding_generated_at="2026-07-11T00:00:00+00:00",
        embedding_source_hash=document_source_hash("Title 1", "Abstract 1"),
    )
    stale = _publication(2, has_embedding=True, embedding_model="old-model")
    missing = _publication(
        3,
        title="  ",
        abstract=None,
        date=None,
        source_url="",
        oai_identifier=" ",
        authors=[],
    )

    counts = classify_metadata_quality([current, stale, missing], model_name=MODEL)

    assert counts["publication_count"] == 3
    assert counts["missing_or_blank_titles"] == 1
    assert counts["missing_or_blank_abstracts"] == 1
    assert counts["missing_dates"] == 1
    assert counts["publications_without_authors"] == 1
    assert counts["missing_or_blank_source_links"] == 1
    assert counts["missing_or_blank_oai_identifiers"] == 1
    assert counts["current_embeddings"] == 1
    assert counts["stale_or_unknown_embeddings"] == 1
    assert counts["missing_embeddings"] == 1


def test_duplicate_reporting_separates_exact_and_potential_candidates() -> None:
    publications = [
        _publication(1, oai_identifier="same", title=" Same   Title ", source_url="HTTPS://EXAMPLE.TEST/X"),
        _publication(2, oai_identifier="same", title="same title", source_url="https://example.test/x", date=datetime(2024, 1, 1, tzinfo=timezone.utc)),
    ]
    publications[0]["date"] = publications[1]["date"]

    duplicates = duplicate_statistics(publications)

    assert duplicates["exact_duplicate_oai_identifier_groups"] == 1
    assert duplicates["exact_duplicate_oai_identifiers"][0]["publication_count"] == 2
    assert duplicates["potential_duplicate_candidate_groups"] == 1
    assert duplicates["potential_duplicate_candidates"][0]["publication_ids"] == [1, 2]
    assert "NFKC" in duplicates["potential_duplicate_rule"]

    not_exact = duplicate_statistics([_publication(3, oai_identifier="same "), _publication(4, oai_identifier="same")])
    assert not_exact["exact_duplicate_oai_identifier_groups"] == 0


def test_snapshot_order_and_hash_are_deterministic_and_metadata_sensitive() -> None:
    first = _publication(1, authors=["B", "A"])
    second = _publication(2)
    snapshot_a, hash_a, canonical_a = build_snapshot([second, first])
    snapshot_b, hash_b, canonical_b = build_snapshot([first, second])

    assert [row["publication_id"] for row in snapshot_a["publications"]] == [1, 2]
    assert snapshot_a["publications"][0]["authors"] == ["A", "B"]
    assert (hash_a, canonical_a) == (hash_b, canonical_b)

    changed = _publication(1, title="Changed", authors=["B", "A"])
    assert build_snapshot([changed, second])[1] != hash_a


def test_repository_quality_aggregation_and_unavailable_values() -> None:
    repositories = [
        {"repository_id": 1, "name": "One"},
        {"repository_id": 2, "name": "Two"},
    ]
    audit, quality_rows, _ = build_audit(
        repositories,
        [_publication(1), _publication(2, repository_id=2, title="")],
        git_commit="test",
        audit_timestamp="2026-07-11T00:00:00+00:00",
        model_name=MODEL,
        database_version="Synthetic PostgreSQL",
    )

    assert [row["publication_count"] for row in quality_rows[:-1]] == [1, 1]
    assert quality_rows[1]["missing_or_blank_titles"] == 1
    assert audit["unavailable_or_not_recorded"] == {
        "selected_metadata_prefix": NOT_RECORDED,
        "parser_skipped_record_count": NOT_RECORDED,
    }


def test_generated_reports_do_not_contain_database_credentials(tmp_path) -> None:
    repositories = [
        {
            "repository_id": 1,
            "name": "Synthetic",
            "oai_endpoint": "https://example.test/oai",
            "metadata_prefix": NOT_RECORDED,
            "parser_skipped_records": NOT_RECORDED,
        }
    ]
    audit, quality_rows, snapshot = build_audit(
        repositories,
        [_publication(1)],
        git_commit="test",
        audit_timestamp="2026-07-11T00:00:00+00:00",
        model_name=MODEL,
        database_version="PostgreSQL synthetic",
    )
    output = tmp_path / "audit"
    write_audit_outputs(output, audit, repositories, quality_rows, snapshot)

    assert {path.name for path in output.iterdir()} == {
        "audit.json",
        "repositories.csv",
        "metadata_quality.csv",
        "corpus_snapshot.json",
        "summary.md",
    }
    combined = b"\n".join(path.read_bytes() for path in output.iterdir())
    assert b"postgresql://" not in combined
    assert b"secret-password" not in combined
    assert json.loads((output / "audit.json").read_text(encoding="utf-8"))["metadata"]["corpus_size"] == 1
