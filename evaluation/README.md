# Search Evaluation Foundation

This package compares three retrieval methods without supplying evaluation topics, relevance judgments, or thesis results. Files in `templates/` are intentionally empty. Real queries and judgments must be created and reviewed separately.

Production run collection is documented in `COLLECT_RUNS.md` and available through `python -m evaluation collect-runs`.

Post-assessment judgment import, detailed reporting, and optional assessor agreement are documented in `REPORTING.md`. Human relevance scoring remains mandatory; the code never creates or infers judgments.

## Methods

- `keyword`: deterministic local baseline over the supplied publication corpus. Text is Unicode NFKC-normalized and case-folded, then split into Unicode word tokens. The score is `2 * title query-term frequency + abstract query-term frequency`, using unique query terms. Documents with score zero are omitted. Ties are resolved by ascending string publication ID. This is a simple internal comparator; it does not reproduce DSpace, Google Scholar, PostgreSQL full-text search, or another external engine.
- `vector_only`: embeds the original query once and invokes the existing vector-fetch boundary with no LLM parsing, phrase boosts, candidate merging, or query-coverage boost. Its embedder and fetcher are injected, which permits deterministic tests and use with the existing Search/Embedding service functions.
- `full_pipeline`: consumes the application Search Service response, including final scores and `plan.parser_mode`. Tests inject a deterministic service callable, so Ollama is never contacted.

## Machine-readable formats

All identifiers are strings to avoid assumptions about future corpus sources.

- Queries (`queries.json`): `{"queries": [{"query_id": "...", "text": "..."}]}`.
- Judgments (`judgments.json`): `{"judgments": [{"query_id": "...", "publication_id": "...", "relevance": 0|1|2}]}` where 0 is irrelevant, 1 partially relevant, and 2 relevant.
- Runs (`runs.json`): `{"runs": [...]}` with one object for every query/method pair. Each run retains `query_id`, `method`, optional `latency_ms`, optional `parser_mode`, and a `results` array. Results contain one-based contiguous `rank`, `publication_id`, numeric `score`, and optional display fields. Zero-result runs are represented by `"results": []` and are never dropped.
- Query metadata (`query-metadata.json`): exact query coverage with language, script, category, and topic fields.
- Detailed report (`report.json`): reproducibility metadata plus aggregate, per-query, grouped, latency, and parser-mode breakdowns. Matching CSV files and `summary.md` are generated atomically.

Schemas are in `schemas/`. Empty starting files are in `templates/`. Synthetic test fixtures live only under `tests/` and are not evaluation results.

Unjudged retrieved documents are treated as nonrelevant. A query with no positive judgments receives zero Recall, MRR, and nDCG; Precision is also zero unless a positively judged result exists. Such queries remain in macro averages and are counted explicitly as `queries_without_relevant_judgments`.

Both candidate pooling and reporting expect exactly one run for every query and method. Default methods are `keyword`, `vector_only`, and `full_pipeline`; override them with `--methods`. Duplicate method arguments and missing, duplicate, or unknown query/method runs are rejected, so pooling and every aggregate use the identical query set, including zero-result runs. Duplicate query IDs, judgments, retrieved publication IDs, ranks, gapped/non-one-based ranks, unknown query references, non-finite scores, and negative/non-finite latency are also rejected.

## Metrics

- Precision@k: positively judged retrieved documents in the first k positions divided by k.
- Recall@k: positively judged retrieved documents in the first k positions divided by all positively judged documents for the query.
- MRR: reciprocal rank of the first positively judged result over the complete supplied run.
- nDCG@k: DCG with gain `2^relevance - 1` and logarithmic discount, divided by the ideal ordering of available graded judgments.

## Commands

Create a deterministic, method-blind assessment pool. The output intentionally contains no method names or method membership:

```powershell
.\.venv\Scripts\python.exe -m evaluation candidate-pool --queries path\to\queries.json --runs path\to\runs.json --output path\to\candidates.csv --depth 10 --seed 2026 --methods keyword vector_only full_pipeline
```

After manual judgments have been completed, export the assessment sheet as UTF-8 CSV and validate/import it:

```powershell
.\.venv\Scripts\python.exe -m evaluation import-judgments --queries path\to\queries.json --pool-template path\to\original-candidates.csv --assessment path\to\completed-assessment.csv --output path\to\judgments.json
```

Then calculate the detailed report:

```powershell
.\.venv\Scripts\python.exe -m evaluation report --queries path\to\queries.json --query-metadata path\to\query-metadata.json --judgments path\to\judgments.json --runs path\to\runs.json --output-dir path\to\report --corpus-size 1000 --k 5 10 --embedding-model intfloat/multilingual-e5-large --ranking-config '{"candidate_multiplier":6}' --methods keyword vector_only full_pipeline
```

Optional second-assessor agreement:

```powershell
.\.venv\Scripts\python.exe -m evaluation agreement --judgments-a path\to\assessor-a.json --judgments-b path\to\assessor-b.json --output-dir path\to\agreement
```

The report records the current Git commit unless `--git-commit` is supplied, a UTC timestamp, corpus/query sizes, methods, k values, model, ranking configuration, input hashes, grade counts, parser modes, latency summaries, and validation assumptions. See `REPORTING.md` for formulas, validation rules, atomic output behavior, and limitations.

## Collecting real evidence

1. Define real information needs before viewing system results. Assign stable query IDs and record the query wording without method-specific rewriting.
2. Run all compared methods over the same frozen corpus and configuration, retaining ranks, scores, latency, parser mode, Git commit, and model/ranking metadata.
3. Export a pooled candidate file with a recorded depth and seed. Give assessors the blinded file, not method-specific runs.
4. Without inspecting method-specific `runs.json`, have a qualified human assign 0/1/2 judgments under a written relevance rubric. Do not infer missing judgments from ranks or scores.
5. Reconcile duplicate or conflicting assessments using a documented process, preserve the raw assessments, then generate the final judgment file.
6. Generate and archive the machine-readable report. Treat conclusions as valid only for the recorded corpus, queries, judgments, and system version.
