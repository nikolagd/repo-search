from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EvaluationQuery:
    query_id: str
    text: str


@dataclass(frozen=True)
class QueryMetadata:
    query_id: str
    language: str
    script: str
    category: str
    topic: str


@dataclass(frozen=True)
class Judgment:
    query_id: str
    publication_id: str
    relevance: int

    def __post_init__(self) -> None:
        if type(self.relevance) is not int or self.relevance not in {0, 1, 2}:
            raise ValueError("relevance must be 0, 1, or 2")


@dataclass(frozen=True)
class RetrievedItem:
    publication_id: str
    score: float
    title: str | None = None
    abstract: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class QueryRun:
    query_id: str
    method: str
    results: list[RetrievedItem]
    latency_ms: float | None = None
    parser_mode: str | None = None

    def record(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "method": self.method,
            "latency_ms": self.latency_ms,
            "parser_mode": self.parser_mode,
            "results": [
                {"rank": rank, **asdict(item)}
                for rank, item in enumerate(self.results, start=1)
            ],
        }
