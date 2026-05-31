from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from microservices.common.config import service_url
from microservices.common.db import get_connection
from microservices.common.http import observed_async_request, raise_for_service
from microservices.common.observability import observe_search_request, setup_observability
from microservices.common.schemas import HealthResponse, SearchRequest
from microservices.common.security import internal_headers, require_api_token
from microservices.query_service.parser import parse_query_fallback

app = FastAPI(title="Repo Search Search Service", version="0.1.0")
setup_observability(app, "search-service")

QUERY_SERVICE_URL = service_url("QUERY_SERVICE_URL", "http://query-service:8000")
EMBEDDING_SERVICE_URL = service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
CANDIDATE_MULTIPLIER = int(os.getenv("SEARCH_CANDIDATE_MULTIPLIER", "6"))
TOPIC_TITLE_BOOST = float(os.getenv("SEARCH_TOPIC_TITLE_BOOST", "0.04"))
TOPIC_ABSTRACT_BOOST = float(os.getenv("SEARCH_TOPIC_ABSTRACT_BOOST", "0.01"))
RANKING_PHRASE_BOOST = float(os.getenv("SEARCH_RANKING_PHRASE_BOOST", "0.02"))
QUERY_COVERAGE_BOOST = float(os.getenv("SEARCH_QUERY_COVERAGE_BOOST", "0.003"))


