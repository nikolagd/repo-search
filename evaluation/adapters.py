from __future__ import annotations

import inspect
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from importlib.metadata import version
from typing import Any

import bm25s

from evaluation.models import EvaluationQuery, QueryRun, RetrievedItem


TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
BM25_K1 = 1.2
BM25_B = 0.75
BM25_TITLE_BOOST = 2.0
BM25_LIBRARY = "bm25s"
BM25_LIBRARY_VERSION = version(BM25_LIBRARY)


def tokenize(text: str | None) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return TOKEN_PATTERN.findall(normalized)


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
