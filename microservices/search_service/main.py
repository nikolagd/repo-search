from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os
from datetime import date, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator

from microservices.common.app_logging import app_observability_log_query_text, emit_app_event
from microservices.common.author_names import (
    AUTHOR_SEARCH_SCHEMA_SQL,
    author_name_key,
    canonicalize_author_name,
    rank_author_suggestions,
)
from microservices.common.config import service_url
from microservices.common.db import get_connection
from microservices.common.embedding_provenance import (
    DOCUMENT_TEMPLATE_VERSION,
    EXPECTED_EMBEDDING_DIMENSION,
    embedding_is_current,
    embedding_model_name,
    embedding_model_revision,
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
from microservices.common.schemas import (
    MAX_AUTHOR_FILTERS,
    AuthorMatch,
    HealthResponse,
    LivenessResponse,
    ReadinessResponse,
    SearchRequest,
)
from microservices.common.security import internal_headers, require_api_token
from microservices.query_service.parser import parse_query_fallback
from microservices.query_service.query_handler import derive_search_mode, sanitize_topic_text
from microservices.search_service.vector_search import execute_author_search, execute_vector_search

QUERY_SERVICE_URL = service_url("QUERY_SERVICE_URL", "http://query-service:8000")
EMBEDDING_SERVICE_URL = service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
CANDIDATE_MULTIPLIER = int(os.getenv("SEARCH_CANDIDATE_MULTIPLIER", "6"))
TOPIC_TITLE_BOOST = float(os.getenv("SEARCH_TOPIC_TITLE_BOOST", "0.04"))
TOPIC_ABSTRACT_BOOST = float(os.getenv("SEARCH_TOPIC_ABSTRACT_BOOST", "0.01"))
RANKING_PHRASE_BOOST = float(os.getenv("SEARCH_RANKING_PHRASE_BOOST", "0.02"))
QUERY_COVERAGE_BOOST = float(os.getenv("SEARCH_QUERY_COVERAGE_BOOST", "0.003"))
LOW_SCORE_THRESHOLD = float(os.getenv("APP_OBSERVABILITY_LOW_SCORE_THRESHOLD", "0.35"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    yield


app = FastAPI(title="Repo Search Search Service", version="0.1.0", lifespan=lifespan)
setup_observability(app, "search-service")


class PublicationEmbeddingRequest(BaseModel):
    embedding: list[float] | None = None
    embedding_model: str | None = Field(default=None, min_length=1)
    embedding_model_revision: str | None = Field(default=None, min_length=1)
    embedding_template_version: str | None = Field(default=None, min_length=1)
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
                    self.embedding_model_revision,
                    self.embedding_template_version,
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
                    ADD COLUMN IF NOT EXISTS embedding_model_revision TEXT,
                    ADD COLUMN IF NOT EXISTS embedding_template_version TEXT,
                    ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER,
                    ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS embedding_source_hash TEXT,
                    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

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
            cur.execute(AUTHOR_SEARCH_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


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


def merge_author_names(explicit: list[str], extracted: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in [*explicit, *(extracted or [])]:
        if not isinstance(candidate, str):
            continue
        name = " ".join(candidate.split())
        if not name or len(name) > 200:
            continue
        try:
            key = author_name_key(name)
        except ValueError:
            continue
        if key not in seen:
            seen.add(key)
            merged.append(name)
        if len(merged) == MAX_AUTHOR_FILTERS:
            break
    return merged


def finalize_search_plan(
    parsed: dict,
    explicit_authors: list[str],
    explicit_author_ids: list[int] | None = None,
    explicit_author_match: AuthorMatch | None = None,
) -> dict:
    plan = dict(parsed)
    validated_extracted = merge_author_names([], plan.get("author_names"))
    explicit_keys = {author_name_key(name) for name in explicit_authors}
    extracted_author_names = [
        name for name in validated_extracted if author_name_key(name) not in explicit_keys
    ]
    author_names = merge_author_names(explicit_authors, validated_extracted)
    embedding_queries = []
    for item in plan.get("embedding_queries") or []:
        if isinstance(item, str) and (clean := sanitize_topic_text(item, author_names)):
            if clean not in embedding_queries:
                embedding_queries.append(clean)
    plan["embedding_queries"] = embedding_queries
    plan["semantic_query"] = embedding_queries[0] if embedding_queries else ""
    plan["author_names"] = author_names
    plan["author_ids"] = list(explicit_author_ids or [])
    plan["extracted_author_names"] = extracted_author_names
    parser_author_match = plan.get("author_match")
    if parser_author_match not in {"any", "all"}:
        parser_author_match = "any"
    plan["author_match"] = explicit_author_match or parser_author_match
    plan["topic_phrases"] = [
        clean
        for item in plan.get("topic_phrases") or []
        if isinstance(item, str) and (clean := sanitize_topic_text(item, author_names))
    ]
    plan["ranking_phrases"] = [
        clean
        for item in plan.get("ranking_phrases") or []
        if isinstance(item, str) and (clean := sanitize_topic_text(item, author_names))
    ]
    plan["search_mode"] = derive_search_mode(
        embedding_queries,
        author_names or (["selected-author"] if explicit_author_ids else []),
    )
    return plan


def fetch_vector_results(
    query_vector: list[float],
    limit: int,
    year_from: int | None,
    year_to: int | None,
    author_names: list[str] | None = None,
    author_ids: list[int] | None = None,
    author_match: AuthorMatch = "any",
) -> list[tuple[Any, ...]]:
    with observe_retrieval_stage(
        "search-service",
        "vector_retrieval",
        {
            "repo_search.candidate_limit": limit,
            "repo_search.year_from": year_from,
            "repo_search.year_to": year_to,
            "repo_search.author_filter_count": len(author_names or []) + len(author_ids or []),
        },
    ) as span:
        conn = get_connection()
        try:
            rows = execute_vector_search(
                conn,
                query_vector,
                limit,
                year_from,
                year_to,
                author_names,
                author_ids,
                author_match,
            )
            if span is not None:
                span.set_attribute("repo_search.result_candidates", len(rows))
            return rows
        finally:
            conn.close()


def fetch_author_results(
    limit: int,
    year_from: int | None,
    year_to: int | None,
    author_names: list[str],
    author_ids: list[int] | None = None,
    author_match: AuthorMatch = "any",
) -> list[tuple[Any, ...]]:
    with observe_retrieval_stage(
        "search-service",
        "author_retrieval",
        {
            "repo_search.candidate_limit": limit,
            "repo_search.year_from": year_from,
            "repo_search.year_to": year_to,
            "repo_search.author_filter_count": len(author_names) + len(author_ids or []),
        },
    ) as span:
        conn = get_connection()
        try:
            rows = execute_author_search(
                conn, limit, year_from, year_to, author_names, author_ids, author_match
            )
            if span is not None:
                span.set_attribute("repo_search.result_candidates", len(rows))
            return rows
        finally:
            conn.close()


def fetch_author_suggestions(query: str, limit: int) -> list[dict[str, Any]]:
    canonical_query = canonicalize_author_name(query)
    if len(canonical_query.replace(" ", "")) < 2:
        raise HTTPException(status_code=422, detail="Author suggestion query must contain at least two letters.")
    longest_token = max(canonical_query.split(), key=len)
    candidate_limit = min(200, max(50, limit * 10))
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.id, a.full_name, COUNT(DISTINCT pa.publication_id) AS publication_count
                FROM author a
                JOIN publication_author pa ON pa.author_id = a.id
                JOIN publication p ON p.id = pa.publication_id AND p.is_active = TRUE
                WHERE a.search_name %% %s
                   OR a.search_name %% %s
                   OR a.search_name LIKE %s
                GROUP BY a.id, a.full_name, a.search_name
                ORDER BY GREATEST(
                    similarity(a.search_name, %s),
                    similarity(a.search_name, %s)
                ) DESC,
                lower(a.full_name) ASC,
                a.id ASC
                LIMIT %s
                """,
                (
                    canonical_query,
                    longest_token,
                    f"%{longest_token}%",
                    canonical_query,
                    longest_token,
                    candidate_limit,
                ),
            )
            candidates = [
                {
                    "id": row[0],
                    "display_name": row[1],
                    "publication_count": int(row[2]),
                }
                for row in cursor.fetchall()
            ]
    finally:
        connection.close()
    return rank_author_suggestions(canonical_query, candidates, limit)


@app.get("/authors/suggestions", dependencies=[Depends(require_api_token)])
def author_suggestions(
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=8, ge=1, le=20),
) -> dict[str, Any]:
    return {"suggestions": fetch_author_suggestions(q, limit)}


def load_index_status() -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.abstract, p.embedding IS NOT NULL,
                       p.embedding_model, p.embedding_model_revision,
                       p.embedding_template_version, p.embedding_dimension,
                       p.embedding_generated_at, p.embedding_source_hash,
                       COALESCE(r.name, 'unknown')
                FROM publication p
                LEFT JOIN repository r ON r.id = p.repository_id
                WHERE p.is_active = TRUE
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    model_name = embedding_model_name()
    model_revision = embedding_model_revision()
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
            "embedding_model_revision": row[4],
            "embedding_template_version": row[5],
            "embedding_dimension": row[6],
            "embedding_generated_at": row[7],
            "embedding_source_hash": row[8],
        }
        repository = repository_counts.setdefault(
            row[9],
            {
                "repository": row[9],
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
            model_revision=model_revision,
            template_version=DOCUMENT_TEMPLATE_VERSION,
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

            if query:
                with observe_retrieval_stage("search-service", "query_parse") as parse_span:
                    parsed = await parse_search_query(query)
                    parser_mode = normalize_parser_mode(
                        parsed.get("parser_mode"), bool(parsed.get("used_fallback"))
                    )
                    record_retrieval_parser_event("search-service", parser_mode)
                    if parse_span is not None:
                        parse_span.set_attribute("repo_search.parser_mode", parser_mode)
            else:
                parser_mode = "explicit"
                parsed = {
                    "embedding_queries": [],
                    "author_names": [],
                    "author_match": "any",
                    "topic_phrases": [],
                    "ranking_phrases": [],
                    "year_from": None,
                    "year_to": None,
                    "interpreted_query": (
                        f"Author filters: {', '.join(request.author_names)}"
                        if request.author_names
                        else f"Selected author IDs: {', '.join(map(str, request.author_ids))}"
                    ),
                    "used_fallback": False,
                    "parser_mode": parser_mode,
                }
                record_retrieval_parser_event("search-service", parser_mode)

            parsed = finalize_search_plan(
                parsed, request.author_names, request.author_ids, request.author_match
            )
            if not parsed["embedding_queries"] and not parsed["author_names"] and not parsed["author_ids"]:
                parsed = finalize_search_plan(
                    parse_query_fallback(query),
                    request.author_names,
                    request.author_ids,
                    request.author_match,
                )
                parser_mode = normalize_parser_mode(
                    parsed.get("parser_mode"), bool(parsed.get("used_fallback"))
                )

            embedding_queries = parsed["embedding_queries"]
            author_names = parsed["author_names"]
            author_ids = parsed["author_ids"]
            author_match = parsed["author_match"]
            search_mode = parsed["search_mode"]
            if span is not None:
                span.set_attribute("repo_search.embedding_query_count", len(embedding_queries))
                span.set_attribute("repo_search.search_mode", search_mode)
                span.set_attribute("repo_search.author_filter_count", len(author_names) + len(author_ids))
                span.set_attribute("repo_search.used_fallback", bool(parsed.get("used_fallback")))
                span.set_attribute("repo_search.parser_mode", parser_mode)
            candidate_limit = max(request.limit, request.limit * CANDIDATE_MULTIPLIER)
            merged: dict[int, dict[str, Any]] = {}
            vector_candidate_count = 0

            if search_mode == "author":
                author_args = (
                    request.limit,
                    parsed["year_from"],
                    parsed["year_to"],
                    author_names,
                )
                rows = fetch_author_results(*author_args, author_ids, author_match)
                for rank, row in enumerate(rows, start=1):
                    merged[row[0]] = {
                        "id": row[0],
                        "title": row[1],
                        "abstract": row[2],
                        "source_url": row[3],
                        "date": row[4],
                        "cosine_distance": None,
                        "cosine_similarity": None,
                        "repository": row[6],
                        "authors": list(row[7] or []),
                        "matched_query": None,
                        "matched_queries": [],
                        "best_rank": rank,
                        "topic_boost": 0.0,
                        "ranking_boost": 0.0,
                        "coverage_boost": 0.0,
                        "score": None,
                    }
            else:
                for embedding_query in embedding_queries:
                    with observe_retrieval_stage("search-service", "query_embedding"):
                        query_vector = await embed_query(embedding_query)
                    fetch_args = (
                        query_vector,
                        candidate_limit,
                        parsed["year_from"],
                        parsed["year_to"],
                    )
                    if author_ids or author_names:
                        rows = fetch_vector_results(
                            *fetch_args, author_names, author_ids, author_match
                        )
                    else:
                        rows = fetch_vector_results(*fetch_args)
                    vector_candidate_count += len(rows)

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
                    if search_mode != "author":
                        topic_boost = phrase_boost(
                            result["title"], result["abstract"], parsed.get("topic_phrases", []),
                            TOPIC_TITLE_BOOST, TOPIC_ABSTRACT_BOOST,
                        )
                        ranking_boost = phrase_boost(
                            result["title"], result["abstract"], parsed.get("ranking_phrases", []),
                            RANKING_PHRASE_BOOST, RANKING_PHRASE_BOOST,
                        )
                        coverage_boost = min(len(result["matched_queries"]) * QUERY_COVERAGE_BOOST, 0.015)
                        result["topic_boost"] = round(topic_boost, 6)
                        result["ranking_boost"] = round(ranking_boost, 6)
                        result["coverage_boost"] = round(coverage_boost, 6)
                        result["score"] = round(
                            result["cosine_similarity"] + topic_boost + ranking_boost + coverage_boost, 6
                        )
                        result["cosine_distance"] = round(result["cosine_distance"], 6)
                        result["cosine_similarity"] = round(result["cosine_similarity"], 6)
                    result["date"] = serialize_datetime(result["date"])
                    result["matched_queries"] = sorted(result["matched_queries"])
                    results.append(result)

                if search_mode != "author":
                    results.sort(key=lambda item: (-item["score"], item["id"]))
                results = results[:request.limit]

            result_scores = [float(result["score"]) for result in results if result["score"] is not None]
            record_retrieval_search(
                "search-service",
                parser_mode,
                len(embedding_queries),
                vector_candidate_count,
                result_scores,
                result_count=len(results),
                search_mode=search_mode,
                author_filter_count=len(author_names) + len(author_ids),
            )
            top_score = max(result_scores) if result_scores else None
            average_score = sum(result_scores) / len(result_scores) if result_scores else None
            log_fields = {
                "query_length": len(query),
                "parser_mode": parser_mode,
                "embedding_query_count": len(embedding_queries),
                "search_mode": search_mode,
                "author_filter_count": len(author_names) + len(author_ids),
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
            elif top_score is not None and top_score < LOW_SCORE_THRESHOLD:
                emit_app_event("retrieval.low_score", "search-service", **log_fields)

            if span is not None:
                span.set_attribute("repo_search.result_count", len(results))
                span.set_attribute("repo_search.vector_candidates", vector_candidate_count)
                if top_score is not None:
                    span.set_attribute("repo_search.top_score", results[0]["score"])

        return {
            "query": query,
            "limit": request.limit,
            "plan": parsed,
            "search_mode": search_mode,
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
                    embedding_model_revision = %s,
                    embedding_template_version = %s,
                    embedding_dimension = %s,
                    embedding_generated_at = %s,
                    embedding_source_hash = %s
                WHERE id = %s
                """,
                (
                    request.embedding,
                    request.embedding_model,
                    request.embedding_model_revision,
                    request.embedding_template_version,
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
