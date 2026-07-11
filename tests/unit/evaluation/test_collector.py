from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import httpx
import pytest

import evaluation.collector as collector_module
from evaluation.collector import (
    CollectorError,
    EvaluationServiceClient,
    RunCollectionError,
    ReadOnlyCorpusStore,
    collect_runs,
    run_collection,
    validate_methods,
    write_runs_atomically,
)
from evaluation.corpus_audit import build_snapshot
from evaluation.io import load_queries, load_runs
from evaluation.models import EvaluationQuery, QueryRun, RetrievedItem
from microservices.common.embedding_provenance import document_source_hash


SERBIAN_QUERIES = [
    "veštačka inteligencija u visokom obrazovanju",
    "примена вештачке интелигенције у образовању",
    "primena deep learning modela u obradi prirodnog jezika",
    "radovi o informacionim sistemima posle 2021. godine",
]


def _json_response(payload, status_code=200):
    return httpx.Response(
        status_code,
        content=json.dumps(payload, allow_nan=True).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


class FakeCorpusStore:
    def __init__(self):
        self.publications = [
            {"id": "2", "title": "shared term", "abstract": None, "source_url": "https://test/2"},
            {"id": "1", "title": "shared term", "abstract": None, "source_url": "https://test/1"},
            {"id": "9", "title": "First", "abstract": None, "source_url": "https://test/9"},
            {"id": "8", "title": "Second", "abstract": None, "source_url": None},
        ]
        self.vector_queries = []
        self.closed = False

    def fetch_vector_results(self, vector, limit, year_from, year_to):
        self.vector_queries.append((vector, limit, year_from, year_to))
        if vector[0] == 2.0:
            return []
        return [
            (2, "Vector two", None, "https://test/2", None, 0.1, "Repo", []),
            (1, "Vector one", None, "https://test/1", None, 0.1, "Repo", []),
        ][:limit]

    def close(self):
        self.closed = True


class FakeServiceClient:
    def __init__(self):
        self.embedded_queries = []
        self.pipeline_queries = []
        self.verified = False
        self.closed = False

    async def verify_model(self):
        self.verified = True

    async def embed_query(self, text):
        self.embedded_queries.append(text)
        marker = 2.0 if text == "no matches" else 1.0
        return [marker, *([0.0] * 1023)]

    async def full_pipeline_search(self, text, limit):
        self.pipeline_queries.append((text, limit))
        if text == "no matches":
            return {"plan": {"parser_mode": "fallback"}, "results": []}
        return {
            "plan": {"parser_mode": "llm"},
            "results": [
                {"id": 9, "score": 0.75, "title": "First", "source_url": "https://test/9"},
                {"id": 8, "score": 0.75, "title": "Second", "source_url": None},
            ][:limit],
        }

    async def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.session = None
        self.rolled_back = False
        self.closed = False

    def set_session(self, **kwargs):
        self.session = kwargs

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_collects_complete_matrix_with_zero_runs_scores_order_and_parser_mode() -> None:
    queries = [EvaluationQuery("q1", "shared term"), EvaluationQuery("q2", "no matches")]
    store = FakeCorpusStore()
    services = FakeServiceClient()

    runs = asyncio.run(
        collect_runs(
            queries,
            ["keyword", "vector_only", "full_pipeline"],
            10,
            corpus_store=store,
            service_client=services,
        )
    )

    assert [(run.query_id, run.method) for run in runs] == [
        (query.query_id, method)
        for query in queries
        for method in ("keyword", "vector_only", "full_pipeline")
    ]
    assert len(runs) == 6
    assert all(run.latency_ms is not None and math.isfinite(run.latency_ms) and run.latency_ms >= 0 for run in runs)
    keyword = runs[0]
    assert [item.publication_id for item in keyword.results] == ["1", "2"]
    assert [item.score for item in keyword.results] == [4.0, 4.0]
    vector = runs[1]
    assert [item.publication_id for item in vector.results] == ["2", "1"]
    assert [item.score for item in vector.results] == [0.9, 0.9]
    pipeline = runs[2]
    assert [item.publication_id for item in pipeline.results] == ["9", "8"]
    assert [item.score for item in pipeline.results] == [0.75, 0.75]
    assert pipeline.parser_mode == "llm"
    assert all(run.parser_mode is None for run in runs if run.method != "full_pipeline")
    assert runs[3].results == []
    assert runs[4].results == []
    assert runs[5].results == []
    assert store.vector_queries[0][2:] == (None, None)


def test_corpus_store_verifies_hash_size_provenance_and_readonly_session(monkeypatch) -> None:
    publication = {
        "id": 1,
        "repository_id": 2,
        "oai_identifier": "oai:test:1",
        "title": "Title",
        "abstract": "Abstract",
        "date": None,
        "source_url": "https://test/1",
        "authors": ["Author"],
        "has_embedding": True,
        "embedding_model": "model",
        "embedding_dimension": 1024,
        "embedding_generated_at": "2026-07-11T00:00:00+00:00",
        "embedding_source_hash": document_source_hash("Title", "Abstract"),
    }
    connection = FakeConnection()
    monkeypatch.setattr(collector_module, "_load_publications", lambda _connection: [publication])
    snapshot_hash = build_snapshot([publication])[1]

    store = ReadOnlyCorpusStore(
        lambda: connection,
        expected_corpus_size=1,
        expected_snapshot_hash=snapshot_hash,
        embedding_model="model",
    )
    assert connection.session == {"readonly": True, "isolation_level": "REPEATABLE READ"}
    store.close()
    assert connection.rolled_back and connection.closed


def test_corpus_store_closes_connection_when_verification_fails(monkeypatch) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(collector_module, "_load_publications", lambda _connection: [])
    with pytest.raises(CollectorError, match="corpus size mismatch"):
        ReadOnlyCorpusStore(
            lambda: connection,
            expected_corpus_size=1,
            expected_snapshot_hash="a" * 64,
            embedding_model="model",
        )
    assert connection.rolled_back and connection.closed


@pytest.mark.parametrize("query_text", SERBIAN_QUERIES)
def test_serbian_query_text_round_trips_exactly_through_json_and_services(tmp_path, query_text) -> None:
    query_path = tmp_path / "queries.json"
    query_path.write_text(
        json.dumps({"queries": [{"query_id": "q1", "text": query_text}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    query = load_queries(query_path)[0]
    services = FakeServiceClient()

    asyncio.run(
        collect_runs(
            [query],
            ["vector_only", "full_pipeline"],
            2,
            corpus_store=FakeCorpusStore(),
            service_client=services,
        )
    )

    assert query.text == query_text
    assert services.embedded_queries == [query_text]
    assert services.pipeline_queries == [(query_text, 2)]


def test_atomic_output_round_trip_has_contiguous_ranks_utf8_and_no_secrets(tmp_path) -> None:
    query = EvaluationQuery("q1", SERBIAN_QUERIES[0])
    runs = [
        QueryRun(
            "q1",
            "keyword",
            [RetrievedItem("10", 2.0, title="veštačka inteligencija"), RetrievedItem("11", 1.0)],
            1.25,
        )
    ]
    output = tmp_path / "runs.json"

    write_runs_atomically(output, runs, [query], ["keyword"])

    raw = output.read_text(encoding="utf-8")
    assert "veštačka inteligencija" in raw
    assert "sentinel-password" not in raw
    loaded = load_runs(output, {"q1"}, {"keyword"})
    assert [item.publication_id for item in loaded[0].results] == ["10", "11"]
    assert [row["rank"] for row in json.loads(raw)["runs"][0]["results"]] == [1, 2]
    assert not list(tmp_path.glob(".runs.json.*.tmp"))


@pytest.mark.parametrize(
    "runs",
    [
        [QueryRun("q1", "keyword", [RetrievedItem("1", 1), RetrievedItem("1", 0.5)], 1.0)],
        [QueryRun("q1", "keyword", [RetrievedItem("1", float("nan"))], 1.0)],
        [QueryRun("q1", "keyword", [], float("inf"))],
        [QueryRun("q1", "keyword", [], 1.0), QueryRun("q1", "keyword", [], 2.0)],
    ],
)
def test_invalid_runs_fail_before_output_is_written(tmp_path, runs) -> None:
    output = tmp_path / "runs.json"
    with pytest.raises(ValueError):
        write_runs_atomically(output, runs, [EvaluationQuery("q1", "query")], ["keyword"])
    assert not output.exists()
    assert not list(tmp_path.glob(".runs.json.*.tmp"))


def test_incomplete_matrix_and_unknown_method_fail_before_output(tmp_path) -> None:
    output = tmp_path / "runs.json"
    with pytest.raises(ValueError, match="incomplete comparison matrix"):
        write_runs_atomically(
            output,
            [QueryRun("q1", "keyword", [], 1.0)],
            [EvaluationQuery("q1", "query")],
            ["keyword", "vector_only"],
        )
    assert not output.exists()
    with pytest.raises(CollectorError, match="unknown evaluation methods"):
        validate_methods(["other"])
    with pytest.raises(CollectorError, match="unique"):
        validate_methods(["keyword", "keyword"])


def test_empty_queries_invalid_limits_and_oversized_pipeline_query_fail_early() -> None:
    for queries, methods, limit, message in (
        ([], ["keyword"], 5, "at least one query"),
        ([EvaluationQuery("q1", "query")], ["keyword"], 0, "between 1 and 50"),
        ([EvaluationQuery("q1", "query")], ["keyword"], 51, "between 1 and 50"),
        ([EvaluationQuery("q1", "x" * 1001)], ["full_pipeline"], 5, "1000"),
    ):
        with pytest.raises(CollectorError, match=message):
            asyncio.run(
                collect_runs(
                    queries,
                    methods,
                    limit,
                    corpus_store=FakeCorpusStore(),
                    service_client=FakeServiceClient(),
                )
            )


def test_existing_output_is_preserved_without_overwrite(tmp_path) -> None:
    output = tmp_path / "runs.json"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(CollectorError, match="already exists"):
        write_runs_atomically(
            output,
            [QueryRun("q1", "keyword", [], 1.0)],
            [EvaluationQuery("q1", "query")],
            ["keyword"],
        )
    assert output.read_text(encoding="utf-8") == "existing"


def test_atomic_replace_failure_preserves_existing_output_and_cleans_temp(tmp_path, monkeypatch) -> None:
    output = tmp_path / "runs.json"
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(collector_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("fail")))
    with pytest.raises(OSError, match="fail"):
        write_runs_atomically(
            output,
            [QueryRun("q1", "keyword", [], 1.0)],
            [EvaluationQuery("q1", "query")],
            ["keyword"],
            overwrite=True,
        )
    assert output.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".runs.json.*.tmp"))


def test_duplicate_vector_results_report_method_and_query_context() -> None:
    store = FakeCorpusStore()
    store.fetch_vector_results = lambda *_args: [
        (1, "One", None, None, None, 0.1, None, []),
        ("1", "Duplicate", None, None, None, 0.2, None, []),
    ]
    with pytest.raises(RunCollectionError) as error:
        asyncio.run(
            collect_runs(
                [EvaluationQuery("serbian-q", "query")],
                ["vector_only"],
                10,
                corpus_store=store,
                service_client=FakeServiceClient(),
            )
        )
    assert error.value.query_id == "serbian-q"
    assert error.value.method == "vector_only"
    assert "duplicate publication" in str(error.value)


def test_full_pipeline_accepts_known_frozen_publication_and_nullable_metadata() -> None:
    runs = asyncio.run(
        collect_runs(
            [EvaluationQuery("known-q", "query")],
            ["full_pipeline"],
            10,
            corpus_store=FakeCorpusStore(),
            service_client=FakeServiceClient(),
        )
    )

    assert [item.publication_id for item in runs[0].results] == ["9", "8"]
    assert runs[0].results[1].source_url is None


def test_unknown_full_pipeline_publication_reports_method_and_query_context() -> None:
    services = FakeServiceClient()

    async def unknown_result(_text, _limit):
        return {
            "plan": {"parser_mode": "llm"},
            "results": [{"id": 999, "score": 0.5, "title": "Other", "source_url": None}],
        }

    services.full_pipeline_search = unknown_result
    with pytest.raises(RunCollectionError) as error:
        asyncio.run(
            collect_runs(
                [EvaluationQuery("frozen-q", "query")],
                ["full_pipeline"],
                10,
                corpus_store=FakeCorpusStore(),
                service_client=services,
            )
        )

    assert error.value.query_id == "frozen-q"
    assert error.value.method == "full_pipeline"
    assert "outside the frozen corpus" in str(error.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", "Changed title"), ("source_url", "https://different.test/9")],
)
def test_full_pipeline_metadata_mismatch_prevents_output_and_is_contextual(
    tmp_path, monkeypatch, field, value
) -> None:
    output = tmp_path / "runs.json"
    store = FakeCorpusStore()
    services = FakeServiceClient()

    async def mismatched_result(_text, _limit):
        result = {"id": 9, "score": 0.5, "title": "First", "source_url": "https://test/9"}
        result[field] = value
        return {"plan": {"parser_mode": "llm"}, "results": [result]}

    services.full_pipeline_search = mismatched_result
    monkeypatch.setattr(collector_module, "ReadOnlyCorpusStore", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(collector_module, "verify_fresh_corpus", lambda *_args, **_kwargs: None)

    with pytest.raises(RunCollectionError) as error:
        asyncio.run(
            run_collection(
                queries=[EvaluationQuery("mismatch-q", "query")],
                methods=["full_pipeline"],
                limit=10,
                output_path=output,
                connection_factory=lambda: None,
                expected_corpus_size=len(store.publications),
                expected_snapshot_hash="a" * 64,
                embedding_model="model",
                service_client=services,
            )
        )

    assert error.value.query_id == "mismatch-q"
    assert error.value.method == "full_pipeline"
    assert f"metadata mismatch for publication ID '9': {field}" in str(error.value)
    assert not output.exists()
    assert store.closed and services.closed


def test_service_timeout_reports_method_and_query_context() -> None:
    services = FakeServiceClient()

    async def timeout(_text):
        raise CollectorError("query embedding request timed out")

    services.embed_query = timeout
    with pytest.raises(RunCollectionError) as error:
        asyncio.run(
            collect_runs(
                [EvaluationQuery("q-timeout", "query")],
                ["vector_only"],
                5,
                corpus_store=FakeCorpusStore(),
                service_client=services,
            )
        )
    assert error.value.method == "vector_only"
    assert error.value.query_id == "q-timeout"
    assert str(error.value) == (
        "collection failed for method='vector_only', query_id='q-timeout': query embedding request timed out"
    )


def test_http_client_preserves_utf8_and_sends_token_only_in_header() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/model/status"):
            return httpx.Response(200, json={"embedding_model": "model", "embedding_dimension": 1024})
        if request.url.path.endswith("/embed/query"):
            return httpx.Response(200, json={"embedding": [0.0] * 1024})
        return httpx.Response(200, json={"plan": {"parser_mode": "llm"}, "results": []})

    client = EvaluationServiceClient(
        embedding_service_url="http://embedding.test",
        full_pipeline_url="http://gateway.test/api/search",
        api_token="sentinel-token",
        timeout_seconds=5,
        expected_embedding_model="model",
        transport=httpx.MockTransport(handler),
    )

    async def execute():
        await client.verify_model()
        await client.embed_query(SERBIAN_QUERIES[1])
        await client.full_pipeline_search(SERBIAN_QUERIES[1], 5)
        await client.close()

    asyncio.run(execute())
    assert all(request.headers["X-API-Key"] == "sentinel-token" for request in requests)
    bodies = [json.loads(request.content.decode("utf-8")) for request in requests if request.content]
    assert bodies == [{"query": SERBIAN_QUERIES[1]}, {"query": SERBIAN_QUERIES[1], "limit": 5}]
    assert all(b"sentinel-token" not in request.content for request in requests)


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "malformed response"),
        ({"plan": {}, "results": None}, "malformed response"),
        ({"plan": None, "results": []}, "missing its query plan"),
        ({"plan": {"parser_mode": 1}, "results": []}, "invalid parser_mode"),
        ({"plan": {}, "results": [{"id": None, "score": 1.0}]}, "invalid publication ID"),
        ({"plan": {}, "results": [{"id": 1, "score": float("nan")}]}, "non-finite score"),
        ({"plan": {}, "results": [{"id": 1, "score": 1.0}, {"id": "1", "score": 0.5}]}, "duplicate"),
        ({"plan": {}, "results": [{"id": 1, "score": 1.0, "title": 7}]}, "invalid title"),
    ],
)
def test_malformed_full_pipeline_responses_are_rejected(payload, message) -> None:
    client = EvaluationServiceClient(
        embedding_service_url="http://embedding.test",
        full_pipeline_url="http://gateway.test/api/search",
        api_token="token",
        timeout_seconds=5,
        expected_embedding_model="model",
        transport=httpx.MockTransport(lambda _request: _json_response(payload)),
    )

    async def execute():
        try:
            with pytest.raises(CollectorError, match=message):
                await client.full_pipeline_search("query", 5)
        finally:
            await client.close()

    asyncio.run(execute())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"embedding": "not-a-list"},
        {"embedding": [0.0]},
        {"embedding": [float("inf")] * 1024},
        {"embedding": [True] * 1024},
    ],
)
def test_malformed_embedding_responses_are_rejected(payload) -> None:
    client = EvaluationServiceClient(
        embedding_service_url="http://embedding.test",
        full_pipeline_url="http://gateway.test/api/search",
        api_token="token",
        timeout_seconds=5,
        expected_embedding_model="model",
        transport=httpx.MockTransport(lambda _request: _json_response(payload)),
    )

    async def execute():
        try:
            with pytest.raises(CollectorError, match="malformed response|invalid vector"):
                await client.embed_query("query")
        finally:
            await client.close()

    asyncio.run(execute())


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (httpx.ReadTimeout("sentinel-token sentinel-password"), "timed out"),
        (httpx.ConnectError("sentinel-token sentinel-password"), "connection failed"),
    ],
)
def test_service_failures_are_sanitized(exception, message) -> None:
    def handler(request):
        exception.request = request
        raise exception

    client = EvaluationServiceClient(
        embedding_service_url="http://embedding.test",
        full_pipeline_url="http://gateway.test/api/search",
        api_token="sentinel-token",
        timeout_seconds=5,
        expected_embedding_model="model",
        transport=httpx.MockTransport(handler),
    )

    async def execute():
        try:
            with pytest.raises(CollectorError, match=message) as error:
                await client.embed_query("query")
            assert "sentinel-token" not in str(error.value)
            assert "sentinel-password" not in str(error.value)
        finally:
            await client.close()

    asyncio.run(execute())


