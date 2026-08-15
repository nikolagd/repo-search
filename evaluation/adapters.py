from __future__ import annotations

import inspect
import platform
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from importlib.metadata import version
from typing import Any

import bm25s
import snowballstemmer

from evaluation.models import EvaluationQuery, QueryMetadata, QueryRun, RetrievedItem


TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
BM25_K1 = 1.2
BM25_B = 0.75
BM25_TITLE_BOOST = 2.0
BM25_LIBRARY = "bm25s"
BM25_LIBRARY_VERSION = version(BM25_LIBRARY)
LANGUAGE_INDEPENDENT_LEXICAL_METHOD = "language_independent_lexical"
LANGUAGE_INDEPENDENT_LEXICAL_VERSION = "1.0"
LANGUAGE_INDEPENDENT_ANALYZER_VERSION = "unicode-word-char4-v1"
LANGUAGE_AWARE_LEXICAL_METHOD = "language_aware_lexical"
LANGUAGE_AWARE_LEXICAL_VERSION = "1.1"
LANGUAGE_AWARE_ANALYZER_VERSION = "serbian-latin-comparison-snowball-occurrence-v2"
CHAR_NGRAM_SIZE = 4
RRF_K = 60
SNOWBALL_LIBRARY = "snowballstemmer"
SNOWBALL_LIBRARY_VERSION = version(SNOWBALL_LIBRARY)


_SERBIAN_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "\u0430": "a",
        "\u0431": "b",
        "\u0432": "v",
        "\u0433": "g",
        "\u0434": "d",
        "\u0452": "\u0111",
        "\u0435": "e",
        "\u0436": "\u017e",
        "\u0437": "z",
        "\u0438": "i",
        "\u0458": "j",
        "\u043a": "k",
        "\u043b": "l",
        "\u0459": "lj",
        "\u043c": "m",
        "\u043d": "n",
        "\u045a": "nj",
        "\u043e": "o",
        "\u043f": "p",
        "\u0440": "r",
        "\u0441": "s",
        "\u0442": "t",
        "\u045b": "\u0107",
        "\u0443": "u",
        "\u0444": "f",
        "\u0445": "h",
        "\u0446": "c",
        "\u0447": "\u010d",
        "\u045f": "d\u017e",
        "\u0448": "\u0161",
    }
)
_SERBIAN_ASCII_FOLD = str.maketrans(
    {
        "\u010d": "c",
        "\u0107": "c",
        "\u0111": "dj",
        "\u0161": "s",
        "\u017e": "z",
    }
)
_SUPPORTED_LANGUAGE_ROUTES = ("serbian", "english", "mixed")


def tokenize(text: str | None) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return TOKEN_PATTERN.findall(normalized)


def normalize_language_independent_text(text: str | None) -> str:
    """Apply the complete language-neutral normalization used by the v1 baseline."""

    return unicodedata.normalize("NFKC", text or "").casefold()


def language_independent_word_tokens(text: str | None) -> list[str]:
    """Split normalized text on every code point except letters, marks, and numbers."""

    tokens: list[str] = []
    current: list[str] = []
    for character in normalize_language_independent_text(text):
        if unicodedata.category(character)[0] in {"L", "M", "N"}:
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def language_independent_character_ngrams(
    text: str | None,
    *,
    size: int = CHAR_NGRAM_SIZE,
) -> list[str]:
    """Return overlapping within-token character n-grams with a short-token fallback."""

    if type(size) is not int or size <= 0:
        raise ValueError("character n-gram size must be a positive integer")
    grams: list[str] = []
    for token in language_independent_word_tokens(text):
        if len(token) < size:
            grams.append(token)
        else:
            grams.extend(token[offset : offset + size] for offset in range(len(token) - size + 1))
    return grams


def normalize_serbian_latin(text: str | None) -> str:
    """Normalize Serbian Cyrillic and Latin text to one precise Latin form."""

    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return normalized.translate(_SERBIAN_CYRILLIC_TO_LATIN)


