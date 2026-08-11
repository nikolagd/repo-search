# Language-independent lexical baseline for the thesis evaluation

## Role and bounded claim

The final candidate pool uses the machine method ID `language_independent_lexical` (version `1.0`) alongside the unchanged `vector_only` and `full_pipeline` runs. In Serbian thesis terminology, this comparator is a **jezički nezavisna leksička osnova**.

This is a classic, strictly lexical comparator. It scores shared normalized word forms and shared character substrings. It does not use embeddings, an LLM, machine translation, transliteration, dictionaries, semantic query expansion, a cross-encoder, relevance feedback, or query-specific rules. “Language-independent” means only that one Unicode-aware analyzer can process the Serbian Cyrillic, Serbian Latin, and English text present in this corpus without first identifying a language. It does not mean cross-lingual retrieval or multilingual semantic understanding. A Serbian query will generally not retrieve an English-only document unless the two share names, abbreviations, numbers, loanwords, or other surface character forms.

The comparator strengthens the fairness of the thesis evaluation: the semantic systems are no longer compared only with raw word BM25 applied to natural-language questions. The new baseline adds a published, knowledge-light character representation while remaining non-semantic.

## Fixed design decision

The design was frozen before inspecting publication 4349 or the new rankings:

1. a word BM25 component;
2. a within-token character 4-gram BM25 component;
3. equal, unsupervised reciprocal-rank fusion (RRF) with `k = 60`.

McNamee and Mayfield evaluated character n-gram tokenization across European-language retrieval collections and used 4-gram and 5-gram representations; their results and later CLEF procedure provide the independent precedent for a language-neutral 4-gram representation. Four was fixed here as the simpler, shorter of those established retrieval settings and was not selected from this evaluation's relevance grades. Cormack, Clarke, and Büttcher define RRF and fixed `k = 60` during their pilot before subsequent validation. RRF is appropriate because the numeric scores of the word and character indexes are not calibrated to each other. Robertson and Zaragoza provide the BM25 framework and parameter interpretation.

No existing relevance grade was used to select the gram size, fusion rule, fusion constant, BM25 parameters, title boost, normalization, or fields. The BM25 values and field boost were inherited unchanged from the historical raw comparator to isolate the effect of representation and fixed rank fusion.

## Exact analyzer

For every query, title, and abstract independently:

1. Replace a missing value with the empty string.
2. Apply Unicode Normalization Form KC (NFKC).
3. Apply Unicode default case folding through Python `str.casefold()`.
4. Scan code points from left to right. A word token is a maximal run whose Unicode General Category begins with `L` (Letter), `M` (Mark), or `N` (Number). Every other code point, including whitespace, punctuation, symbols, and underscore, is a boundary and is discarded.
5. The word component emits those tokens unchanged.
6. The character component emits every overlapping sequence of exactly four Unicode code points inside each word token. It never crosses a word boundary and adds no boundary markers. If a token contains fewer than four code points, emit that whole token once so names and abbreviations such as `AI` do not create an empty representation.

Diacritics are preserved. Serbian Latin and Serbian Cyrillic remain distinct scripts. There is no `č/ć/š/ž/đ` folding, Cyrillic-to-Latin conversion, or accent stripping. NFKC may compose/decompose compatibility forms as specified by Unicode, and case folding can change code-point length. Metadata records the Python version and `unicodedata.unidata_version` used for generation.

Examples:

| Input | Word tokens | Character representation (illustrative) |
|---|---|---|
| `VEŠTAČKA` | `veštačka` | `vešt`, `ešta`, `štač`, `tačk`, `ačka` |
| `ВЕШТАЧКА` | `вештачка` | Cyrillic 4-grams only |
| `repo-search` | `repo`, `search` | `repo`, `sear`, `earc`, `arch` |
| `AI` | `ai` | `ai` (short-token rule) |

## BM25 scoring

Each representation builds separate title and abstract indexes using `bm25s==0.3.10`, its `lucene` method, `k1 = 1.2`, and `b = 0.75`. For query term `t` and document field `d`, the conventional BM25 form is:

