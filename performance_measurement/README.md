# Runtime performance measurement

This package provides reproducible tooling for later search-latency, Prometheus
resource, and embedding-backfill measurements. It is separate from relevance
evaluation and does not change search, ranking, embedding, or job behavior. Its
synthetic tests and implementation are not thesis performance results.

## Identity model and mandatory preflight

Every command performs runtime identity verification before a measurement timer,
Prometheus query, or backfill creation. Reports keep four concepts separate:

- `runner_git_commit` is the commit of the checkout executing this CLI. It is not
  evidence of what is deployed.
- `verified_deployment_identity` comes from a required, separately captured and
  SHA-256-hashed deployment-evidence file. It records the deployed Git revision,
  immutable image digests, runtime kind, evidence timestamp, and whether the
  deployed revision matches the runner.
- `configured_expectations` are the model name, embedding revision/template, and
  LLM model requested by the measurement config.
- `observed_runtime_model_identity` contains only those four observed runtime
  fields plus the UTC verification timestamp.

For microservices, the preflight authenticates with `X-API-Key` to configurable
Query Service and Embedding Service `/model/status` URLs. It compares Query
Service `llm_model` and Embedding Service `embedding_model`,
`embedding_model_revision`, and `embedding_template_version` with the configured
expectations. Missing, malformed, or mismatched fields fail before measurement.
Other status fields such as service URLs, devices, or initialization messages are
not copied into the report.

The current services do not expose their deployed Git or image identity, so an
external deployment-evidence file is required for every command. A thesis-ready
run additionally requires its full deployed Git revision to equal
`runner_git_commit`. Set `thesis_ready` to `false` only for diagnostic runs; a
revision mismatch is then recorded and cannot be presented as thesis-ready.

The legacy monolith has no equivalent runtime model-status contract. It therefore
fails closed unless its external evidence also contains all four observed model
fields. Missing embedding revision or template data is never filled from the
runner checkout or current defaults.

Example microservices configuration:

```json
{
  "deployment_label": "compose-gpu",
  "expected_models": {
    "embedding_model": "intfloat/multilingual-e5-large",
    "embedding_model_revision": "verified-revision",
    "embedding_template_version": "verified-template",
    "llm_model": "verified-llm-model"
  },
  "runtime_identity": {
    "runtime_kind": "microservices",
    "thesis_ready": true,
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "query_model_status_url": "http://localhost:8004/model/status",
    "embedding_model_status_url": "http://localhost:8003/model/status",
    "request_timeout_seconds": 30
  },
  "corpus": {
    "size": 0,
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "search": {
    "endpoint": "http://localhost:8000/api/search",
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "warmup_repetitions": 1,
    "measured_repetitions": 10,
    "timeout_seconds": 180,
    "run_classification": "warm",
    "cold_evidence_max_age_seconds": 120
  }
}
```

Required deployment evidence for microservices:

```json
{
  "deployment_label": "compose-gpu",
  "runtime_kind": "microservices",
  "deployment_git_revision": "<full deployed Git revision>",
  "image_identities": {
    "query-service": "registry.example/query@sha256:<64 hexadecimal characters>",
    "embedding-service": "registry.example/embedding@sha256:<64 hexadecimal characters>"
  },
  "captured_at_utc": "2026-01-01T09:59:00Z",
  "source": "external deployment inspection record"
}
```

For `runtime_kind: legacy_monolith`, `runtime_identity` contains only
`runtime_kind` and `thesis_ready`. Its evidence has the same base fields plus:

```json
{
  "observed_runtime_models": {
    "embedding_model": "externally verified model",
    "embedding_model_revision": "externally verified revision",
    "embedding_template_version": "externally verified template",
    "llm_model": "externally verified LLM"
  }
}
```

## CLI and outputs

API tokens are accepted only through configured environment-variable names. No
token CLI argument exists, credential fields and credential-bearing URLs are
rejected, and exact token values are scanned before publication.