def normalize_serbian_latin_folded(text: str | None) -> str:
    """Return an ASCII-friendly Serbian Latin comparison form.

    This form is supplemental only. The original Unicode token channel remains
    indexed separately by :class:`LanguageAwareLexicalAdapter`.
    """

    canonical = normalize_serbian_latin(text)
    decomposed = unicodedata.normalize("NFD", canonical)
    without_marks = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    return without_marks.translate(_SERBIAN_ASCII_FOLD)


def serbian_latin_word_tokens(text: str | None) -> list[str]:
    """Tokenize the canonical Serbian Latin comparison representation."""

    return language_independent_word_tokens(normalize_serbian_latin(text))


def serbian_latin_folded_word_tokens(text: str | None) -> list[str]:
    """Tokenize the diacritic-insensitive Serbian Latin comparison form."""

    return language_independent_word_tokens(normalize_serbian_latin_folded(text))


def language_route(query_metadata: QueryMetadata) -> str:
    """Resolve a fixed analyzer route from declared query language and script."""

    language = query_metadata.language.strip().casefold()
    script = query_metadata.script.strip().casefold()
    if language in {"serbian", "sr", "srpski"} and script in {"latin", "cyrillic", "\u0107irilica"}:
        return "serbian"
    if language in {"english", "en"} and script == "latin":
        return "english"
    if language in {"serbian_mixed", "mixed", "serbian-mixed"} and script == "latin":
        return "mixed"
    raise ValueError(
        "unsupported language-aware query metadata route: "
        f"language={query_metadata.language!r}, script={query_metadata.script!r}"
    )


def _stem_tokens(tokens: list[str], stemmer: Any) -> list[str]:
    return [str(token) for token in stemmer.stemWords(tokens)] if tokens else []


def language_aware_analysis(
    text: str | None,
    route: str,
    *,
    serbian_stemmer: Any | None = None,
    english_stemmer: Any | None = None,
) -> dict[str, list[str]]:
    """Build precise and occurrence-preserving comparison tokens for one route.

    Each original source-token occurrence contributes at most one copy of each
    normalized variant. Repeated source-token occurrences remain repeated so
    BM25 term frequency and document length remain meaningful.
    """

    if route not in _SUPPORTED_LANGUAGE_ROUTES:
        raise ValueError(f"unsupported language-aware route: {route!r}")
    if serbian_stemmer is None and route in {"serbian", "mixed"}:
        serbian_stemmer = snowballstemmer.stemmer("serbian")
    if english_stemmer is None and route in {"english", "mixed"}:
        english_stemmer = snowballstemmer.stemmer("english")

    original_tokens = language_independent_word_tokens(text)
    canonical_tokens = serbian_latin_word_tokens(text)
    folded_tokens = serbian_latin_folded_word_tokens(text)
    stemmed_tokens: list[str] = []
    comparison_tokens: list[str] = []

    for original_token in original_tokens:
        canonical_variants = serbian_latin_word_tokens(original_token)
        folded_variants = serbian_latin_folded_word_tokens(original_token)
        variants: list[str] = []
        if route in {"serbian", "mixed"}:
            variants.extend(canonical_variants)
            variants.extend(folded_variants)
            serbian_stems = _stem_tokens(canonical_variants, serbian_stemmer)
            stemmed_tokens.extend(serbian_stems)
            variants.extend(serbian_stems)
        if route == "english":
            variants.append(original_token)
            english_stems = _stem_tokens([original_token], english_stemmer)
            stemmed_tokens.extend(english_stems)
            variants.extend(english_stems)
        elif route == "mixed":
            english_stems = _stem_tokens([original_token], english_stemmer)
            stemmed_tokens.extend(english_stems)
            variants.extend(english_stems)

        occurrence_seen: set[str] = set()
        for value in variants:
            if value and value not in occurrence_seen:
                comparison_tokens.append(value)
                occurrence_seen.add(value)

    return {
        "original_word_tokens": original_tokens,
        "canonical_serbian_tokens": canonical_tokens,
        "diacritic_insensitive_tokens": folded_tokens,
        "stemmed_tokens": stemmed_tokens,
        "comparison_tokens": comparison_tokens,
    }


