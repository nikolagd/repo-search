from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from microservices.common.embedding_provenance import (
    DEFAULT_EMBEDDING_MODEL_REVISION,
    DOCUMENT_TEMPLATE_VERSION,
    EXPECTED_EMBEDDING_DIMENSION,
    embedding_is_current,
    embedding_model_name,
    embedding_model_revision,
)


SNAPSHOT_FORMAT = "repo-search-corpus-v1"
NOT_RECORDED = "not recorded"


def _serialize(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _normalize_duplicate_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def build_snapshot(publications: list[dict[str, Any]]) -> tuple[dict[str, Any], str, bytes]:
    records = []
    for publication in sorted(publications, key=lambda item: int(item["id"])):
        records.append(
            {
                "publication_id": int(publication["id"]),
                "repository_id": int(publication["repository_id"]),
                "oai_identifier": publication.get("oai_identifier"),
                "title": publication.get("title"),
                "abstract": publication.get("abstract"),
                "date": _serialize(publication.get("date")),
                "source_url": publication.get("source_url"),
                "authors": sorted(str(author) for author in publication.get("authors", [])),
            }
        )
    snapshot = {"snapshot_format": SNAPSHOT_FORMAT, "publications": records}
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return snapshot, hashlib.sha256(canonical).hexdigest(), canonical


def classify_metadata_quality(
    publications: list[dict[str, Any]],
    *,
    model_name: str,
    model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    template_version: str = DOCUMENT_TEMPLATE_VERSION,
    dimension: int = EXPECTED_EMBEDDING_DIMENSION,
) -> dict[str, int]:
    counts = Counter(
        {
            "publication_count": len(publications),
            "missing_or_blank_titles": 0,
            "missing_or_blank_abstracts": 0,
            "missing_dates": 0,
            "publications_without_authors": 0,
            "missing_or_blank_source_links": 0,
            "missing_or_blank_oai_identifiers": 0,
            "current_embeddings": 0,
            "stale_or_unknown_embeddings": 0,
            "missing_embeddings": 0,
        }
    )
    for publication in publications:
        counts["missing_or_blank_titles"] += _blank(publication.get("title"))
        counts["missing_or_blank_abstracts"] += _blank(publication.get("abstract"))
        counts["missing_dates"] += publication.get("date") is None
        counts["publications_without_authors"] += not publication.get("authors")
        counts["missing_or_blank_source_links"] += _blank(publication.get("source_url"))
        counts["missing_or_blank_oai_identifiers"] += _blank(publication.get("oai_identifier"))
        if not publication.get("has_embedding"):
            counts["missing_embeddings"] += 1
        elif embedding_is_current(
            publication,
            model_name=model_name,
            model_revision=model_revision,
            template_version=template_version,
            dimension=dimension,
        ):
            counts["current_embeddings"] += 1
        else:
            counts["stale_or_unknown_embeddings"] += 1
    return dict(counts)


def duplicate_statistics(publications: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = Counter(
        publication["oai_identifier"]
        for publication in publications
        if not _blank(publication.get("oai_identifier"))
    )
    exact_groups = [
        {"oai_identifier": identifier, "publication_count": count}
        for identifier, count in sorted(identifiers.items())
        if count > 1
    ]

    candidates: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for publication in publications:
        normalized_title = _normalize_duplicate_text(publication.get("title"))
        if not normalized_title:
            continue
        key = (
            normalized_title,
            _serialize(publication.get("date")) or "",
            _normalize_duplicate_text(publication.get("source_url")),
        )
        candidates[key].append(int(publication["id"]))
    potential_groups = [
        {
            "normalized_title": key[0],
            "date": key[1] or None,
            "normalized_source_url": key[2] or None,
            "publication_ids": sorted(ids),
            "publication_count": len(ids),
        }
        for key, ids in sorted(candidates.items())
        if len(ids) > 1
    ]
    return {
        "exact_duplicate_oai_identifiers": exact_groups,
        "exact_duplicate_oai_identifier_groups": len(exact_groups),
        "potential_duplicate_candidates": potential_groups,
        "potential_duplicate_candidate_groups": len(potential_groups),
        "potential_duplicate_rule": (
            "Unicode NFKC, case-fold and whitespace-collapse title and source URL; "
            "group nonblank titles by normalized title, exact ISO date (or missing), and normalized source URL (or missing)."
        ),
    }


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


def load_corpus(
    connection: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, str, str, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_current_snapshot()::text,
                   current_setting('transaction_read_only'),
                   current_setting('transaction_isolation')
            """
        )
        transaction_snapshot, transaction_read_only, transaction_isolation = cursor.fetchone()
        cursor.execute(
            "SELECT id, name, oai_endpoint, last_harvest, refresh_interval FROM repository ORDER BY id"
        )
        repositories = [
            {
                "repository_id": row[0],
                "name": row[1],
                "oai_endpoint": row[2],
                "last_successful_harvest": row[3],
                "refresh_interval": row[4],
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT p.id, p.repository_id, p.oai_identifier, p.title, p.abstract, p.date, p.source_url,
                   p.embedding IS NOT NULL, p.embedding_model, p.embedding_model_revision,
                   p.embedding_template_version, p.embedding_dimension,
                   p.embedding_generated_at, p.embedding_source_hash,
                   COALESCE(ARRAY_AGG(a.full_name ORDER BY a.full_name)
                       FILTER (WHERE a.full_name IS NOT NULL), '{}')
            FROM publication p
            LEFT JOIN publication_author pa ON pa.publication_id = p.id
            LEFT JOIN author a ON a.id = pa.author_id
            GROUP BY p.id
            ORDER BY p.id
            """
        )
        publications = [
            {
                "id": row[0],
                "repository_id": row[1],
                "oai_identifier": row[2],
                "title": row[3],
                "abstract": row[4],
                "date": row[5],
                "source_url": row[6],
                "has_embedding": row[7],
                "embedding_model": row[8],
                "embedding_model_revision": row[9],
                "embedding_template_version": row[10],
                "embedding_dimension": row[11],
                "embedding_generated_at": row[12],
                "embedding_source_hash": row[13],
                "authors": list(row[14] or []),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT id, repository_id, status, processed_records, created_at, started_at, finished_at
            FROM admin_job
            WHERE job_type = 'repository_harvest'
            ORDER BY repository_id, created_at DESC, id DESC
            """
        )
        latest_jobs: dict[int, dict[str, Any]] = {}
        for row in cursor.fetchall():
            if row[1] is not None and row[1] not in latest_jobs:
                latest_jobs[row[1]] = {
                    "latest_harvest_job_status": row[2],
                    "processed_records": row[3],
                    "job_created_at": row[4],
                    "job_started_at": row[5],
                    "job_finished_at": row[6],
                    "harvest_duration_seconds": _duration_seconds(row[5], row[6]),
                }
        cursor.execute("SELECT version()")
        database_version = cursor.fetchone()[0]

    publication_counts = Counter(publication["repository_id"] for publication in publications)
    for repository in repositories:
        repository["publication_count"] = publication_counts[repository["repository_id"]]
        repository.update(
            latest_jobs.get(
                repository["repository_id"],
                {
                    "latest_harvest_job_status": None,
                    "processed_records": None,
                    "job_created_at": None,
                    "job_started_at": None,
                    "job_finished_at": None,
                    "harvest_duration_seconds": None,
                },
            )
        )
        repository["metadata_prefix"] = NOT_RECORDED
        repository["parser_skipped_records"] = NOT_RECORDED
    return (
        repositories,
        publications,
        database_version,
        transaction_snapshot,
        transaction_read_only,
        transaction_isolation,
    )


def _csv_value(value: Any) -> Any:
    return _serialize(value) if isinstance(value, (datetime, date)) else value


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: _csv_value(row.get(key)) for key in fieldnames} for row in rows)


def build_audit(
    repositories: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    *,
    git_commit: str,
    audit_timestamp: str,
    model_name: str,
    model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    template_version: str = DOCUMENT_TEMPLATE_VERSION,
    database_version: str | None,
    database_transaction_snapshot: str | None = None,
    transaction_read_only: str | None = None,
    transaction_isolation: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    snapshot, snapshot_hash, canonical_snapshot = build_snapshot(publications)
    quality_rows = []
    for repository in repositories:
        repository_publications = [
            publication for publication in publications if publication["repository_id"] == repository["repository_id"]
        ]
        quality_rows.append(
            {
                "repository_id": repository["repository_id"],
                "repository_name": repository["name"],
                **classify_metadata_quality(
                    repository_publications,
                    model_name=model_name,
                    model_revision=model_revision,
                    template_version=template_version,
                ),
            }
        )
    quality_rows.append(
        {
            "repository_id": "all",
            "repository_name": "All repositories",
            **classify_metadata_quality(
                publications,
                model_name=model_name,
                model_revision=model_revision,
                template_version=template_version,
            ),
        }
    )
    audit = {
        "metadata": {
            "git_commit": git_commit,
            "audit_timestamp": audit_timestamp,
            "corpus_size": len(publications),
            "repository_count": len(repositories),
            "active_embedding_model": model_name,
            "active_embedding_model_revision": model_revision,
            "active_embedding_template_version": template_version,
            "corpus_snapshot_hash_sha256": snapshot_hash,
            "snapshot_format": SNAPSHOT_FORMAT,
            "database_server_version": database_version,
            "postgresql_transaction_snapshot": database_transaction_snapshot,
            "transaction_read_only": transaction_read_only,
            "transaction_isolation": transaction_isolation,
            "external_database_backup_snapshot_identifier": NOT_RECORDED,
        },
        "unavailable_or_not_recorded": {
            "selected_metadata_prefix": NOT_RECORDED,
            "parser_skipped_record_count": NOT_RECORDED,
        },
        "metadata_quality": quality_rows[-1],
        "duplicates": duplicate_statistics(publications),
        "limitations": [
            "This audit measures persisted database state only.",
            "Potential duplicate candidates are heuristic groups, not confirmed duplicates.",
            "Selected OAI metadataPrefix and parser-skipped record counts are not persisted.",
            "The PostgreSQL transaction snapshot is not an external backup or database snapshot identifier.",
            "Embedding currentness uses the configured active model and existing provenance rules.",
        ],
    }
    return audit, quality_rows, canonical_snapshot


def write_audit_outputs(
    output_directory: Path,
    audit: dict[str, Any],
    repositories: list[dict[str, Any]],
    quality_rows: list[dict[str, Any]],
    canonical_snapshot: bytes,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=False)
    (output_directory / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=_serialize) + "\n",
        encoding="utf-8",
    )
    (output_directory / "corpus_snapshot.json").write_bytes(canonical_snapshot)
    _write_csv(
        output_directory / "repositories.csv",
        repositories,
        [
            "repository_id",
            "name",
            "oai_endpoint",
            "last_successful_harvest",
            "refresh_interval",
            "publication_count",
            "latest_harvest_job_status",
            "processed_records",
            "job_created_at",
            "job_started_at",
            "job_finished_at",
            "harvest_duration_seconds",
            "metadata_prefix",
            "parser_skipped_records",
        ],
    )
    quality_fields = [
        "repository_id",
        "repository_name",
        "publication_count",
        "missing_or_blank_titles",
        "missing_or_blank_abstracts",
        "missing_dates",
        "publications_without_authors",
        "missing_or_blank_source_links",
        "missing_or_blank_oai_identifiers",
        "current_embeddings",
        "stale_or_unknown_embeddings",
        "missing_embeddings",
    ]
    _write_csv(output_directory / "metadata_quality.csv", quality_rows, quality_fields)

    metadata = audit["metadata"]
    quality = audit["metadata_quality"]
    duplicates = audit["duplicates"]
    summary = [
        "# Corpus Audit Summary",
        "",
        "## Measured Values",
        "",
        f"- Corpus size: {metadata['corpus_size']}",
        f"- Repository count: {metadata['repository_count']}",
        f"- Current embeddings: {quality['current_embeddings']}",
        f"- Stale or unknown-provenance embeddings: {quality['stale_or_unknown_embeddings']}",
        f"- Missing embeddings: {quality['missing_embeddings']}",
        f"- Exact duplicate OAI identifier groups: {duplicates['exact_duplicate_oai_identifier_groups']}",
        f"- Snapshot SHA-256: `{metadata['corpus_snapshot_hash_sha256']}`",
        "",
        "## Unavailable Or Not Recorded",
        "",
        f"- Selected metadataPrefix: {NOT_RECORDED}",
        f"- Parser-skipped record count: {NOT_RECORDED}",
        "",
        "## Heuristic Duplicate Candidates",
        "",
        f"- Potential duplicate candidate groups: {duplicates['potential_duplicate_candidate_groups']}",
        f"- Rule: {duplicates['potential_duplicate_rule']}",
        "",
        "## Limitations",
        "",
        *[f"- {limitation}" for limitation in audit["limitations"]],
    ]
    (output_directory / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


def run_audit(
    connection_factory: Callable[[], Any],
    output_root: str | Path,
    *,
    git_commit: str,
    audit_time: datetime,
    model_name: str,
    model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    template_version: str = DOCUMENT_TEMPLATE_VERSION,
) -> Path:
    connection = connection_factory()
    try:
        connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
        (
            repositories,
            publications,
            database_version,
            transaction_snapshot,
            transaction_read_only,
            transaction_isolation,
        ) = load_corpus(connection)
        connection.rollback()
    finally:
        connection.close()

    timestamp = audit_time.astimezone(timezone.utc)
    timestamp_text = timestamp.isoformat()
    directory_name = timestamp.strftime("corpus-audit-%Y%m%dT%H%M%S.%fZ")
    output_directory = Path(output_root) / directory_name
    audit, quality_rows, canonical_snapshot = build_audit(
        repositories,
        publications,
        git_commit=git_commit,
        audit_timestamp=timestamp_text,
        model_name=model_name,
        model_revision=model_revision,
        template_version=template_version,
        database_version=database_version,
        database_transaction_snapshot=transaction_snapshot,
        transaction_read_only=transaction_read_only,
        transaction_isolation=transaction_isolation,
    )
    write_audit_outputs(output_directory, audit, repositories, quality_rows, canonical_snapshot)
    return output_directory


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a read-only repo-search corpus audit")
    parser.add_argument(
        "--database-url",
        default=os.getenv("CORPUS_AUDIT_DATABASE_URL"),
        help="PostgreSQL URL; alternatively set CORPUS_AUDIT_DATABASE_URL",
    )
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--embedding-model", default=embedding_model_name())
    parser.add_argument("--embedding-model-revision", default=embedding_model_revision())
    parser.add_argument("--embedding-template-version", default=DOCUMENT_TEMPLATE_VERSION)
    parser.add_argument("--git-commit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit("--database-url or CORPUS_AUDIT_DATABASE_URL is required")
    import psycopg2

    output = run_audit(
        lambda: psycopg2.connect(args.database_url),
        args.output_root,
        git_commit=args.git_commit or _git_commit(),
        audit_time=datetime.now(timezone.utc),
        model_name=args.embedding_model,
        model_revision=args.embedding_model_revision,
        template_version=args.embedding_template_version,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