def test_http_status_invalid_json_and_model_mismatch_are_sanitized() -> None:
    responses = [
        httpx.Response(503, text="sentinel-token sentinel-password"),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"embedding_model": "wrong", "embedding_dimension": 1024}),
    ]

    async def execute():
        for response, message in zip(responses, ("HTTP 503", "malformed JSON", "does not match")):
            client = EvaluationServiceClient(
                embedding_service_url="http://embedding.test",
                full_pipeline_url="http://gateway.test/api/search",
                api_token="sentinel-token",
                timeout_seconds=5,
                expected_embedding_model="model",
                transport=httpx.MockTransport(lambda _request, response=response: response),
            )
            try:
                with pytest.raises(CollectorError, match=message) as error:
                    await client.verify_model()
                assert "sentinel-token" not in str(error.value)
                assert "sentinel-password" not in str(error.value)
            finally:
                await client.close()

    asyncio.run(execute())


def test_embedding_dimension_mismatch_is_rejected_before_queries() -> None:
    client = EvaluationServiceClient(
        embedding_service_url="http://embedding.test",
        full_pipeline_url="http://gateway.test/api/search",
        api_token="token",
        timeout_seconds=5,
        expected_embedding_model="model",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"embedding_model": "model", "embedding_dimension": 2},
            )
        ),
    )

    async def execute():
        try:
            with pytest.raises(CollectorError, match="dimension"):
                await client.verify_model()
        finally:
            await client.close()

    asyncio.run(execute())


