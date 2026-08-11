# Final BM25 comparison protocol

> Historical protocol: this file preserves the raw-BM25 evaluation design. The replacement candidate protocol is `LANGUAGE_INDEPENDENT_LEXICAL_BASELINE.md`, whose method ID is `language_independent_lexical`. Raw `bm25` remains reproducible but is no longer the candidate pool's lexical method.

The final thesis comparison uses all 30 frozen queries and exactly three methods: `bm25`, `vector_only`, and `full_pipeline`. The candidate set is the method-blind union of each method's first five results for each query, deduplicated by `(query_id, publication_id)` with seed `2026`. Every pooled pair must receive a manual 0/1/2 relevance judgment before final effectiveness results are calculated.

The primary relevance metrics are **Precision@5**, **nDCG@5**, and **MRR@5**. This pooled depth supports claims only through rank 5. Recall, Recall@10, nDCG@10, unbounded MRR, and result quality below rank 5 must not be reported as conclusions from this pool. Generic evaluator output may contain those reusable fields; they are outside this protocol and must be ignored for the final comparison.

Depth 5 is the methodological floor because it evaluates a useful first result page for every method while keeping complete manual assessment feasible. Depths 3 and 4 leave part of that page unevaluated; depth 10 substantially increases the assessment burden. The ignored artifact handoff records the measured pool-size comparison for depths 3, 4, 5, and 10.

`bm25` is the final lexical comparator: a reproducible Lucene-style baseline over the frozen local corpus, using pinned `bm25s==0.3.10`, `k1=1.2`, `b=0.75`, Unicode NFKC normalization and case-folding, Unicode word tokens, separate title and abstract indexes, no stop-word removal, and the documented score `2.0 * title BM25 + abstract BM25`. It is not a reproduction of Google Scholar and is not claimed to be identical to either repository's DSpace/Solr configuration.

`vector_only` is semantic retrieval without query parsing. `full_pipeline` is the complete application search path with its existing parser-mode behavior. The legacy `keyword` token-frequency method remains available only for historical compatibility and is excluded from this protocol.