```powershell
$env:PERFORMANCE_API_TOKEN = "<runtime token>"
.\.venv\Scripts\python.exe -m performance_measurement search --config .\config.json --deployment-evidence .\deployment-evidence.json --queries .\queries.json --output-dir .\.codex-tmp\performance\compose-search
.\.venv\Scripts\python.exe -m performance_measurement resources --config .\config.json --deployment-evidence .\deployment-evidence.json --output-dir .\.codex-tmp\performance\compose-resources
.\.venv\Scripts\python.exe -m performance_measurement backfill --config .\config.json --deployment-evidence .\deployment-evidence.json --output-dir .\.codex-tmp\performance\compose-backfill
Remove-Item Env:PERFORMANCE_API_TOKEN -ErrorAction SilentlyContinue
```

Each output directory contains `measurement.json`, `samples.csv`, `summary.md`,
and `SHA256SUMS`. It is built in a temporary sibling directory and atomically
published. Existing output is protected unless `--overwrite` is explicit.
`.codex-tmp/performance` is reserved for later approved real runs and remains
untracked.

Search query input is strict and query text is not copied into outputs:

```json
{"queries": [{"id": "q01", "query": "example search", "limit": 10}]}
```

## Search latency and cold evidence

Search requests are sequential and timed with `time.perf_counter_ns`. Raw rows
retain phase, classification, query ID, repetition, outcome/status, HTTP status,
nanosecond/millisecond latency, result count, parser mode, and a generic failure
category. Warm-up rows never enter measured statistics. Failed requests remain
explicit raw rows but are excluded from latency summaries.

`warm` is the normal post-warm-up classification. `first_request` requires zero
warm-ups and does not claim a cold system. `cold` requires zero warm-ups and a
separate restart/readiness evidence file:

```json
{
  "deployment_label": "compose-gpu",
  "source": "external restart and readiness log",
  "restart_completed_at_utc": "2026-01-01T10:00:00Z",
  "readiness_confirmed_at_utc": "2026-01-01T10:01:00Z"
}
```

Readiness must follow restart, must not be in the future, and must be no older at
measurement start than configured `cold_evidence_max_age_seconds`. The bound and
observed readiness age are retained in the report. The first request after valid
evidence is `cold`; later requests are `warm`.

Summaries contain attempted/successful/failed counts, mean, median, min, max, p50,
and p95. Percentiles use deterministic nearest rank:
`sorted_values[ceil(p*n)-1]`.

## Prometheus resources

Prometheus instant/range definitions are named and fully configurable; the tool
does not hard-code deployment labels. Each definition must return at most one
series. Multiple series fail with an instruction to aggregate or narrow the
PromQL expression, so values from different label sets are never pooled.

Example configuration section:

```json
{
  "prometheus": {
    "base_url": "http://localhost:9090",
    "timeout_seconds": 30,
    "metrics": [
      {
        "name": "query_cpu_rate",
        "metric_type": "cpu",
        "unit": "cores",
        "query_kind": "query",
        "query": "sum(rate(container_cpu_usage_seconds_total{container=\"query-service\"}[1m]))"
      }
    ]
  }
}
```

If Prometheus is authenticated, set `api_token_env` in this section. The token
is sent as a bearer token and is never written to the report.

The accepted label set and series count are recorded with each metric summary.
Raw timestamp/value/labels are retained. Empty or failed CPU, RAM, GPU utilization,
or GPU framebuffer queries are `unavailable` with null summaries, never zero.
Non-finite samples invalidate the run.

## Embedding backfill and comparability

Backfill preflight finishes before the command creates one existing Job Service
`embedding_backfill` job and polls its ID to a terminal state. It records observed
queue time, service start/finish, attempts, processed records, service/observed
duration, and records per second. The tool never harvests data or manufactures
stale embeddings. Because Job Service timestamps are timezone-naive PostgreSQL
values, config must explicitly set `job_timestamp_timezone: UTC`.

Example configuration section:

```json
{
  "backfill": {
    "job_service_url": "http://localhost:8002",
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "poll_interval_seconds": 1,
    "timeout_seconds": 3600,
    "request_timeout_seconds": 30,
    "job_timestamp_timezone": "UTC"
  }
}
```

Compose, Kubernetes, and monolith runs are comparable only when deployment
evidence, query/config hashes, runner and deployed revisions, corpus identity,
configured and observed models, repetitions, endpoint semantics, PromQL scope,
and measurement windows match. Scrape intervals, readiness criteria, machine
contention, cache state, GPU clocks, and cold-evidence age remain review items for
every future real run.