def language_aware_comparison_tokens(
    text: str | None,
    route: str,
    *,
    serbian_stemmer: Any | None = None,
    english_stemmer: Any | None = None,
) -> list[str]:
    """Return the supplemental language-aware BM25 channel tokens."""

    return language_aware_analysis(
        text,
        route,
        serbian_stemmer=serbian_stemmer,
        english_stemmer=english_stemmer,
    )["comparison_tokens"]


class _BM25FieldIndex:
    """Wrap bm25s while making an entirely empty optional field well-defined."""

    def __init__(self, corpus: list[list[str]], *, k1: float, b: float):
        self.size = len(corpus)
        self.model = None
        if any(corpus):
            self.model = bm25s.BM25(k1=k1, b=b, method="lucene", backend="numpy")
            self.model.index(corpus, show_progress=False)

    def get_scores(self, query_terms: list[str]):
        if self.model is None or not query_terms:
            return [0.0] * self.size
        return self.model.get_scores(query_terms)


def _logical_index_statistics(corpus: list[list[str]]) -> dict[str, int]:
    vocabulary = set(term for document in corpus for term in document)
    return {
        "document_count": len(corpus),
        "nonempty_document_count": sum(bool(document) for document in corpus),
        "term_occurrence_count": sum(len(document) for document in corpus),
        "unique_term_count": len(vocabulary),
        "unique_term_utf8_bytes": sum(len(term.encode("utf-8")) for term in vocabulary),
    }


