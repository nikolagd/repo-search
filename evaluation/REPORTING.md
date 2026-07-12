# Post-Assessment Evaluation Reporting

This workflow begins only after a human assessor has completed the blinded candidate assessment. It validates and converts assessor input; it never infers relevance, fills blanks, reconciles disagreements, or creates thesis conclusions.

Do not inspect `runs.json` or method-specific rankings until blinded assessment is complete. Human scoring remains mandatory.

## 1. Export And Import Judgments

Complete the `Procena` sheet in the assessment workbook, then export that sheet as a UTF-8 CSV. The tracked command intentionally does not parse XLSX and adds no spreadsheet runtime dependency.

Keep the original generated `candidates.csv` unchanged as the immutable pool template. Import the completed CSV with:

```powershell
.\.venv\Scripts\python.exe -m evaluation import-judgments `
  --queries path\to\queries.json `
  --pool-template path\to\original-candidates.csv `
  --assessment path\to\completed-assessment.csv `
  --output path\to\judgments.json
```

Use `--overwrite` only when intentionally replacing an existing output. The output may not alias an input file.

Both CSV files must contain these columns in this exact order:

```text
candidate_id,query_text,query_id,publication_id,title,abstract,source_url,relevance
```

Validation is all-or-nothing:

- headers must match exactly;
- candidate IDs must be nonblank and unique;
- each query/publication pair must be unique;
- every template candidate must occur exactly once in the assessment;
- additional or missing candidates are rejected;
- candidate ID, query text, query ID, publication ID, title, abstract, and source URL must exactly match the immutable template, including empty nullable metadata cells;
- every query ID and query text must match `queries.json`;
- template relevance cells must be blank;
- assessment relevance must be exactly `0`, `1`, or `2` with no whitespace, decimal notation, Boolean text, formula, or explanation.

Physical row order may change because identity is checked by candidate ID. The generated `judgments.json` contains only `query_id`, `publication_id`, and integer `relevance`. It is written through a validated temporary file and atomically published only after the complete assessment passes.

## 2. Query Metadata

Create query metadata from `evaluation/templates/query_metadata.template.json`. The schema is `evaluation/schemas/query_metadata.schema.json`:

```json
{
  "query_metadata": [
    {
      "query_id": "q1",
      "language": "sr",
      "script": "latin",
      "category": "conceptual",
      "topic": "veštačka inteligencija"
    }
  ]
}
```

Each record has exactly `query_id`, `language`, `script`, `category`, and `topic`. Values are free-form nonblank strings so Serbian Latin, Serbian Cyrillic, English, and mixed terminology remain unchanged. Query IDs must be unique and cover the evaluated query set exactly; missing, unknown, duplicate, or extra fields fail validation.

Choose and document a controlled spelling/case vocabulary for grouping values. For example, `sr` and `Serbian` would otherwise form different groups.

## 3. Detailed Report

```powershell
.\.venv\Scripts\python.exe -m evaluation report `
  --queries path\to\queries.json `
  --query-metadata path\to\query-metadata.json `
  --judgments path\to\judgments.json `
  --runs path\to\runs.json `
  --output-dir path\to\report `
  --corpus-size 5646 `
  --k 5 10 `
  --embedding-model intfloat/multilingual-e5-large `
  --ranking-config path\to\ranking-config.json `
  --methods keyword vector_only full_pipeline
```

`--ranking-config` also accepts an inline JSON object. The current Git commit and a UTC timestamp are recorded automatically. Use `--overwrite` only to replace a previously generated report directory.

Ranking configuration is output metadata, so it must contain only non-sensitive ranking parameters. Keys indicating tokens, passwords, secrets, credentials, administrator data, API keys, database URLs/DSNs, and PostgreSQL URL values are rejected.

The command requires exactly one run for every query/method pair, including explicit zero-result runs. It writes the report into a temporary sibling directory and publishes it only after every file is generated successfully.

Outputs:

