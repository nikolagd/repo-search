# Search Evaluation Foundation

This package compares the frozen three-method evaluation plus an opt-in language-aware lexical fourth method without supplying evaluation topics, relevance judgments, or thesis results. Files in `templates/` are intentionally empty. Real queries and judgments must be created and reviewed separately.

Production run collection is documented in `COLLECT_RUNS.md` and available through `python -m evaluation collect-runs`.

For historical reproduction, `python -m evaluation.bm25_artifacts` still builds the raw `bm25` comparison. The final candidate generator is `python -m evaluation.language_independent_lexical_artifacts`; it creates a new isolated directory containing `language-independent-lexical-runs.json`, `runs.json` with `language_independent_lexical`/`vector_only`/`full_pipeline`, a blinded `candidates.csv`, and complete `metadata.json`. Both commands require expected SHA-256 values and refuse to overwrite an existing output directory.

Post-assessment judgment import, detailed reporting, and optional assessor agreement are documented in `REPORTING.md`. Human relevance scoring remains mandatory; the code never creates or infers judgments.

## Methods

- `keyword`: legacy deterministic token-frequency baseline. Text is Unicode NFKC-normalized and case-folded, then split into Unicode word tokens. The score remains `2 * title query-term frequency + abstract query-term frequency` for backward compatibility with historical frozen artifacts. It is not part of the final evaluation method set.
- `bm25`: historical raw lexical comparator. `bm25s==0.3.10` supplies Lucene-style BM25 scoring with `k1=1.2` and `b=0.75`. It remains available for exact reproduction of the earlier pool.
- `language_independent_lexical`: final candidate lexical comparator. It fuses a Unicode word BM25 rank and a within-word character 4-gram BM25 rank using reciprocal-rank fusion with fixed `k=60`. Each component retains `k1=1.2`, `b=0.75`, and `2.0 * title BM25 + abstract BM25`. It is language-independent only in preprocessing: there is no translation, transliteration, semantic equivalence, stemming, lemmatization, or stop-word list. A Serbian query ordinarily cannot retrieve an English-only document unless they share surface forms.
- `language_aware_lexical`: evaluation-only extension of the preceding comparator. It retains the precise original word and within-token character 4-gram channels and adds one equal-weight language-aware BM25 rank channel. Serbian routes use declared query metadata (`language` and `script`) for canonical Cyrillic-to-Latin comparison, diacritic-insensitive Serbian Latin variants, and the pinned Snowball Serbian stemmer. English routes use the pinned Snowball English stemmer. The one `Serbian_mixed` route applies both stemmers deterministically. No synonyms, lemmatization, stop-word tuning, vector retrieval, or LLM language detection is used.
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

Both candidate pooling and reporting expect exactly one run for every query and method. The backward-compatible default final methods remain `language_independent_lexical`, `vector_only`, and `full_pipeline`; pass `language_aware_lexical` explicitly for the fourth-method comparison. Historical `bm25` and legacy `keyword` remain explicit supported overrides. Duplicate method arguments and missing, duplicate, or unknown query/method runs are rejected, so pooling and every aggregate use the identical query set, including zero-result runs. Duplicate query IDs, judgments, retrieved publication IDs, ranks, gapped/non-one-based ranks, unknown query references, non-finite scores, and negative/non-finite latency are also rejected.

## Metrics

- Precision@k: positively judged retrieved documents in the first k positions divided by k.
- Recall@k: positively judged retrieved documents in the first k positions divided by all positively judged documents for the query.
- MRR: reciprocal rank of the first positively judged result over the complete supplied run.
- MRR@k: reciprocal rank of the first positively judged result only within the first k positions.
- nDCG@k: DCG with gain `2^relevance - 1` and logarithmic discount, divided by the ideal ordering of available graded judgments.

The replacement candidate protocol is defined in `LANGUAGE_INDEPENDENT_LEXICAL_BASELINE.md`; `FINAL_BM25_PROTOCOL.md` is retained as the historical raw-BM25 protocol. Generic reporting remains reusable and therefore still emits Recall and unbounded MRR, but those fields are not supported claims for the depth-5 protocol.

## Commands

Create a deterministic, method-blind assessment pool. The output intentionally contains no method names or method membership:

```powershell
.\.venv\Scripts\python.exe -m evaluation candidate-pool --queries path\to\queries.json --runs path\to\runs.json --output path\to\candidates.csv --depth 10 --seed 2026 --methods language_independent_lexical vector_only full_pipeline
```

Collect the language-aware method only with the frozen query metadata:

```powershell
.\.venv\Scripts\python.exe -m evaluation collect-runs --queries path\to\queries.json --query-metadata path\to\query-metadata.json --output path\to\runs.json --methods language_aware_lexical --limit 20 ...
```

After manual judgments have been completed, export the assessment sheet as UTF-8 CSV and validate/import it:

```powershell
.\.venv\Scripts\python.exe -m evaluation import-judgments --queries path\to\queries.json --pool-template path\to\original-candidates.csv --assessment path\to\completed-assessment.csv --output path\to\judgments.json
```

Then calculate the detailed report:

```powershell
.\.venv\Scripts\python.exe -m evaluation report --queries path\to\queries.json --query-metadata path\to\query-metadata.json --judgments path\to\judgments.json --runs path\to\runs.json --output-dir path\to\report --corpus-size 1000 --k 5 10 --embedding-model intfloat/multilingual-e5-large --ranking-config '{"candidate_multiplier":6}' --methods language_independent_lexical vector_only full_pipeline
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