class KeywordBaselineAdapter:
    """Local baseline: 2x title term frequency plus 1x abstract term frequency."""

    method = "keyword"

    def __init__(self, publications: Iterable[dict[str, Any]]):
        self.publications = list(publications)

    async def retrieve(self, query: EvaluationQuery, limit: int) -> QueryRun:
        started = time.perf_counter()
        query_terms = set(tokenize(query.text))
        ranked: list[RetrievedItem] = []
        for publication in self.publications:
            title_counts = Counter(tokenize(publication.get("title")))
            abstract_counts = Counter(tokenize(publication.get("abstract")))
            score = float(
                2 * sum(title_counts[term] for term in query_terms)
                + sum(abstract_counts[term] for term in query_terms)
            )
            if score <= 0:
                continue
            ranked.append(
                RetrievedItem(
                    publication_id=str(publication["id"]),
                    score=score,
                    title=publication.get("title"),
                    abstract=publication.get("abstract"),
                    source_url=publication.get("source_url"),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.publication_id))
        return QueryRun(
            query.query_id,
            self.method,
            ranked[:limit],
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class BM25BaselineAdapter:
    """Fielded Lucene-style BM25 baseline over frozen title and abstract text.

    Each field is indexed independently with the same BM25 parameters. The final
    score is ``2.0 * title_score + abstract_score``. No stemming or stop-word
    removal is applied; :func:`tokenize` owns the complete text normalization.
    """

    method = "bm25"

    def __init__(
        self,
        publications: Iterable[dict[str, Any]],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
        title_boost: float = BM25_TITLE_BOOST,
    ):
        self.publications = list(publications)
        if not self.publications:
            raise ValueError("BM25 corpus must not be empty")
        if k1 < 0 or not 0 <= b <= 1 or title_boost <= 0:
            raise ValueError("invalid BM25 parameters")
        self.k1 = k1
        self.b = b
        self.title_boost = title_boost
        self._title_index = _BM25FieldIndex(
            [tokenize(publication.get("title")) for publication in self.publications],
            k1=k1,
            b=b,
        )
        self._abstract_index = _BM25FieldIndex(
            [tokenize(publication.get("abstract")) for publication in self.publications],
            k1=k1,
            b=b,
        )

    async def retrieve(self, query: EvaluationQuery, limit: int) -> QueryRun:
        started = time.perf_counter()
        query_terms = tokenize(query.text)
        title_scores = self._title_index.get_scores(query_terms)
        abstract_scores = self._abstract_index.get_scores(query_terms)
        ranked: list[RetrievedItem] = []
        for index, publication in enumerate(self.publications):
            score = float(self.title_boost * title_scores[index] + abstract_scores[index])
            if score <= 0:
                continue
            ranked.append(
                RetrievedItem(
                    publication_id=str(publication["id"]),
                    score=score,
                    title=publication.get("title"),
                    abstract=publication.get("abstract"),
                    source_url=publication.get("source_url"),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.publication_id))
        return QueryRun(
            query.query_id,
            self.method,
            ranked[:limit],
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def bm25_metadata() -> dict[str, Any]:
    return {
        "algorithm": "BM25",
        "implementation": BM25_LIBRARY,
        "implementation_version": BM25_LIBRARY_VERSION,
        "variant": "lucene",
        "k1": BM25_K1,
        "b": BM25_B,
        "tokenization": "Unicode NFKC, case-fold, Unicode \\w+ tokens",
        "stop_words": None,
        "fields": [
            {"name": "title", "boost": BM25_TITLE_BOOST},
            {"name": "abstract", "boost": 1.0},
        ],
        "field_combination": "2.0 * title BM25 score + abstract BM25 score",
        "tie_breaker": "publication_id ascending",
    }


class LanguageIndependentLexicalAdapter:
    """Strictly lexical word/character BM25 fused by reciprocal rank fusion."""

    method = LANGUAGE_INDEPENDENT_LEXICAL_METHOD

    def __init__(
        self,
        publications: Iterable[dict[str, Any]],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
        title_boost: float = BM25_TITLE_BOOST,
        character_ngram_size: int = CHAR_NGRAM_SIZE,
        rrf_k: int = RRF_K,
    ):
        self.publications = list(publications)
        if not self.publications:
            raise ValueError("language-independent lexical corpus must not be empty")
        if k1 < 0 or not 0 <= b <= 1 or title_boost <= 0:
            raise ValueError("invalid BM25 parameters")
        if type(character_ngram_size) is not int or character_ngram_size <= 0:
            raise ValueError("character n-gram size must be a positive integer")
        if type(rrf_k) is not int or rrf_k < 0:
            raise ValueError("RRF k must be a non-negative integer")
        self.k1 = k1
        self.b = b
        self.title_boost = title_boost
        self.character_ngram_size = character_ngram_size
        self.rrf_k = rrf_k

        word_title = [
            language_independent_word_tokens(item.get("title")) for item in self.publications
        ]
        word_abstract = [
            language_independent_word_tokens(item.get("abstract")) for item in self.publications
        ]
        char_title = [
            language_independent_character_ngrams(item.get("title"), size=character_ngram_size)
            for item in self.publications
        ]
        char_abstract = [
            language_independent_character_ngrams(item.get("abstract"), size=character_ngram_size)
            for item in self.publications
        ]
        self.index_statistics = {
            "measurement": "logical analyzer output; not serialized bm25s bytes",
            "word": {
                "title": _logical_index_statistics(word_title),
                "abstract": _logical_index_statistics(word_abstract),
            },
            "character_4gram": {
                "title": _logical_index_statistics(char_title),
                "abstract": _logical_index_statistics(char_abstract),
            },
        }
        self._word_title_index = _BM25FieldIndex(word_title, k1=k1, b=b)
        self._word_abstract_index = _BM25FieldIndex(word_abstract, k1=k1, b=b)
        self._char_title_index = _BM25FieldIndex(char_title, k1=k1, b=b)
        self._char_abstract_index = _BM25FieldIndex(char_abstract, k1=k1, b=b)

    def _component_ranks(
        self,
        title_index: _BM25FieldIndex,
        abstract_index: _BM25FieldIndex,
        query_terms: list[str],
    ) -> dict[int, int]:
        title_scores = title_index.get_scores(query_terms)
        abstract_scores = abstract_index.get_scores(query_terms)
        scores = [
            float(self.title_boost * title_scores[index] + abstract_scores[index])
            for index in range(len(self.publications))
        ]
        ranked = sorted(
            (index for index, score in enumerate(scores) if score > 0),
            key=lambda index: (-scores[index], str(self.publications[index]["id"])),
        )
        return {index: rank for rank, index in enumerate(ranked, start=1)}

    async def retrieve(self, query: EvaluationQuery, limit: int) -> QueryRun:
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        started = time.perf_counter()
        word_ranks = self._component_ranks(
            self._word_title_index,
            self._word_abstract_index,
            language_independent_word_tokens(query.text),
        )
        char_ranks = self._component_ranks(
            self._char_title_index,
            self._char_abstract_index,
            language_independent_character_ngrams(
                query.text,
                size=self.character_ngram_size,
            ),
        )
        fused_scores: dict[int, float] = {}
        for ranks in (word_ranks, char_ranks):
            for index, rank in ranks.items():
                fused_scores[index] = fused_scores.get(index, 0.0) + 1.0 / (self.rrf_k + rank)
        ranked_indices = sorted(
            fused_scores,
            key=lambda index: (-fused_scores[index], str(self.publications[index]["id"])),
        )
        results = [
            RetrievedItem(
                publication_id=str(self.publications[index]["id"]),
                score=fused_scores[index],
                title=self.publications[index].get("title"),
                abstract=self.publications[index].get("abstract"),
                source_url=self.publications[index].get("source_url"),
            )
            for index in ranked_indices[:limit]
        ]
        return QueryRun(
            query.query_id,
            self.method,
            results,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def language_independent_lexical_metadata(
    *,
    index_statistics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "method_id": LANGUAGE_INDEPENDENT_LEXICAL_METHOD,
        "method_version": LANGUAGE_INDEPENDENT_LEXICAL_VERSION,
        "algorithm": "RRF(word BM25, within-token character 4-gram BM25)",
        "implementation": BM25_LIBRARY,
        "implementation_version": BM25_LIBRARY_VERSION,
        "python_version": platform.python_version(),
        "unicode_database_version": unicodedata.unidata_version,
        "bm25_variant": "lucene",
        "bm25_parameters": {"k1": BM25_K1, "b": BM25_B},
        "analyzer_id": LANGUAGE_INDEPENDENT_ANALYZER_VERSION,
        "normalization_steps": [
            "replace null with empty string",
            "Unicode NFKC normalization",
            "Unicode default case folding",
            "split at code points whose Unicode general category is not Letter, Mark, or Number",
        ],
        "diacritics": "preserved",
        "transliteration": None,
        "stop_words": None,
        "stemming": None,
        "lemmatization": None,
        "word_tokens": "maximal runs of Unicode Letter/Mark/Number code points",
        "character_ngrams": {
            "minimum_n": CHAR_NGRAM_SIZE,
            "maximum_n": CHAR_NGRAM_SIZE,
            "boundaries": "within word tokens only; punctuation and whitespace never crossed",
            "boundary_markers": False,
            "short_token_rule": "a token shorter than four code points is emitted whole once",
        },
        "fields": [
            {"name": "title", "boost": BM25_TITLE_BOOST},
            {"name": "abstract", "boost": 1.0},
        ],
        "component_field_combination": "2.0 * title BM25 score + abstract BM25 score",
        "fusion": {
            "method": "reciprocal_rank_fusion",
            "k": RRF_K,
            "components": ["word_bm25", "character_4gram_bm25"],
            "component_weights": "equal; one reciprocal-rank contribution per component",
            "missing_document_contribution": 0.0,
        },
        "tie_breaking": [
            "component BM25 score descending, then publication_id ascending",
            "fused RRF score descending, then publication_id ascending",
        ],
        "semantic_components": [],
        "operating_assumption": "same-language lexical overlap or shared surface forms",
        "cross_language_mapping": None,
        "cross_lingual_retrieval": False,
        "multilingual_semantic_understanding": False,
    }
    if index_statistics is not None:
        metadata["index_statistics"] = index_statistics
    return metadata


class LanguageAwareLexicalAdapter(LanguageIndependentLexicalAdapter):
    """Language-aware extension with one explicit supplemental BM25 rank channel."""

    method = LANGUAGE_AWARE_LEXICAL_METHOD

    def __init__(
        self,
        publications: Iterable[dict[str, Any]],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
        title_boost: float = BM25_TITLE_BOOST,
        character_ngram_size: int = CHAR_NGRAM_SIZE,
        rrf_k: int = RRF_K,
    ):
        super().__init__(
            publications,
            k1=k1,
            b=b,
            title_boost=title_boost,
            character_ngram_size=character_ngram_size,
            rrf_k=rrf_k,
        )
        self._serbian_stemmer = snowballstemmer.stemmer("serbian")
        self._english_stemmer = snowballstemmer.stemmer("english")
        self._comparison_indexes: dict[str, tuple[_BM25FieldIndex, _BM25FieldIndex]] = {}
        comparison_statistics: dict[str, dict[str, dict[str, int]]] = {}
        for route in _SUPPORTED_LANGUAGE_ROUTES:
            title_tokens = [
                language_aware_comparison_tokens(
                    publication.get("title"),
                    route,
                    serbian_stemmer=self._serbian_stemmer,
                    english_stemmer=self._english_stemmer,
                )
                for publication in self.publications
            ]
            abstract_tokens = [
                language_aware_comparison_tokens(
                    publication.get("abstract"),
                    route,
                    serbian_stemmer=self._serbian_stemmer,
                    english_stemmer=self._english_stemmer,
                )
                for publication in self.publications
            ]
            self._comparison_indexes[route] = (
                _BM25FieldIndex(title_tokens, k1=k1, b=b),
                _BM25FieldIndex(abstract_tokens, k1=k1, b=b),
            )
            comparison_statistics[route] = {
                "title": _logical_index_statistics(title_tokens),
                "abstract": _logical_index_statistics(abstract_tokens),
            }
        self.index_statistics["language_aware_comparison"] = comparison_statistics

    async def retrieve(
        self,
        query: EvaluationQuery,
        limit: int,
        *,
        query_metadata: QueryMetadata | None = None,
    ) -> QueryRun:
        if query_metadata is None:
            raise ValueError("language-aware lexical retrieval requires query metadata")
        if query_metadata.query_id != query.query_id:
            raise ValueError("query metadata does not match the evaluation query")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        started = time.perf_counter()
        route = language_route(query_metadata)
        word_ranks = self._component_ranks(
            self._word_title_index,
            self._word_abstract_index,
            language_independent_word_tokens(query.text),
        )
        char_ranks = self._component_ranks(
            self._char_title_index,
            self._char_abstract_index,
            language_independent_character_ngrams(
                query.text,
                size=self.character_ngram_size,
            ),
        )
        comparison_title_index, comparison_abstract_index = self._comparison_indexes[route]
        comparison_ranks = self._component_ranks(
            comparison_title_index,
            comparison_abstract_index,
            language_aware_comparison_tokens(
                query.text,
                route,
                serbian_stemmer=self._serbian_stemmer,
                english_stemmer=self._english_stemmer,
            ),
        )
        fused_scores: dict[int, float] = {}
        for ranks in (word_ranks, char_ranks, comparison_ranks):
            for index, rank in ranks.items():
                fused_scores[index] = fused_scores.get(index, 0.0) + 1.0 / (self.rrf_k + rank)
        ranked_indices = sorted(
            fused_scores,
            key=lambda index: (-fused_scores[index], str(self.publications[index]["id"])),
        )
        results = [
            RetrievedItem(
                publication_id=str(self.publications[index]["id"]),
                score=fused_scores[index],
                title=self.publications[index].get("title"),
                abstract=self.publications[index].get("abstract"),
                source_url=self.publications[index].get("source_url"),
            )
            for index in ranked_indices[:limit]
        ]
        return QueryRun(
            query.query_id,
            self.method,
            results,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def language_aware_lexical_metadata(
    *,
    index_statistics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "method_id": LANGUAGE_AWARE_LEXICAL_METHOD,
        "method_version": LANGUAGE_AWARE_LEXICAL_VERSION,
        "algorithm": (
            "RRF(word BM25, within-token character 4-gram BM25, "
            "language-aware comparison BM25)"
        ),
        "implementation": BM25_LIBRARY,
        "implementation_version": BM25_LIBRARY_VERSION,
        "stemmer_library": SNOWBALL_LIBRARY,
        "stemmer_library_version": SNOWBALL_LIBRARY_VERSION,
        "stemmer_algorithms": {"serbian": "serbian", "english": "english"},
        "python_version": platform.python_version(),
        "unicode_database_version": unicodedata.unidata_version,
        "bm25_variant": "lucene",
        "bm25_parameters": {"k1": BM25_K1, "b": BM25_B},
        "title_boost": BM25_TITLE_BOOST,
        "analyzer_id": LANGUAGE_AWARE_ANALYZER_VERSION,
        "normalization_steps": [
            "replace null with empty string",
            "Unicode NFKC normalization and default case folding for the precise channel",
            "retain original Unicode Letter/Mark/Number word tokens",
            "canonical Serbian Cyrillic-to-Latin mapping for the comparison channel",
            "NFD mark removal plus Serbian Latin ASCII folding for a supplemental variant",
            "apply the declared Snowball stemmer to the declared language route",
        ],
        "serbian_cyrillic_to_latin_mapping": {
            "\u0430": "a",
            "\u0431": "b",
            "\u0432": "v",
            "\u0433": "g",
            "\u0434": "d",
            "\u0452": "\u0111",
            "\u0435": "e",
            "\u0436": "\u017e",
            "\u0437": "z",
            "\u0438": "i",
            "\u0458": "j",
            "\u043a": "k",
            "\u043b": "l",
            "\u0459": "lj",
            "\u043c": "m",
            "\u043d": "n",
            "\u045a": "nj",
            "\u043e": "o",
            "\u043f": "p",
            "\u0440": "r",
            "\u0441": "s",
            "\u0442": "t",
            "\u045b": "\u0107",
            "\u0443": "u",
            "\u0444": "f",
            "\u0445": "h",
            "\u0446": "c",
            "\u0447": "\u010d",
            "\u045f": "d\u017e",
            "\u0448": "\u0161",
        },
        "serbian_diacritic_insensitive_mapping": {
            "\u010d": "c",
            "\u0107": "c",
            "\u0111": "dj",
            "\u0161": "s",
            "\u017e": "z",
        },
        "query_routing": {
            "source": "query metadata language and script fields; no LLM detection",
            "routes": {
                "serbian": {
                    "language_values": ["Serbian", "sr", "srpski"],
                    "script_values": ["Latin", "Cyrillic", "\u0107irilica"],
                    "stemmer": "serbian",
                },
                "english": {
                    "language_values": ["English", "en"],
                    "script_values": ["Latin"],
                    "stemmer": "english",
                },
                "mixed": {
                    "language_values": ["Serbian_mixed", "mixed", "Serbian-mixed"],
                    "script_values": ["Latin"],
                    "stemmers": ["serbian", "english"],
                    "handling": "apply both fixed stemmers to the complete query and corpus text",
                },
            },
        },
        "precise_original_channel": {
            "word_tokens": "same Unicode Letter/Mark/Number tokens as language_independent_lexical",
            "character_ngrams": "same within-token character 4-grams as language_independent_lexical",
            "folded_forms_replace_original": False,
        },
        "comparison_channel": {
            "tokens": [
                "canonical_serbian_latin_tokens",
                "diacritic_insensitive_serbian_latin_tokens",
                "declared_language_stems",
            ],
            "token_occurrence_policy": (
                "one copy of each normalized variant per original source-token occurrence; "
                "repeated source-token occurrences remain repeated"
            ),
            "document_level_deduplication": False,
            "synonyms": None,
            "lemmatization": None,
            "stop_words": None,
        },
        "character_ngrams": {
            "minimum_n": CHAR_NGRAM_SIZE,
            "maximum_n": CHAR_NGRAM_SIZE,
            "boundaries": "within word tokens only; punctuation and whitespace never crossed",
            "boundary_markers": False,
            "short_token_rule": "a token shorter than four code points is emitted whole once",
        },
        "fields": [
            {"name": "title", "boost": BM25_TITLE_BOOST},
            {"name": "abstract", "boost": 1.0},
        ],
        "component_field_combination": "2.0 * title BM25 score + abstract BM25 score",
        "fusion": {
            "method": "reciprocal_rank_fusion",
            "k": RRF_K,
            "components": [
                "word_bm25_precise_original",
                "character_4gram_bm25_precise_original",
                "language_aware_comparison_bm25",
            ],
            "component_weights": "equal; one reciprocal-rank contribution per component",
            "missing_document_contribution": 0.0,
        },
        "tie_breaking": [
            "component BM25 score descending, then publication_id ascending",
            "fused RRF score descending, then publication_id ascending",
        ],
        "semantic_components": [],
        "operating_assumption": "lexical overlap, Serbian script/diacritic variants, or language-specific inflection",
        "cross_language_mapping": None,
        "cross_lingual_retrieval": False,
        "multilingual_semantic_understanding": False,
    }
    if index_statistics is not None:
        metadata["index_statistics"] = index_statistics
    return metadata


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class VectorOnlyAdapter:
    """Vector retrieval without query parsing, candidate merging, or ranking boosts."""

    method = "vector_only"

    def __init__(
        self,
        embed_query: Callable[[str], list[float] | Awaitable[list[float]]],
        fetch_vector_results: Callable[[list[float], int, int | None, int | None], Any],
    ):
        self.embed_query = embed_query
        self.fetch_vector_results = fetch_vector_results

    async def retrieve(self, query: EvaluationQuery, limit: int) -> QueryRun:
        started = time.perf_counter()
        vector = await _resolve(self.embed_query(query.text))
        rows = await _resolve(self.fetch_vector_results(vector, limit, None, None))
        results = []
        for row in rows:
            if isinstance(row, dict):
                publication_id = row["id"]
                score = (
                    row["cosine_similarity"]
                    if "cosine_similarity" in row
                    else 1 - float(row["cosine_distance"])
                )
                title, abstract, source_url = row.get("title"), row.get("abstract"), row.get("source_url")
            else:
                publication_id, title, abstract, source_url = row[0], row[1], row[2], row[3]
                score = 1 - float(row[5])
            results.append(RetrievedItem(str(publication_id), float(score), title, abstract, source_url))
        return QueryRun(
            query.query_id,
            self.method,
            results[:limit],
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class FullPipelineAdapter:
    """Adapter for the application Search Service `/search` response."""

    method = "full_pipeline"

    def __init__(self, search: Callable[[str, int], dict[str, Any] | Awaitable[dict[str, Any]]]):
        self.search = search

    async def retrieve(self, query: EvaluationQuery, limit: int) -> QueryRun:
        started = time.perf_counter()
        response = await _resolve(self.search(query.text, limit))
        results = [
            RetrievedItem(
                publication_id=str(row["id"]),
                score=float(row["score"]),
                title=row.get("title"),
                abstract=row.get("abstract"),
                source_url=row.get("source_url"),
            )
            for row in response.get("results", [])[:limit]
        ]
        plan = response.get("plan") or {}
        parser_mode = plan.get("parser_mode")
        return QueryRun(
            query.query_id,
            self.method,
            results,
            latency_ms=(time.perf_counter() - started) * 1000,
            parser_mode=parser_mode,
        )