- `report.json`: machine-readable metadata and all breakdowns;
- `metrics.csv`: retained aggregate method effectiveness metrics;
- `per_query_metrics.csv`: query metadata, effectiveness, latency, parser mode, result count, positive-judgment count, and explicit no-positive flag for every query/method pair;
- `grouped_metrics.csv`: method macro averages by language, script, and category;
- `latency_summary.csv`: run/sample counts, mean, median, minimum, maximum, and p95 by method;
- `parser_mode_summary.csv`: parser-mode counts/percentages with explicit applicability;
- `summary.md`: concise tables suitable as verified input to later thesis writing.

Grouped effectiveness is calculated from the same per-query rows as aggregate effectiveness. A complete matrix is revalidated before grouping, so no group can accidentally compare methods over unequal query coverage.

Latency p95 uses the deterministic nearest-rank convention:

```text
sorted_values[ceil(0.95 * n) - 1]
```

Only non-null latency samples enter latency statistics; total, measured, and missing counts are reported. Keyword and vector-only parser modes remain JSON `null` with `applicability=not_applicable`. Full-pipeline null mode is `applicability=unreported`; reported modes retain their actual values. No parser mode is invented.

Effectiveness and latency remain separate. No synthetic overall score is calculated.

## 4. Metrics And Judgment Policy

The report reuses `evaluation.metrics`:

- Precision@k: positive retrieved judgments in the first k divided by k;
- Recall@k: positive retrieved judgments in the first k divided by all positive judgments for that query;
- MRR: reciprocal rank of the first positively judged result over the complete run;
- nDCG@k: gain `2^relevance - 1`, logarithmic rank discount, normalized by the ideal available graded judgments.

Grades 1 and 2 count as positive for Precision, Recall, and MRR. nDCG retains the 0/1/2 grading. Unjudged retrieved documents are treated as nonrelevant. A query without positive judgments remains in every macro average and receives zero Recall, MRR, and nDCG; its status is reported explicitly.

The report records Git commit, UTC timestamp, corpus/query sizes, methods, k values, model, ranking configuration, input SHA-256 values, grade counts, count of queries without positive judgments, parser counts, percentile convention, and validation assumptions. It never records secrets or service/database credentials.

## 5. Optional Assessor Agreement

When a second independent judgment file exists:

```powershell
.\.venv\Scripts\python.exe -m evaluation agreement `
  --judgments-a path\to\assessor-a.json `
  --judgments-b path\to\assessor-b.json `
  --output-dir path\to\agreement
```

Both files must contain an identical nonempty set of query/publication pairs. Missing, additional, duplicate, or malformed judgments are rejected. Disagreements are never reconciled automatically and both source files remain unchanged.

Outputs are `agreement.json`, `confusion_matrix.csv`, `disagreements.csv`, and `summary.md`. The 3x3 confusion matrix uses assessor A grades as rows and assessor B grades as columns, ordered 0, 1, 2.

Formulas:

- exact agreement: `100 * diagonal_count / pair_count`;
- unweighted Cohen's kappa: `(p_o - p_e) / (1 - p_e)`;
- quadratic weight: `((grade_a - grade_b) / 2)^2`;
- quadratic weighted kappa: `1 - observed_weighted_disagreement / expected_weighted_disagreement`.

If a kappa denominator is zero, the kappa is undefined and is written as JSON `null`. It is never silently replaced by 0 or 1. Exact agreement remains reportable. Disagreement rows are deterministically ordered for later adjudication.

## Limitations

- Human relevance decisions determine validity; this code validates structure, not assessor expertise or rubric quality.
- Treating unjudged documents as nonrelevant can depress metrics when pooling is shallow.
- Macro averages describe the supplied query set and frozen corpus only.
- Query-metadata grouping depends on a consistently maintained vocabulary.
- One assessor provides no inter-assessor reliability evidence; agreement output is optional and requires genuine second-assessor data.
- Generated Markdown is evidence input, not thesis prose or an automatic conclusion.
