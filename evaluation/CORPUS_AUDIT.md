# Corpus Audit Foundation

The corpus-audit command creates a read-only, reproducible description and vector-free snapshot of the persisted publication corpus. It does not initialize schemas, harvest repositories, update publications, request embeddings, or call application endpoints.

## Command

Pass the database URL explicitly:

```powershell
.\.venv\Scripts\python.exe -m evaluation.corpus_audit --database-url "postgresql://USER:PASSWORD@HOST:PORT/DATABASE" --output-root path\to\audits --embedding-model intfloat/multilingual-e5-large
```

Or set `CORPUS_AUDIT_DATABASE_URL` and omit `--database-url`. The URL and its credentials are used only to establish the connection and are never written to generated files. Before the first query, the command starts one PostgreSQL `REPEATABLE READ`, `READ ONLY` transaction. Every audit query uses that connection and transaction, and the command executes only `SELECT` statements.

Each invocation creates `corpus-audit-YYYYMMDDTHHMMSS.ffffffZ` under the output root.

## Outputs

- `audit.json`: reproducibility metadata, PostgreSQL transaction snapshot, whole-corpus quality counts, exact and heuristic duplicate groups, explicitly unavailable fields, and limitations.
- `repositories.csv`: repository identity/configuration, publication count, and the latest persisted harvest job by descending start time and job ID.
- `metadata_quality.csv`: one row per repository and one `all` row with quality and embedding-state counts.
- `corpus_snapshot.json`: canonical UTF-8 snapshot used for hashing. It contains no vector values or database credentials.
- `summary.md`: separates measured values, unavailable/not-recorded values, heuristic duplicate candidates, and limitations.

## Definitions

- Missing or blank text means SQL `NULL` or a string containing only whitespace.
- A publication has no authors when no `publication_author`/`author` rows produce a name.
- Missing date means SQL `NULL`.
- Current, stale/unknown, and missing embeddings use `microservices.common.embedding_provenance.embedding_is_current`, the active model argument, and the existing expected dimension.
- Latest harvest job means latest submitted job: the first `repository_harvest` row ordered by repository ID, `created_at DESC`, then job ID descending. A newer queued job with no start timestamp therefore remains latest. `job_created_at` is exported. Duration is `finished_at - started_at`; it is unavailable if either timestamp is missing.
- `last_successful_harvest` remains the independent persisted `repository.last_harvest` value and is not replaced by latest-job state.
- Exact duplicate OAI identifiers are separate nonblank values occurring more than once. The unique database constraint should normally make this zero.
- Potential duplicate candidates are not confirmed duplicates. Their grouping rule is: Unicode NFKC normalization, case-folding, and whitespace collapse for title and source URL; require a nonblank normalized title; group by normalized title, exact ISO date or missing, and normalized source URL or missing.
- Selected OAI `metadataPrefix` and parser-skipped record counts are reported as `not recorded`, because the current schema does not persist them. This is distinct from numeric zero.

## Snapshot And Hash

Snapshot format is `repo-search-corpus-v1`. Publications are ordered by numeric publication ID; authors are sorted as strings. Each record contains publication ID, repository ID, OAI identifier, title, abstract, ISO date, source URL, and authors. Embedding vectors and provenance are excluded.

The snapshot object is serialized with UTF-8, Unicode preserved, keys sorted, and JSON separators `,` and `:` without insignificant whitespace. `corpus_snapshot.json` is exactly those canonical bytes. The SHA-256 hexadecimal digest of those bytes is stored in `audit.json`. Unchanged persisted snapshot fields therefore produce the same hash regardless of audit timestamp or input row order.

## Limitations And Real Corpus Freeze

- The audit describes persisted state at one PostgreSQL repeatable-read transaction snapshot; it does not verify remote OAI repositories or infer unrecorded harvest/parser behavior.
- `pg_current_snapshot()` is recorded as a PostgreSQL transaction snapshot for reproducibility diagnostics. It is not an external backup, restore point, or durable database snapshot identifier.
- The external database backup/snapshot identifier remains a separate manual value because the application cannot infer it. For the real evaluation freeze, stop corpus-changing jobs or run against a database snapshot/replica and record its external identifier separately.
- The tool loads publication metadata into memory to produce deterministic JSON and Python-side provenance/duplicate classifications. Large future corpora may require streaming or external sorting.
- Database server version is recorded when the server returns it; missing server metadata must remain unavailable rather than inferred.

Before the real evaluation, record the Git commit, active embedding model, database snapshot/backup identifier, audit output directory, and resulting corpus hash. Archive all five files without editing them and use the canonical snapshot as the corpus identity for retrieval runs.
