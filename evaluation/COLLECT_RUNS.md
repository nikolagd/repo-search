# Real Search-Evaluation Run Collector

`collect-runs` executes a pre-authored UTF-8 query set against the frozen corpus and writes schema-compatible `runs.json`. It does not create queries, judgments, metrics, candidate pools, or thesis conclusions.

## Method Boundaries

- `keyword` uses `KeywordBaselineAdapter` over the publication metadata loaded from the frozen database. Its existing NFKC/case-fold/token-frequency scoring and score-then-string-ID ordering are unchanged. It is an internal baseline, not a reproduction of DSpace, Google Scholar, PostgreSQL full-text ranking, or another search engine.
- `bm25` is the final lexical comparator. It uses pinned `bm25s==0.3.10`, its `lucene` scoring variant, `k1=1.2`, `b=0.75`, separate title and abstract indexes, and the documented field combination `2.0 * title BM25 + abstract BM25`. Tokenization is Unicode NFKC plus case-folding and Unicode word tokens, without stemming or stop-word removal. Equal scores end with ascending string `publication_id`.
- `vector_only` sends the original query directly to Embedding Service `/embed/query`, never calls Query Service, and executes the shared production pgvector retrieval helper without years, phrases, boosts, candidate merging, or coverage logic. Evaluation adds `publication.id ASC` only as a deterministic equal-distance tie-breaker; production search keeps its existing tie behavior.
- `full_pipeline` sends the exact query to Gateway `/api/search`. Gateway/Search/Query/Embedding services own parsing and ranking. The collector preserves returned order, scores, and `plan.parser_mode` and does not reimplement ranking.

Every method result must reference a publication loaded from the verified frozen transaction. For `full_pipeline`, the returned title and source URL must also equal the frozen values, including valid `null` values. This prevents a Gateway connected to another database from silently contributing results.

Keyword, BM25, and vector-only share one PostgreSQL `REPEATABLE READ`, `READ ONLY` transaction. Full pipeline uses service-owned transactions; therefore stopped corpus writers plus pre/post corpus verification are the cross-boundary consistency safeguard.

BM25 is a reproducible Solr/Lucene-style lexical baseline over the frozen local corpus. It is not claimed to reproduce Google Scholar, and it is not claimed to be byte-for-byte identical to either source repository's DSpace/Solr configuration. Live RFOS/REPFF search is not used as the primary comparator because its indexes and configuration can change and raw scores cannot be merged across repositories.

## Required Runtime Configuration

- `EVALUATION_DATABASE_URL` by default, or the environment variable named by `--database-url-env`. It must point to the same frozen primary database used by Search Service.
- `EVALUATION_API_TOKEN` by default, or the environment variable named by `--api-token-env`. It is sent only in `X-API-Key`.
- Embedding Service base URL and Gateway `/api/search` URL as CLI arguments.
- Expected corpus size, canonical corpus snapshot SHA-256, and active embedding model.

Database URLs, passwords, API tokens, JWTs, and administrator credentials are never written to runs, printed by the collector, or included in sanitized collector errors. JWT/admin credentials are not needed. Do not use `.codex-tmp/evaluation/credentials.local.txt`.

## Frozen `repo-search-eval` Procedure

The tracked `evaluation/docker-compose.collect-runs.yml` adds only an ephemeral runner to the existing `repo-search-eval` project. It reuses the already-built Search Service image for dependencies, mounts the current checkout read-only at `/workspace`, and therefore imports both `evaluation` and `microservices` from the current feature branch even though `Dockerfile.microservice` does not copy `evaluation`. It publishes no ports. PostgreSQL, Embedding Service, and Gateway are reached on the existing Compose network.

Use the existing ignored `.codex-tmp/evaluation/repo-search-eval.env`; do not copy credentials into the shell command. Prepare controlled ignored input/output directories, then place the independently authored UTF-8 query file at `.codex-tmp/evaluation/queries/queries.json`:

```powershell
New-Item -ItemType Directory -Force .codex-tmp\evaluation\queries, .codex-tmp\evaluation\runs
```