class PublicationEmbeddingRequest(BaseModel):
    embedding: list[float] | None = None


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def normalize_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None

    normalized = date_str.replace("Z", "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def ensure_schema() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

                ALTER TABLE publication
                    ADD COLUMN IF NOT EXISTS embedding vector(1024);

                DO $$
                BEGIN
                    IF to_regclass('public.publication_search') IS NOT NULL THEN
                        UPDATE publication p
                        SET embedding = ps.embedding
                        FROM publication_search ps
                        WHERE p.id = ps.id
                          AND p.embedding IS NULL
                          AND ps.embedding IS NOT NULL;

                        DROP TABLE publication_search;
                    END IF;
                END
                $$;

                CREATE INDEX IF NOT EXISTS idx_catalog_publication_embedding
                    ON publication USING ivfflat (embedding vector_cosine_ops);
                """
            )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        database = "ok"
    except Exception:
        database = "unavailable"
    finally:
        conn.close()

    return HealthResponse(status="ok", database=database)


async def parse_search_query(query: str) -> dict:
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            response = await observed_async_request(
                client,
                "POST",
                f"{QUERY_SERVICE_URL}/query/parse",
                service_name="search-service",
                upstream_service="query-service",
                json={"query": query},
                headers=internal_headers(),
            )
            raise_for_service(response, "Query service")
            return response.json()
        except Exception:
            return parse_query_fallback(query)


async def embed_query(query: str) -> list[float]:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await observed_async_request(
            client,
            "POST",
            f"{EMBEDDING_SERVICE_URL}/embed/query",
            service_name="search-service",
            upstream_service="embedding-service",
            json={"query": query},
            headers=internal_headers(),
        )
        raise_for_service(response, "Embedding service")
        return response.json()["embedding"]


def phrase_boost(title: str | None, abstract: str | None, phrases: list[str], title_boost: float, abstract_boost: float) -> float:
    boost = 0.0
    title_text = (title or "").lower()
    abstract_text = (abstract or "").lower()

    for phrase in phrases:
        normalized = phrase.lower()
        if normalized in title_text:
            boost += title_boost
        elif normalized in abstract_text:
            boost += abstract_boost

    return boost


def fetch_vector_results(query_vector: list[float], limit: int, year_from: int | None, year_to: int | None) -> list[tuple[Any, ...]]:
    sql = """
        WITH ranked AS (
            SELECT id, repository_id, title, abstract, source_url, date,
                   embedding <=> %s::vector AS cosine_distance
            FROM publication
            WHERE embedding IS NOT NULL
    """
    params: list[Any] = [query_vector]

    if year_from is not None:
        sql += " AND date >= %s"
        params.append(f"{year_from}-01-01")

    if year_to is not None:
        sql += " AND date <= %s"
        params.append(f"{year_to}-12-31")

    sql += """
            ORDER BY cosine_distance ASC
            LIMIT %s
        )
        SELECT ranked.id, ranked.title, ranked.abstract, ranked.source_url, ranked.date,
               ranked.cosine_distance, r.name,
               COALESCE(
                   ARRAY_AGG(a.full_name ORDER BY a.full_name)
                       FILTER (WHERE a.full_name IS NOT NULL),
                   '{}'
               ) AS authors
        FROM ranked
        LEFT JOIN repository r ON r.id = ranked.repository_id
        LEFT JOIN publication_author pa ON pa.publication_id = ranked.id
        LEFT JOIN author a ON a.id = pa.author_id
        GROUP BY ranked.id, ranked.title, ranked.abstract, ranked.source_url, ranked.date,
                 ranked.cosine_distance, r.name
        ORDER BY ranked.cosine_distance ASC
    """
    params.append(limit)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


@app.post("/search", dependencies=[Depends(require_api_token)])
async def search(request: SearchRequest) -> dict[str, Any]:
    with observe_search_request("search-service"):
        query = request.query.strip()
        parsed = await parse_search_query(query)
        embedding_queries = [item for item in parsed["embedding_queries"] if item.strip()]
        candidate_limit = max(request.limit, request.limit * CANDIDATE_MULTIPLIER)
        merged: dict[int, dict[str, Any]] = {}

        for embedding_query in embedding_queries:
            query_vector = await embed_query(embedding_query)
            rows = fetch_vector_results(query_vector, candidate_limit, parsed["year_from"], parsed["year_to"])

            for rank, row in enumerate(rows, start=1):
                publication_id = row[0]
                cosine_distance = float(row[5])
                cosine_similarity = 1 - cosine_distance
                existing = merged.get(publication_id)

                if existing is None:
                    merged[publication_id] = {
                        "id": row[0],
                        "title": row[1],
                        "abstract": row[2],
                        "source_url": row[3],
                        "date": row[4],
                        "cosine_distance": cosine_distance,
                        "cosine_similarity": cosine_similarity,
                        "repository": row[6],
                        "authors": list(row[7] or []),
                        "matched_query": embedding_query,
                        "matched_queries": {embedding_query},
                        "best_rank": rank,
                    }
                    continue

                existing["matched_queries"].add(embedding_query)
                if cosine_similarity > existing["cosine_similarity"]:
                    existing["cosine_distance"] = cosine_distance
                    existing["cosine_similarity"] = cosine_similarity
                    existing["matched_query"] = embedding_query
                    existing["best_rank"] = rank

        results = []
        for result in merged.values():
            topic_boost = phrase_boost(
                result["title"],
                result["abstract"],
                parsed.get("topic_phrases", []),
                TOPIC_TITLE_BOOST,
                TOPIC_ABSTRACT_BOOST,
            )
            ranking_boost = phrase_boost(
                result["title"],
                result["abstract"],
                parsed.get("ranking_phrases", []),
                RANKING_PHRASE_BOOST,
                RANKING_PHRASE_BOOST,
            )
            coverage_boost = min(len(result["matched_queries"]) * QUERY_COVERAGE_BOOST, 0.015)
            result["topic_boost"] = round(topic_boost, 6)
            result["ranking_boost"] = round(ranking_boost, 6)
            result["coverage_boost"] = round(coverage_boost, 6)
            result["score"] = round(result["cosine_similarity"] + topic_boost + ranking_boost + coverage_boost, 6)
            result["cosine_distance"] = round(result["cosine_distance"], 6)
            result["cosine_similarity"] = round(result["cosine_similarity"], 6)
            result["date"] = serialize_datetime(result["date"])
            result["matched_queries"] = sorted(result["matched_queries"])
            results.append(result)

        results.sort(key=lambda item: item["score"], reverse=True)
        results = results[:request.limit]

        return {
            "query": query,
            "limit": request.limit,
            "plan": parsed,
            "results": results,
            "total": len(results),
        }


@app.get("/embeddings/status", dependencies=[Depends(require_api_token)])
def embedding_status() -> dict[str, int]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE embedding IS NOT NULL),
                    COUNT(*) FILTER (WHERE embedding IS NULL)
                FROM publication
                """
            )
            indexed, with_embeddings, missing = cur.fetchone()
    finally:
        conn.close()

    return {
        "indexed_publications": indexed,
        "publications_with_embeddings": with_embeddings,
        "missing_embeddings": missing,
    }


@app.post("/publications/{publication_id}/embedding", dependencies=[Depends(require_api_token)])
def upsert_publication_embedding(publication_id: int, request: PublicationEmbeddingRequest) -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE publication
                SET embedding = %s
                WHERE id = %s
                """,
                (request.embedding, publication_id),
            )

            if cur.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publication was not found.")

        conn.commit()
        return {"status": "ok"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
