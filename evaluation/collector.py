from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from evaluation.adapters import BM25BaselineAdapter, FullPipelineAdapter, KeywordBaselineAdapter, VectorOnlyAdapter
from evaluation.corpus_audit import build_snapshot
from evaluation.io import load_runs, validate_comparison_matrix, write_json
from evaluation.models import EvaluationQuery, QueryRun
from microservices.common.embedding_provenance import (
    DEFAULT_EMBEDDING_MODEL_REVISION,
    DOCUMENT_TEMPLATE_VERSION,
    EXPECTED_EMBEDDING_DIMENSION,
    embedding_is_current,
)
from microservices.search_service.vector_search import execute_vector_search


SUPPORTED_METHODS = ("keyword", "bm25", "vector_only", "full_pipeline")
FINAL_METHODS = ("bm25", "vector_only", "full_pipeline")


class CollectorError(RuntimeError):
    pass


class RunCollectionError(CollectorError):
    def __init__(self, query_id: str, method: str, reason: str):
        self.query_id = query_id
        self.method = method
        self.reason = reason
        super().__init__(f"collection failed for method={method!r}, query_id={query_id!r}: {reason}")


def validate_methods(methods: Sequence[str]) -> list[str]:
    selected = list(methods)
    if not selected:
        raise CollectorError("at least one method is required")
    if len(selected) != len(set(selected)):
        raise CollectorError("methods must be unique")
    unknown = sorted(set(selected) - set(SUPPORTED_METHODS))
    if unknown:
        raise CollectorError(f"unknown evaluation methods: {unknown}")
    return selected


