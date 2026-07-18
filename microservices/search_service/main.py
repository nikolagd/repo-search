from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

from microservices.common.app_logging import app_observability_log_query_text, emit_app_event
from microservices.common.config import service_url
from microservices.common.db import get_connection
from microservices.common.embedding_provenance import (
    EXPECTED_EMBEDDING_DIMENSION,
    embedding_is_current,
    embedding_model_name,
)
from microservices.common.health import (
    HEALTH_OK,
    HEALTH_UNAVAILABLE,
    build_health_response,
    build_liveness_response,
    build_readiness_response,
    check_database,
)
from microservices.common.http import observed_async_request, raise_for_service
from microservices.common.observability import (
    normalize_parser_mode,
    observe_retrieval_stage,
    observe_search_request,
    record_retrieval_parser_event,
    record_retrieval_search,
    set_retrieval_index_stats,
    set_retrieval_model_info,
    setup_observability,
)
from microservices.common.schemas import HealthResponse, LivenessResponse, ReadinessResponse, SearchRequest
from microservices.common.security import internal_headers, require_api_token
from microservices.query_service.parser import parse_query_fallback
from microservices.search_service.vector_search import execute_vector_search

app = FastAPI(title="Repo Search Search Service", version="0.1.0")
setup_observability(app, "search-service")

QUERY_SERVICE_URL = service_url("QUERY_SERVICE_URL", "http://query-service:8000")
EMBEDDING_SERVICE_URL = service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
CANDIDATE_MULTIPLIER = int(os.getenv("SEARCH_CANDIDATE_MULTIPLIER", "6"))
TOPIC_TITLE_BOOST = float(os.getenv("SEARCH_TOPIC_TITLE_BOOST", "0.04"))
TOPIC_ABSTRACT_BOOST = float(os.getenv("SEARCH_TOPIC_ABSTRACT_BOOST", "0.01"))
RANKING_PHRASE_BOOST = float(os.getenv("SEARCH_RANKING_PHRASE_BOOST", "0.02"))
QUERY_COVERAGE_BOOST = float(os.getenv("SEARCH_QUERY_COVERAGE_BOOST", "0.003"))
LOW_SCORE_THRESHOLD = float(os.getenv("APP_OBSERVABILITY_LOW_SCORE_THRESHOLD", "0.35"))


class PublicationEmbeddingRequest(BaseModel):
    embedding: list[float] | None = None
    embedding_model: str | None = Field(default=None, min_length=1)
    embedding_dimension: int | None = Field(default=None, gt=0)
    embedding_generated_at: datetime | None = None
    embedding_source_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_dimension(self) -> "PublicationEmbeddingRequest":
        if self.embedding is None:
            if any(
                value is not None
                for value in (
                    self.embedding_model,
                    self.embedding_dimension,
                    self.embedding_generated_at,
                    self.embedding_source_hash,
                )
            ):
                raise ValueError("embedding provenance cannot be stored without a vector")
            return self
        if self.embedding_dimension is not None and self.embedding_dimension != len(self.embedding):
            raise ValueError("embedding_dimension does not match the vector length")
        if len(self.embedding) != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError(f"embedding vector length must be {EXPECTED_EMBEDDING_DIMENSION}")
        return self


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
                    ADD COLUMN IF NOT EXISTS embedding vector(1024),
                    ADD COLUMN IF NOT EXISTS embedding_model TEXT,
                    ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER,
                    ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS embedding_source_hash TEXT;

                DO $$
                BEGIN
                    IF to_regclass('publication_search') IS NOT NULL THEN
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
    set_retrieval_model_info(
        "search-service",
        "ranking_config",
        {
            "candidate_multiplier": CANDIDATE_MULTIPLIER,
            "topic_title_boost": TOPIC_TITLE_BOOST,
            "topic_abstract_boost": TOPIC_ABSTRACT_BOOST,
            "ranking_phrase_boost": RANKING_PHRASE_BOOST,
            "query_coverage_boost": QUERY_COVERAGE_BOOST,
        },
    )
    app.state.collect_metrics = refresh_retrieval_index_metrics