\[
BM25(d,q)=\sum_{t\in q} IDF(t)\frac{f(t,d)(k_1+1)}{f(t,d)+k_1\left(1-b+b\frac{|d|}{avgdl}\right)}
\]

where:

- `q` is the analyzed query term sequence;
- `f(t,d)` is the frequency of term `t` in field `d`;
- `|d|` is the analyzed field length;
- `avgdl` is the mean analyzed length of that field;
- `k1` controls term-frequency saturation;
- `b` controls length normalization;
- `IDF(t)` is the Lucene-style inverse-document-frequency weight supplied by the pinned implementation.

For each component `c` (word or character), title and abstract are scored independently and combined as:

\[
S_c(D,q)=2.0\,BM25_{c,title}(D,q)+1.0\,BM25_{c,abstract}(D,q).
\]

The `2.0` title boost, title/abstract fields, `k1`, and `b` are inherited from the historical raw BM25 evaluation and were not reselected. Missing/empty abstracts create an empty field representation and contribute zero.

Within a component, documents with a positive score are ordered by descending `S_c`; equal scores use ascending string `publication_id`. A document with zero component score is absent from that component's ranking.

## Reciprocal-rank fusion

Let `R = {word, char4}` and let `r_c(D)` be the one-based rank of document `D` in component `c`. The fused score is:

\[
RRF(D)=\sum_{c\in R:D\in c}\frac{1}{60+r_c(D)}.
\]

The fixed constant is `60`. Components have equal weight. An absent document contributes zero for that component. Fusion considers every positive-score document, not only the requested output depth. Final results are ordered by descending RRF score and then ascending string `publication_id`. This creates deterministic result identities and scores; measured latency and generation timestamps are naturally run-specific.

## Pseudocode

```text
build(corpus):
  for field in [title, abstract]:
    word_docs[field]  = analyze_words(corpus[field])
    char4_docs[field] = analyze_char4(corpus[field])
    word_index[field]  = BM25(word_docs[field], k1=1.2, b=0.75)
    char4_index[field] = BM25(char4_docs[field], k1=1.2, b=0.75)

retrieve(query, limit):
  word_scores  = 2 * word_title(query)  + word_abstract(query)
  char4_scores = 2 * char4_title(query) + char4_abstract(query)
  word_rank  = rank_positive(word_scores,  score desc, publication_id asc)
  char4_rank = rank_positive(char4_scores, score desc, publication_id asc)
  for each document in union(word_rank, char4_rank):
    score = (1/(60 + word_rank[document]) if present else 0)
          + (1/(60 + char4_rank[document]) if present else 0)
  return first limit documents by score desc, publication_id asc
```

## Frozen evaluation procedure and provenance

The generator `python -m evaluation.language_independent_lexical_artifacts` requires full expected SHA-256 values for the query set, corpus snapshot, and historical run file. It rejects a mismatch and refuses to overwrite an existing directory. It runs only `language_independent_lexical` locally, loads the frozen historical file, and copies the `vector_only` and `full_pipeline` records without invoking their embedding or service boundaries. It validates those reused records against the frozen corpus and compares canonical record hashes after writing.

Generated metadata includes method/analyzer versions, Python/Unicode/bm25s versions, every normalization operation, gram range, short-token and boundary rules, BM25 and field parameters, RRF rule, tie handling, logical index statistics, source/starting commits, corpus/query/frozen-run hashes, per-method reused-record hashes, generated artifact hashes, corpus/query counts, top-k, pool depth, seed, runtime, and UTC generation time. Logical index size is reported as analyzer term occurrences, vocabulary counts, and UTF-8 vocabulary bytes; it is not mislabeled as a serialized or resident-memory size.

The candidate pool is the union of the first five results from exactly `language_independent_lexical`, `vector_only`, and `full_pipeline` for each of the 30 frozen queries. Duplicate `(query_id, publication_id)` pairs are collapsed. Candidate order is reproducibly shuffled per query with seed `2026`. Method identity, rank, and score are excluded from the scoring sheet.

Completed grades are transferred only by exact `(query_id, publication_id)`. Blank cells remain blank; conflicts, duplicate pairs, invalid grades, old judged pairs leaving the pool, and newly unjudged pairs are all counted. No relevance is inferred. Final Precision@5, nDCG@5, and MRR@5 remain deferred until every new pooled pair is manually judged.