def _load_publications(connection: Any) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.repository_id, p.oai_identifier, p.title, p.abstract, p.date, p.source_url,
                   p.embedding IS NOT NULL, p.embedding_model, p.embedding_model_revision,
                   p.embedding_template_version, p.embedding_dimension,
                   p.embedding_generated_at, p.embedding_source_hash,
                   COALESCE(ARRAY_AGG(a.full_name ORDER BY a.full_name)
                       FILTER (WHERE a.full_name IS NOT NULL), '{}')
            FROM publication p
            LEFT JOIN publication_author pa ON pa.publication_id = p.id
            LEFT JOIN author a ON a.id = pa.author_id
            WHERE p.is_active = TRUE
            GROUP BY p.id
            ORDER BY p.id
            """
        )
        return [
            {
                "id": row[0],
                "repository_id": row[1],
                "oai_identifier": row[2],
                "title": row[3],
                "abstract": row[4],
                "date": row[5],
                "source_url": row[6],
                "has_embedding": row[7],
                "embedding_model": row[8],
                "embedding_model_revision": row[9],
                "embedding_template_version": row[10],
                "embedding_dimension": row[11],
                "embedding_generated_at": row[12],
                "embedding_source_hash": row[13],
                "authors": list(row[14] or []),
            }
            for row in cursor.fetchall()
        ]


class ReadOnlyCorpusStore:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        expected_corpus_size: int,
        expected_snapshot_hash: str,
        embedding_model: str,
        embedding_model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
        embedding_template_version: str = DOCUMENT_TEMPLATE_VERSION,
    ):
        self.connection = connection_factory()
        try:
            self.connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
            self.publications = _load_publications(self.connection)
            self.expected_corpus_size = expected_corpus_size
            self.expected_snapshot_hash = expected_snapshot_hash.lower()
            self.embedding_model = embedding_model
            self.embedding_model_revision = embedding_model_revision
            self.embedding_template_version = embedding_template_version
            if len(self.expected_snapshot_hash) != 64 or any(
                character not in "0123456789abcdef" for character in self.expected_snapshot_hash
            ):
                raise CollectorError("expected corpus snapshot hash must be 64 hexadecimal characters")
            self.verify()
        except Exception:
            try:
                self.connection.rollback()
            finally:
                self.connection.close()
            raise

    def verify(self) -> None:
        if len(self.publications) != self.expected_corpus_size:
            raise CollectorError(
                f"corpus size mismatch: expected {self.expected_corpus_size}, observed {len(self.publications)}"
            )
        snapshot_hash = build_snapshot(self.publications)[1]
        if snapshot_hash != self.expected_snapshot_hash:
            raise CollectorError(
                f"corpus snapshot hash mismatch: expected {self.expected_snapshot_hash}, observed {snapshot_hash}"
            )
        noncurrent = [
            str(publication["id"])
            for publication in self.publications
            if not embedding_is_current(
                publication,
                model_name=self.embedding_model,
                model_revision=self.embedding_model_revision,
                template_version=self.embedding_template_version,
                dimension=EXPECTED_EMBEDDING_DIMENSION,
            )
        ]
        if noncurrent:
            raise CollectorError(f"corpus contains {len(noncurrent)} missing or stale embeddings")

    def fetch_vector_results(
        self,
        query_vector: list[float],
        limit: int,
        year_from: int | None,
        year_to: int | None,
    ) -> list[tuple[Any, ...]]:
        if year_from is not None or year_to is not None:
            raise CollectorError("vector-only retrieval does not accept interpreted year constraints")
        return execute_vector_search(
            self.connection,
            query_vector,
            limit,
            None,
            None,
            deterministic_ties=True,
        )

    def close(self) -> None:
        try:
            self.connection.rollback()
        finally:
            self.connection.close()


def verify_fresh_corpus(
    connection_factory: Callable[[], Any],
    *,
    expected_corpus_size: int,
    expected_snapshot_hash: str,
    embedding_model: str,
    embedding_model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    embedding_template_version: str = DOCUMENT_TEMPLATE_VERSION,
) -> None:
    store = ReadOnlyCorpusStore(
        connection_factory,
        expected_corpus_size=expected_corpus_size,
        expected_snapshot_hash=expected_snapshot_hash,
        embedding_model=embedding_model,
        embedding_model_revision=embedding_model_revision,
        embedding_template_version=embedding_template_version,
    )
    store.close()


class EvaluationServiceClient:
    def __init__(
        self,
        *,
        embedding_service_url: str,
        full_pipeline_url: str,
        api_token: str,
        timeout_seconds: float,
        expected_embedding_model: str,
        expected_embedding_model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
        expected_embedding_template_version: str = DOCUMENT_TEMPLATE_VERSION,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.embedding_service_url = embedding_service_url.rstrip("/")
        self.full_pipeline_url = full_pipeline_url
        self.expected_embedding_model = expected_embedding_model
        self.expected_embedding_model_revision = expected_embedding_model_revision
        self.expected_embedding_template_version = expected_embedding_template_version
        self.client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"X-API-Key": api_token},
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _json_request(self, method: str, url: str, *, label: str, **kwargs: Any) -> Any:
        try:
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise CollectorError(f"{label} request timed out") from exc
        except httpx.RequestError as exc:
            raise CollectorError(f"{label} connection failed") from exc
        except httpx.HTTPStatusError as exc:
            raise CollectorError(f"{label} returned HTTP {exc.response.status_code}") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise CollectorError(f"{label} returned malformed JSON") from exc

    async def verify_model(self) -> None:
        payload = await self._json_request(
            "GET",
            f"{self.embedding_service_url}/model/status",
            label="embedding model status",
        )
        if not isinstance(payload, dict) or payload.get("embedding_model") != self.expected_embedding_model:
            raise CollectorError("embedding service model does not match the configured evaluation model")
        if payload.get("embedding_model_revision") != self.expected_embedding_model_revision:
            raise CollectorError("embedding service revision does not match the configured evaluation revision")
        if payload.get("embedding_template_version") != self.expected_embedding_template_version:
            raise CollectorError("embedding service template does not match the configured evaluation template")
        if payload.get("embedding_dimension") != EXPECTED_EMBEDDING_DIMENSION:
            raise CollectorError("embedding service dimension does not match the frozen vector dimension")

    async def embed_query(self, query: str) -> list[float]:
        payload = await self._json_request(
            "POST",
            f"{self.embedding_service_url}/embed/query",
            label="query embedding",
            json={"query": query},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("embedding"), list):
            raise CollectorError("query embedding returned a malformed response")
        vector = payload["embedding"]
        if len(vector) != EXPECTED_EMBEDDING_DIMENSION or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in vector
        ):
            raise CollectorError("query embedding returned an invalid vector")
        return [float(value) for value in vector]

    async def full_pipeline_search(self, query: str, limit: int) -> dict[str, Any]:
        payload = await self._json_request(
            "POST",
            self.full_pipeline_url,
            label="full pipeline",
            json={"query": query, "limit": limit},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise CollectorError("full pipeline returned a malformed response")
        plan = payload.get("plan")
        if not isinstance(plan, dict):
            raise CollectorError("full pipeline response is missing its query plan")
        parser_mode = plan.get("parser_mode")
        if parser_mode is not None and not isinstance(parser_mode, str):
            raise CollectorError("full pipeline returned an invalid parser_mode")
        identifiers = []
        for result in payload["results"]:
            if not isinstance(result, dict) or "id" not in result or "score" not in result:
                raise CollectorError("full pipeline returned a malformed result")
            if (
                isinstance(result["id"], bool)
                or not isinstance(result["id"], (str, int))
                or not str(result["id"])
            ):
                raise CollectorError("full pipeline returned an invalid publication ID")
            score = result["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise CollectorError("full pipeline returned a non-finite score")
            for field in ("title", "abstract", "source_url"):
                if field in result and result[field] is not None and not isinstance(result[field], str):
                    raise CollectorError(f"full pipeline returned an invalid {field}")
            identifiers.append(str(result["id"]))
        if len(identifiers) != len(set(identifiers)):
            raise CollectorError("full pipeline returned duplicate publication IDs")
        return payload


def validate_collected_run(
    run: QueryRun,
    *,
    expected_query_id: str,
    expected_method: str,
    frozen_publications: dict[str, dict[str, Any]],
) -> None:
    if run.query_id != expected_query_id or run.method != expected_method:
        raise CollectorError("adapter returned an unexpected query or method identity")
    if (
        run.latency_ms is None
        or isinstance(run.latency_ms, bool)
        or not isinstance(run.latency_ms, (int, float))
        or not math.isfinite(run.latency_ms)
        or run.latency_ms < 0
    ):
        raise CollectorError("adapter returned invalid latency")
    if expected_method != "full_pipeline" and run.parser_mode is not None:
        raise CollectorError("non-pipeline method returned parser_mode")
    if run.parser_mode is not None and (not isinstance(run.parser_mode, str) or not run.parser_mode):
        raise CollectorError("adapter returned invalid parser_mode")
    identifiers = [item.publication_id for item in run.results]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
        raise CollectorError("adapter returned an invalid publication ID")
    if len(identifiers) != len(set(identifiers)):
        raise CollectorError("adapter returned duplicate publication IDs")
    for item in run.results:
        frozen_publication = frozen_publications.get(item.publication_id)
        if frozen_publication is None:
            raise CollectorError(
                f"adapter returned publication ID {item.publication_id!r} outside the frozen corpus"
            )
        if isinstance(item.score, bool) or not isinstance(item.score, (int, float)) or not math.isfinite(item.score):
            raise CollectorError("adapter returned a non-finite score")
        for value in (item.title, item.abstract, item.source_url):
            if value is not None and not isinstance(value, str):
                raise CollectorError("adapter returned malformed publication metadata")
        if expected_method == "full_pipeline":
            for field in ("title", "source_url"):
                if getattr(item, field) != frozen_publication.get(field):
                    raise CollectorError(
                        f"full pipeline metadata mismatch for publication ID {item.publication_id!r}: {field}"
                    )


async def collect_runs(
    queries: list[EvaluationQuery],
    methods: Sequence[str],
    limit: int,
    *,
    corpus_store: ReadOnlyCorpusStore,
    service_client: EvaluationServiceClient,
) -> list[QueryRun]:
    selected_methods = validate_methods(methods)
    if not queries:
        raise CollectorError("at least one query is required")
    if limit <= 0 or limit > 50:
        raise CollectorError("limit must be between 1 and 50")
    if "full_pipeline" in selected_methods and any(len(query.text) > 1000 for query in queries):
        raise CollectorError("full-pipeline queries must not exceed 1000 characters")
    await service_client.verify_model()
    frozen_publications = {str(publication["id"]): publication for publication in corpus_store.publications}
    if len(frozen_publications) != len(corpus_store.publications):
        raise CollectorError("frozen corpus contains duplicate publication IDs")
    adapters = {}
    if "keyword" in selected_methods:
        adapters["keyword"] = KeywordBaselineAdapter(corpus_store.publications)
    if "bm25" in selected_methods:
        adapters["bm25"] = BM25BaselineAdapter(corpus_store.publications)
    if "vector_only" in selected_methods:
        adapters["vector_only"] = VectorOnlyAdapter(
            service_client.embed_query,
            corpus_store.fetch_vector_results,
        )
    if "full_pipeline" in selected_methods:
        adapters["full_pipeline"] = FullPipelineAdapter(service_client.full_pipeline_search)
    runs = []
    for query in queries:
        for method in selected_methods:
            try:
                run = await adapters[method].retrieve(query, limit)
                validate_collected_run(
                    run,
                    expected_query_id=query.query_id,
                    expected_method=method,
                    frozen_publications=frozen_publications,
                )
                runs.append(run)
            except Exception as exc:
                if isinstance(exc, RunCollectionError):
                    raise
                reason = str(exc) if isinstance(exc, CollectorError) else "unexpected adapter failure"
                raise RunCollectionError(query.query_id, method, reason) from exc
    validate_comparison_matrix(runs, {query.query_id for query in queries}, set(selected_methods))
    return runs


def write_runs_atomically(
    output_path: str | Path,
    runs: list[QueryRun],
    queries: list[EvaluationQuery],
    methods: Sequence[str],
    *,
    overwrite: bool = False,
) -> None:
    output = Path(output_path)
    if output.exists() and not overwrite:
        raise CollectorError(f"output already exists: {output.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_json(temporary, {"runs": [run.record() for run in runs]})
        query_ids = {query.query_id for query in queries}
        expected_methods = set(validate_methods(methods))
        validated = load_runs(temporary, query_ids, expected_methods)
        validate_comparison_matrix(validated, query_ids, expected_methods)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


async def run_collection(
    *,
    queries: list[EvaluationQuery],
    methods: Sequence[str],
    limit: int,
    output_path: str | Path,
    connection_factory: Callable[[], Any],
    expected_corpus_size: int,
    expected_snapshot_hash: str,
    embedding_model: str,
    embedding_model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    embedding_template_version: str = DOCUMENT_TEMPLATE_VERSION,
    service_client: EvaluationServiceClient,
    overwrite: bool = False,
) -> None:
    if expected_corpus_size <= 0:
        await service_client.close()
        raise CollectorError("expected corpus size must be positive")
    if Path(output_path).exists() and not overwrite:
        await service_client.close()
        raise CollectorError(f"output already exists: {Path(output_path).name}")
    store = None
    try:
        try:
            store = ReadOnlyCorpusStore(
                connection_factory,
                expected_corpus_size=expected_corpus_size,
                expected_snapshot_hash=expected_snapshot_hash,
                embedding_model=embedding_model,
                embedding_model_revision=embedding_model_revision,
                embedding_template_version=embedding_template_version,
            )
        except CollectorError:
            raise
        except Exception:
            raise CollectorError("database connection or corpus verification failed") from None
        runs = await collect_runs(queries, methods, limit, corpus_store=store, service_client=service_client)
    finally:
        try:
            if store is not None:
                try:
                    store.close()
                except Exception:
                    raise CollectorError("database cleanup failed") from None
        finally:
            try:
                await service_client.close()
            except Exception:
                raise CollectorError("service client cleanup failed") from None
    try:
        verify_fresh_corpus(
            connection_factory,
            expected_corpus_size=expected_corpus_size,
            expected_snapshot_hash=expected_snapshot_hash,
            embedding_model=embedding_model,
            embedding_model_revision=embedding_model_revision,
            embedding_template_version=embedding_template_version,
        )
    except CollectorError:
        raise
    except Exception:
        raise CollectorError("post-collection corpus verification failed") from None
    write_runs_atomically(output_path, runs, queries, methods, overwrite=overwrite)