async def embedding_readiness_status() -> str:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await observed_async_request(
                client,
                "GET",
                f"{EMBEDDING_SERVICE_URL}/ready",
                service_name="search-service",
                upstream_service="embedding-service",
                headers=internal_headers(),
            )
        return HEALTH_OK if response.status_code < 400 else HEALTH_UNAVAILABLE
    except Exception:
        return HEALTH_UNAVAILABLE


async def readiness_dependencies() -> dict[str, str]:
    return {
        "database": check_database(get_connection),
        "embedding-service": await embedding_readiness_status(),
    }


@app.get("/live", response_model=LivenessResponse)
def live() -> LivenessResponse:
    return build_liveness_response()


@app.get("/ready", response_model=ReadinessResponse, dependencies=[Depends(require_api_token)])
async def ready(response: Response) -> ReadinessResponse:
    return build_readiness_response(response, await readiness_dependencies())


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
async def health() -> HealthResponse:
    dependencies = await readiness_dependencies()
    return build_health_response(dependencies["database"], dependencies)


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
            plan = parse_query_fallback(query)
            plan["parser_mode"] = "fallback_service_error"
            return plan


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
    with observe_retrieval_stage(
        "search-service",
        "vector_retrieval",
        {
            "repo_search.candidate_limit": limit,
            "repo_search.year_from": year_from,
            "repo_search.year_to": year_to,
        },
    ) as span:
        conn = get_connection()
        try:
            rows = execute_vector_search(conn, query_vector, limit, year_from, year_to)
            if span is not None:
                span.set_attribute("repo_search.result_candidates", len(rows))
            return rows
        finally:
            conn.close()


def load_index_status() -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.abstract, p.embedding IS NOT NULL,
                       p.embedding_model, p.embedding_dimension,
                       p.embedding_generated_at, p.embedding_source_hash,
                       COALESCE(r.name, 'unknown')
                FROM publication p
                LEFT JOIN repository r ON r.id = p.repository_id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    model_name = embedding_model_name()
    indexed = len(rows)
    with_embeddings = 0
    missing = 0
    current = 0
    stale = 0
    repository_counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        publication = {
            "title": row[0],
            "abstract": row[1],
            "has_embedding": row[2],
            "embedding_model": row[3],
            "embedding_dimension": row[4],
            "embedding_generated_at": row[5],
            "embedding_source_hash": row[6],
        }
        repository = repository_counts.setdefault(
            row[7],
            {
                "repository": row[7],
                "publications": 0,
                "publications_with_embeddings": 0,
                "current_embeddings": 0,
                "missing_embeddings": 0,
                "stale_embeddings": 0,
            },
        )
        repository["publications"] += 1
        if not publication["has_embedding"]:
            missing += 1
            repository["missing_embeddings"] += 1
        elif embedding_is_current(
            publication,
            model_name=model_name,
            dimension=EXPECTED_EMBEDDING_DIMENSION,
        ):
            with_embeddings += 1
            current += 1
            repository["publications_with_embeddings"] += 1
            repository["current_embeddings"] += 1
        else:
            with_embeddings += 1
            stale += 1
            repository["publications_with_embeddings"] += 1
            repository["stale_embeddings"] += 1

    repositories = sorted(repository_counts.values(), key=lambda item: item["repository"])
    coverage = with_embeddings / indexed if indexed else 0
    return {
        "indexed_publications": indexed,
        "publications_with_embeddings": with_embeddings,
        "current_embeddings": current,
        "missing_embeddings": missing,
        "stale_embeddings": stale,
        "embedding_coverage_ratio": coverage,
        "repositories": repositories,
    }


def refresh_retrieval_index_metrics() -> None:
    status = load_index_status()
    set_retrieval_index_stats(
        "search-service",
        status["indexed_publications"],
        status["publications_with_embeddings"],
        status["missing_embeddings"],
        status["repositories"],
    )


