from __future__ import annotations

import random
from typing import Any

from evaluation.models import QueryRun


def build_candidate_pool(
    runs: list[QueryRun],
    depth: int,
    seed: int,
    query_texts: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if depth <= 0:
        raise ValueError("depth must be positive")
    by_query: dict[str, dict[str, dict[str, Any]]] = {}
    for run in runs:
        candidates = by_query.setdefault(run.query_id, {})
        for item in run.results[:depth]:
            candidates.setdefault(
                item.publication_id,
                {
                    "query_id": run.query_id,
                    "publication_id": item.publication_id,
                    "title": item.title,
                    "abstract": item.abstract,
                    "source_url": item.source_url,
                },
            )

    output = []
    for query_id in sorted(by_query):
        candidates = list(by_query[query_id].values())
        random.Random(f"{seed}:{query_id}").shuffle(candidates)
        for index, candidate in enumerate(candidates, start=1):
            output.append(
                {
                    "candidate_id": f"{query_id}-C{index:04d}",
                    "query_text": (query_texts or {}).get(query_id),
                    **candidate,
                    "relevance": "",
                }
            )
    return output
