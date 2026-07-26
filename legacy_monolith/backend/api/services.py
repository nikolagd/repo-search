from __future__ import annotations

from datetime import date, datetime
from typing import Any

from etl.db import get_connection


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return str(value)


def check_database() -> bool:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    finally:
        conn.close()


def get_repositories() -> list[dict[str, Any]]:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, oai_endpoint, last_harvest, refresh_interval
                FROM repository
                ORDER BY id
                """
            )

            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "oai_endpoint": row[2],
                    "last_harvest": serialize_datetime(row[3]),
                    "refresh_interval": row[4],
                }
                for row in cur.fetchall()
            ]
    finally:
        conn.close()


def get_stats() -> dict[str, Any]:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM repository")
            repositories = cur.fetchone()[0]

            cur.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE embedding IS NOT NULL),
                    MAX(date)
                FROM publication
                WHERE is_active = TRUE
                """
            )
            publications, publications_with_embeddings, last_harvest = cur.fetchone()

            return {
                "repositories": repositories,
                "publications": publications,
                "publications_with_embeddings": publications_with_embeddings,
                "last_harvest": serialize_datetime(last_harvest),
            }
    finally:
        conn.close()


def run_search(query: str, limit: int) -> dict[str, Any]:
    from search.query_handler import parse_query
    from search.search import semantic_search

    parsed = parse_query(query)
    results = semantic_search(
        embedding_queries=parsed["embedding_queries"],
        limit=limit,
        year_from=parsed["year_from"],
        year_to=parsed["year_to"],
        topic_phrases=parsed["topic_phrases"],
        ranking_phrases=parsed["ranking_phrases"],
    )

    return {
        "query": query,
        "limit": limit,
        "plan": parsed,
        "results": [serialize_search_result(result) for result in results],
        "total": len(results),
    }


def serialize_search_result(result: dict[str, Any]) -> dict[str, Any]:
    authors = result.get("authors") or []

    return {
        **result,
        "authors": list(authors),
        "date": serialize_datetime(result.get("date")),
        "cosine_distance": round(float(result.get("cosine_distance", 0)), 6),
        "cosine_similarity": round(float(result.get("cosine_similarity", 0)), 6),
        "topic_boost": round(float(result.get("topic_boost", 0)), 6),
        "ranking_boost": round(float(result.get("ranking_boost", 0)), 6),
        "coverage_boost": round(float(result.get("coverage_boost", 0)), 6),
        "score": round(float(result.get("score", 0)), 6),
    }