@app.post("/search", dependencies=[Depends(require_api_token)])
async def search(request: SearchRequest) -> dict[str, Any]:
    with observe_search_request("search-service") as span:
        with observe_retrieval_stage("search-service", "total"):
            query = request.query.strip()
            if span is not None:
                span.set_attribute("repo_search.query_length", len(query))
                span.set_attribute("repo_search.limit", request.limit)

            with observe_retrieval_stage("search-service", "query_parse") as parse_span:
                parsed = await parse_search_query(query)
                parser_mode = normalize_parser_mode(parsed.get("parser_mode"), bool(parsed.get("used_fallback")))
                record_retrieval_parser_event("search-service", parser_mode)
                if parse_span is not None:
                    parse_span.set_attribute("repo_search.parser_mode", parser_mode)

            embedding_queries = [item for item in parsed["embedding_queries"] if item.strip()]
            if span is not None:
                span.set_attribute("repo_search.embedding_query_count", len(embedding_queries))
                span.set_attribute("repo_search.used_fallback", bool(parsed.get("used_fallback")))
                span.set_attribute("repo_search.parser_mode", parser_mode)
            candidate_limit = max(request.limit, request.limit * CANDIDATE_MULTIPLIER)
            merged: dict[int, dict[str, Any]] = {}
            vector_candidate_count = 0

            for embedding_query in embedding_queries:
                with observe_retrieval_stage("search-service", "query_embedding"):
                    query_vector = await embed_query(embedding_query)
                rows = fetch_vector_results(query_vector, candidate_limit, parsed["year_from"], parsed["year_to"])
                vector_candidate_count += len(rows)

                with observe_retrieval_stage("search-service", "candidate_merge"):
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

            with observe_retrieval_stage("search-service", "ranking"):
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

            result_scores = [float(result["score"]) for result in results]
            record_retrieval_search(
                "search-service",
                parser_mode,
                len(embedding_queries),
                vector_candidate_count,
                result_scores,
            )
            top_score = max(result_scores) if result_scores else 0
            average_score = sum(result_scores) / len(result_scores) if result_scores else 0
            log_fields = {
                "query_length": len(query),
                "parser_mode": parser_mode,
                "embedding_query_count": len(embedding_queries),
                "vector_candidate_count": vector_candidate_count,
                "result_count": len(results),
                "top_score": top_score,
                "average_score": average_score,
            }
            if app_observability_log_query_text():
                log_fields["query"] = query
            emit_app_event("search.completed", "search-service", **log_fields)
            if not results:
                emit_app_event("search.zero_results", "search-service", **log_fields)
            elif top_score < LOW_SCORE_THRESHOLD:
                emit_app_event("retrieval.low_score", "search-service", **log_fields)

            if span is not None:
                span.set_attribute("repo_search.result_count", len(results))
                span.set_attribute("repo_search.vector_candidates", vector_candidate_count)
                if results:
                    span.set_attribute("repo_search.top_score", results[0]["score"])

        return {
            "query": query,
            "limit": request.limit,
            "plan": parsed,
            "results": results,
            "total": len(results),
        }


@app.get("/embeddings/status", dependencies=[Depends(require_api_token)])
def embedding_status() -> dict[str, int]:
    status = load_index_status()

    return {
        "indexed_publications": status["indexed_publications"],
        "publications_with_embeddings": status["publications_with_embeddings"],
        "current_embeddings": status["current_embeddings"],
        "missing_embeddings": status["missing_embeddings"],
        "stale_embeddings": status["stale_embeddings"],
    }


@app.get("/model-observability/status", dependencies=[Depends(require_api_token)])
def model_observability_status() -> dict[str, Any]:
    index_status = load_index_status()
    return {
        "ranking_config": {
            "candidate_multiplier": CANDIDATE_MULTIPLIER,
            "topic_title_boost": TOPIC_TITLE_BOOST,
            "topic_abstract_boost": TOPIC_ABSTRACT_BOOST,
            "ranking_phrase_boost": RANKING_PHRASE_BOOST,
            "query_coverage_boost": QUERY_COVERAGE_BOOST,
        },
        "index": index_status,
    }


@app.post("/publications/{publication_id}/embedding", dependencies=[Depends(require_api_token)])
def upsert_publication_embedding(publication_id: int, request: PublicationEmbeddingRequest) -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE publication
                SET embedding = %s,
                    embedding_model = %s,
                    embedding_dimension = %s,
                    embedding_generated_at = %s,
                    embedding_source_hash = %s
                WHERE id = %s
                """,
                (
                    request.embedding,
                    request.embedding_model,
                    request.embedding_dimension,
                    request.embedding_generated_at,
                    request.embedding_source_hash,
                    publication_id,
                ),
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