Keep both write-capable job components stopped. If Docker was restarted, start only the already-created read path; `start` does not recreate the frozen containers or volumes. Wait until PostgreSQL, Ollama, Embedding Service, Query Service, Search Service, and Catalog Service report ready. Gateway aggregate health may remain unhealthy solely because Job Service is intentionally stopped.

```powershell
docker compose --env-file .codex-tmp/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .codex-tmp/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml stop job-worker job-service

docker compose --env-file .codex-tmp/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .codex-tmp/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml start db-primary ollama embedding-service query-service catalog-service search-service gateway

docker compose --env-file .codex-tmp/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .codex-tmp/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml ps
```

Run the collector only after those read-path services are ready:

```powershell
docker compose --env-file .codex-tmp/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .codex-tmp/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml run --rm --no-deps evaluation-runner collect-runs `
  --queries /evaluation-input/queries.json `
  --output /evaluation-output/runs.json `
  --methods bm25 vector_only full_pipeline `
  --limit 20 `
  --database-url-env EVALUATION_DATABASE_URL `
  --api-token-env EVALUATION_API_TOKEN `
  --embedding-service-url http://embedding-service:8000 `
  --full-pipeline-url http://gateway:8000/api/search `
  --request-timeout 180 `
  --embedding-model intfloat/multilingual-e5-large `
  --expected-corpus-size 5646 `
  --expected-snapshot-hash b366854b50c7abb40b51c29a943f89fdd22b0af33cac6b6cd3371ff2404eebce
```

`evaluation/docker-compose.collect-runs.yml` constructs the private-network database URL and maps the API token only from variables in the ignored environment file. The input bind mount is read-only; only `.codex-tmp/evaluation/runs` is writable. The runner container itself has a read-only root filesystem and is removed after completion. The collector opens PostgreSQL as `REPEATABLE READ, READ ONLY`. `--overwrite` is required to replace an existing output.

Resolve the complete configuration and prove that the current checkout starts inside the runner without executing a query or inspecting rankings:

```powershell
docker compose --env-file .codex-tmp/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .codex-tmp/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml config --quiet

docker compose --env-file .codex-tmp/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .codex-tmp/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml run --rm --no-deps evaluation-runner --help
```

## Validation And Failure Behavior

- Queries must have unique, nonblank string IDs and nonblank string text. Text is read/written as UTF-8 without transliteration or ASCII conversion.
- Methods must be unique members of `keyword`, `bm25`, `vector_only`, and `full_pipeline`. The default final set is `bm25`, `vector_only`, and `full_pipeline`; `keyword` must be requested explicitly for historical work.
- Limit is 1–50, matching the application Search API.
- Before retrieval, the collector verifies corpus size, canonical snapshot hash, current provenance for every embedding, Embedding Service model, and vector dimension.
- Every selected query/method pair produces exactly one run, including explicit empty results.
- Per-run IDs, display fields, scores, latency, parser mode, duplicates, and method/query identity are checked in context. Every result ID must be in the frozen corpus; full-pipeline title and source URL must match its frozen publication, including nullable values. Final schema and comparison-matrix validation reuse `evaluation.io`.
- A fresh read-only database connection repeats corpus verification after service calls. Output is created only afterward.
- Output uses a same-directory temporary file and atomic `os.replace`. A failed query identifies its query ID and method, leaves no partial new file, and never silently omits a run.

The collector does not write model/ranking metadata into `runs.json` because the existing schema contains runs only. Search Service owns ranking configuration; record its configuration separately when invoking the existing report command.

## Frozen-Corpus Safeguards And Limits

- Keep Job Worker and Job Service stopped. Do not start harvesting or backfill processes for collection.
- The aggregate Gateway health endpoint is expected to be unhealthy while Job Service is stopped; `/api/search` remains the relevant endpoint.
- Collector timeout cannot extend Search-to-Query (90 seconds) or Gateway proxy (120 seconds) upstream timeouts.
- Pre/post canonical metadata/provenance checks detect corpus metadata or provenance changes, but cannot detect a vector value changed without its provenance changing.
- Do not inspect ranked output to devise queries. Queries must be finalized independently before collection, and judgments must later come from blinded pools.
