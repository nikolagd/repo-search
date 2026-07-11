from __future__ import annotations

import inspect
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from evaluation.models import EvaluationQuery, QueryRun, RetrievedItem


TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


def tokenize(text: str | None) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return TOKEN_PATTERN.findall(normalized)


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
                score = row.get("cosine_similarity", 1 - float(row["cosine_distance"]))
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
        parser_mode = plan.get("parser_mode") or ("fallback" if plan.get("used_fallback") else "unknown")
        return QueryRun(
            query.query_id,
            self.method,
            results,
            latency_ms=(time.perf_counter() - started) * 1000,
            parser_mode=parser_mode,
        )