Dry-run artifacts and the scoring workbook are retained locally and excluded from the repository. The tracked methodology and source commit provide the reproducible implementation record.

### Frozen dry-run generated on 2026-08-09

The isolated dry run was generated from source commit `9c7208e42a12e5a2da65eeee2802f51f5616a1c6`, which descends directly from the recorded `test` starting commit `bb88a7bdfc65139ba2465cbc47f2347257b89001`.

Protected input hashes all matched before generation and after workbook validation:

- queries: `8fe5748b24f16f6c9e2d3037002eab1d4a613df1e1d419827da3768961d03f88`;
- corpus snapshot: `b366854b50c7abb40b51c29a943f89fdd22b0af33cac6b6cd3371ff2404eebce`;
- historical runs input: `86b36e45e377d42a07407150de14f309c4383f012466e22e5e3ae6d2db07264e`;
- original scoring workbook: `0fff8874465fced16a8b4b2581884613eb4fb2846ce7c55ae932f42451a8381d`.

Generated artifact hashes:

- `language-independent-lexical-runs.json`: `e89a820142acb94e9e8a4a6e394ee670e2e535285ea47b13a58e7318078b2430`;
- combined `runs.json`: `0240547bd1b9ab085ab26461d2b3fa8a09df2f6a5b750404864bcf8a35c24011`;
- blinded `candidates.csv`: `51b5f4765e1b2aa36b6d396d41720f969d4f1aa9a758d53c1ff40c7e6fec7dad`;
- `metadata.json`: `d77b8004c97a1520fb8d4fa59599d68cbfd6ad7ada5c6116a51b302676398273`;
- `comparison/lexical-comparison.json`: `451c48dc61297a900328fe0f806f2fec517c4c34eecb6936e1af2a1bcd325ea3`;
- expanded workbook: `b9fc752a571132ca5cf3b6ca50ac9b6a24a447d200414089b8a21eb192af8714`;
- final judgment-transfer report: `419e8ac571ddeb01e102a802ccafa9b55ba3f73590d99bee83cee295925b8c75`.

The pool contains 390 pairs. Relative to the historical raw-BM25 pool, 341 remain, 49 leave, and 49 enter. Of 253 completed judgments, 225 transfer exactly, 28 judged pairs leave the pool, the old workbook contains 137 blank rows, and the new workbook contains 165 unjudged rows. Conflicts, duplicate-pair errors, and invalid grades are all zero. These are workload/provenance counts, not effectiveness results.

The locally retained validated workbook contains three rendered and reopened sheets, four long-abstract entries expanded across the dedicated full-text sheet, and zero detected formula errors.

The non-qrels comparison retains 96 of 150 raw lexical top-five pairs and 341 of 390 full-pool pairs. Publication 4349 was inspected only after the method and run were frozen: it is outside depth five at ranks 17, 8, and 15 for q17, q19, and q20 respectively. This is post-hoc error analysis and is not a method-selection argument.

## Tests

Automated tests cover deterministic ordering/scores; NFKC and case folding; Serbian Cyrillic and Latin; English and mixed text; preserved diacritics and combining marks; punctuation boundaries; short tokens, names, and abbreviations; empty/missing abstracts; exact metadata; invalid gram/RRF/CLI values; frozen hash rejection; no calls to semantic retrieval during isolated artifact generation; unchanged reused vector/full-pipeline records; blinded/deduplicated pooling; and stable-pair judgment transfer with blank/conflict/unmatched counts. Synthetic fixtures contain no frozen human relevance targets.

## Evaluation strata

Query metadata must continue to record language, script, category, and topic. Chapter 6 analysis should distinguish at least same-language lexical needs from cross-language information needs. The lexical baseline is expected to be meaningful chiefly in the same-language/shared-form stratum. It must not be presented as evidence that character n-grams solve cross-language retrieval.

## Limitations and defensible claims

Defensible:

- one fixed analyzer handles the Serbian Cyrillic, Serbian Latin, and English surface text in this corpus;
- character 4-grams reduce dependence on exact whole-word equality and can share evidence across related inflected/spelling forms within the same script;
- the method is deterministic, transparent, reproducible, and strictly lexical;
- the method is stronger than using only unchanged natural-language word tokens as the comparator design, without asserting effectiveness before judgments are complete.

Not defensible:

- universal multilingual support;
- semantic understanding, synonymy, or conceptual equivalence;
- Serbian-to-English or Cyrillic-to-Latin retrieval without shared forms;
- robustness to arbitrary spelling differences, OCR errors, or transliteration;
- superiority over raw BM25 before complete judgments and prespecified metrics are available;
- reproduction of Google Scholar, DSpace/Solr, or any production repository index.

Character indexes are larger than word indexes and can match incidental substrings. RRF discards score magnitude and treats both components equally. NFKC/case folding are irreversible. Punctuation is discarded, so symbol-heavy terms such as `C++` lose distinctions. The short-token rule protects recall for abbreviations but can retain generic short forms.

## Thesis-ready methodology outline

1. Motivate a fair, non-semantic comparator for natural-language Serbian/English queries.
2. Define “language-independent” as analyzer reuse, not cross-lingual understanding.
3. Specify NFKC, case folding, Unicode-category token boundaries, preserved diacritics/scripts, exact 4-grams, and short-token fallback.
4. Present separate title/abstract BM25 equations and the inherited `2:1` field combination.
5. Present RRF with two equal components and fixed `k=60`.
6. State deterministic tie rules and complete provenance.
7. Explain frozen hashes, reuse of vector/full runs, depth-5 blinded pooling, and stable-pair judgment transfer.
8. Report pool churn/runtime/logical index size without using incomplete grades to claim improvement.
9. Analyze results by language/script/information-need stratum after judgments are complete.
10. Bound conclusions to lexical overlap and this frozen corpus/query set.

## Reference review for Zotero/thesis

These sources were introduced by this baseline and should be reviewed before being added to Zotero or cited in the thesis. No quotations are copied into this document.

1. Paul McNamee and James Mayfield, “Character N-Gram Tokenization for European Language Text Retrieval,” *Information Retrieval* 7, 73–97 (2004). DOI: `10.1023/B:INRT.0000009441.78971.be`. Stable URL: https://doi.org/10.1023/B:INRT.0000009441.78971.be. Supports character n-grams as a knowledge-light/language-neutral retrieval representation and the established 4-/5-gram experimental precedent; it does not support a claim of semantic or universal multilingual retrieval.
2. Gordon V. Cormack, Charles L. A. Clarke, and Stefan Büttcher, “Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods,” SIGIR 2009. DOI: `10.1145/1571941.1572114`; ISBN: `978-1-60558-483-6`. Author PDF: https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf. Supports the RRF equation, rank-only fusion motivation, and fixed `k=60`.
3. Stephen Robertson and Hugo Zaragoza, “The Probabilistic Relevance Framework: BM25 and Beyond,” *Foundations and Trends in Information Retrieval* 3(4), 333–389 (2009). DOI: `10.1561/1500000019`; ISBN: `978-1-60198-308-4`. Stable URL: https://doi.org/10.1561/1500000019. Supports the BM25 framework, term-frequency saturation, document-length normalization, and parameter interpretation.
4. Mark Davis and Martin Dürst, “Unicode Normalization Forms,” Unicode Standard Annex #15. Stable URL: https://unicode.org/reports/tr15/. No DOI/ISBN. Authoritative source for NFKC behavior; the concrete Unicode data version remains runtime provenance.
5. The Unicode Consortium, *The Unicode Standard*, default case operations and CaseFolding data. Stable URLs: https://www.unicode.org/versions/latest/ and https://www.unicode.org/Public/UCD/latest/ucd/CaseFolding.txt. ISBN for the continuously updated online standard is not assigned. Supports the case-folding operation and Unicode General Category terminology.

Implementation-only provenance (normally not a thesis literature citation): `bm25s==0.3.10`, https://github.com/xhluca/bm25s, Apache-2.0. It is an existing pinned dependency; this change adds no library.
