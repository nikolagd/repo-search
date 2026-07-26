# Runtime performance measurement

This package creates reproducible measurement artifacts for search latency,
Prometheus resources, and an existing embedding-backfill workflow. It is separate
from `evaluation`: it does not calculate relevance metrics or modify search,
ranking, embedding, or job behavior. The implementation and its synthetic tests
are not accepted thesis performance results.

Run the CLI from the repository root:

```powershell
$env:PERFORMANCE_API_TOKEN = "<runtime token>"
.\.venv\Scripts\python.exe -m performance_measurement search --config .\config.json --queries .\queries.json --output-dir .\.codex-tmp\performance\compose-search
.\.venv\Scripts\python.exe -m performance_measurement resources --config .\config.json --output-dir .\.codex-tmp\performance\compose-resources
.\.venv\Scripts\python.exe -m performance_measurement backfill --config .\config.json --output-dir .\.codex-tmp\performance\compose-backfill
Remove-Item Env:PERFORMANCE_API_TOKEN -ErrorAction SilentlyContinue
```

The output directory is never replaced unless `--overwrite` is explicit. JSON,
CSV, Markdown, and `SHA256SUMS` are built in a temporary sibling directory and
published as one atomic directory replacement. `.codex-tmp/performance` is
reserved for later real run artifacts and must remain untracked.

## Inputs and configuration

Search queries use this strict shape; query text is sent to the endpoint but is
not copied into result artifacts:

```json
{
  "queries": [
    {"id": "q01", "query": "example search", "limit": 10}
  ]
}
```

All commands require a deployment label and explicit model provenance. Optional
corpus size/hash fields are retained when supplied. Credentials are prohibited
in JSON and URLs; only the name of an environment variable is configured.

```json
{
  "deployment_label": "compose-gpu",
  "models": {
    "embedding_model": "intfloat/multilingual-e5-large",
    "embedding_model_revision": "verified-revision",
    "embedding_template_version": "verified-template",
    "llm_model": "verified-llm-model"
  },
  "corpus": {"size": 0, "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
  "search": {
    "endpoint": "http://localhost:8000/api/search",
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "warmup_repetitions": 1,
    "measured_repetitions": 10,
    "timeout_seconds": 180,
    "run_classification": "warm"
  },
  "prometheus": {
    "base_url": "http://localhost:9090",
    "timeout_seconds": 30,
    "metrics": [
      {"name": "container_cpu", "metric_type": "cpu", "unit": "cores", "query_kind": "query", "query": "<deployment-specific PromQL>"},
      {"name": "working_set", "metric_type": "ram", "unit": "bytes", "query_kind": "query_range", "query": "<deployment-specific PromQL>", "start": "<RFC3339 or epoch>", "end": "<RFC3339 or epoch>", "step": "15s"},
      {"name": "gpu_util", "metric_type": "gpu_utilization", "unit": "percent", "query_kind": "query", "query": "<DCGM_FI_DEV_GPU_UTIL PromQL>"},
      {"name": "gpu_framebuffer", "metric_type": "gpu_framebuffer", "unit": "bytes", "query_kind": "query", "query": "<DCGM framebuffer PromQL>"}
    ]
  },
  "backfill": {
    "job_service_url": "http://localhost:8006",
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "poll_interval_seconds": 2,
    "timeout_seconds": 3600,
    "request_timeout_seconds": 30,
    "job_timestamp_timezone": "UTC"
  }
}
```

Prometheus labels and queries are never hard-coded by the package. Instant
vectors, range matrices, and scalars retain each raw timestamp, value, and label
set. Summaries contain sample count, mean, median, min, max, p50, and p95. Empty,
failed, CPU, RAM, or GPU queries are `unavailable` with null summaries, never
zero. Non-finite samples invalidate the run.

## Latency and cold/warm rules

Requests are sequential and use `time.perf_counter_ns`. Raw rows retain phase,
query ID, repetition, HTTP status, outcome, nanosecond/millisecond latency,
result count, and parser mode. Warm-up rows are never included in measured
statistics. Failed HTTP, transport, and response-validation samples remain raw
rows and are counted as failed, but do not enter latency percentiles.

`warm` is the normal post-warm-up classification. `first_request` is allowed only
with zero warm-ups and does not claim a cold system. `cold` also requires zero
warm-ups plus a separate `--cold-evidence` JSON file created from an external
restart/readiness procedure:

```json
{
  "deployment_label": "compose-gpu",
  "source": "restart/readiness command log and operator record",
  "restart_completed_at_utc": "2026-01-01T10:00:00Z",
  "readiness_confirmed_at_utc": "2026-01-01T10:01:00Z"
}
```

The first request after valid evidence is `cold`; later requests are `warm`.
Without that evidence the CLI refuses `cold`, so process position alone cannot
create a cold claim. Percentiles use the same deterministic nearest-rank rule as
evaluation: sort values and select `ceil(p*n)-1`.

## Embedding backfill and comparability

The backfill command creates one Job Service `embedding_backfill` job and polls
its ID to a terminal state. It records observed queue time, service start/finish,
attempt count, processed records, service and observed duration, and records per
second. Existing Job Service timestamps are timezone-naive PostgreSQL values, so
the config must explicitly assert `job_timestamp_timezone: UTC`. The tool never
harvests data or manufactures stale embeddings merely to make a run possible.

Compose, Kubernetes, and monolith runs remain comparable only when query/config
hashes, Git commit, corpus identity, model provenance, repetition counts,
percentile convention, endpoint semantics, and measurement windows are matched.
Deployment labels identify environments but do not alter query ordering or
payloads. Resource summaries aggregate configured Prometheus series; they do not
make differently scoped PromQL expressions comparable. Cold evidence, readiness
criteria, machine contention, caches, GPU clocks, and monitoring scrape intervals
must be reviewed with every future real run.