def test_database_failure_is_sanitized_closes_client_and_writes_nothing(tmp_path, monkeypatch) -> None:
    secret = "postgresql://user:sentinel-password@database/eval"
    services = FakeServiceClient()
    output = tmp_path / "runs.json"

    def fail_store(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(collector_module, "ReadOnlyCorpusStore", fail_store)
    with pytest.raises(CollectorError, match="database connection or corpus verification failed") as error:
        asyncio.run(
            run_collection(
                queries=[EvaluationQuery("q1", "query")],
                methods=["keyword"],
                limit=5,
                output_path=output,
                connection_factory=lambda: None,
                expected_corpus_size=1,
                expected_snapshot_hash="a" * 64,
                embedding_model="model",
                service_client=services,
            )
        )
    assert secret not in str(error.value)
    assert services.closed
    assert not output.exists()


def test_post_collection_corpus_failure_prevents_output(tmp_path, monkeypatch) -> None:
    output = tmp_path / "runs.json"
    services = FakeServiceClient()

    class Store(FakeCorpusStore):
        def close(self):
            pass

    monkeypatch.setattr(collector_module, "ReadOnlyCorpusStore", lambda *_args, **_kwargs: Store())
    monkeypatch.setattr(
        collector_module,
        "verify_fresh_corpus",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("postgresql://sentinel-password")),
    )
    with pytest.raises(CollectorError, match="post-collection corpus verification failed") as error:
        asyncio.run(
            run_collection(
                queries=[EvaluationQuery("q1", "shared term")],
                methods=["keyword"],
                limit=5,
                output_path=output,
                connection_factory=lambda: None,
                expected_corpus_size=2,
                expected_snapshot_hash="a" * 64,
                embedding_model="model",
                service_client=services,
            )
        )
    assert "sentinel-password" not in str(error.value)
    assert not output.exists()
