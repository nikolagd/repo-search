from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
from threading import Thread
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from microservices.catalog_service import main as catalog
from microservices.job_service import main as job_service
from microservices.workers import job_worker


def oai_page(identifier: str, title: str, author: str, *, token: str | None = None) -> str:
    token_xml = f"<resumptionToken>{token}</resumptionToken>" if token else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header><identifier>{identifier}</identifier></header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>{title}</dc:title>
          <dc:creator>{author}</dc:creator>
          <dc:date>2024-02-29</dc:date>
          <dc:description>{title} abstract</dc:description>
          <dc:identifier>https://example.test/{identifier.rsplit(':', 1)[-1]}</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    {token_xml}
  </ListRecords>
</OAI-PMH>"""


@pytest.fixture
def fake_oai_repository():
    requests_seen: list[dict[str, list[str]]] = []
    first_page = oai_page("oai:synthetic:1", "First title", "Alice Author", token="page-two")
    second_page = oai_page("oai:synthetic:2", "Second title", "Bob Author")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - HTTP handler API
            params = parse_qs(urlparse(self.path).query)
            requests_seen.append(params)
            verb = params.get("verb", [None])[0]
            if verb == "Identify":
                payload = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><Identify>
                    <repositoryName>Synthetic repository</repositoryName>
                    <baseURL>http://localhost/</baseURL>
                    <protocolVersion>2.0</protocolVersion>
                    <earliestDatestamp>2020-01-01</earliestDatestamp>
                    <deletedRecord>no</deletedRecord>
                    <granularity>YYYY-MM-DD</granularity>
                </Identify></OAI-PMH>"""
            elif verb == "ListMetadataFormats":
                payload = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListMetadataFormats>
                    <metadataFormat><metadataPrefix>oai_dc</metadataPrefix></metadataFormat>
                </ListMetadataFormats></OAI-PMH>"""
            elif params.get("resumptionToken") == ["page-two"]:
                payload = second_page
            elif verb == "ListRecords":
                payload = first_page
            else:
                self.send_error(400)
                return
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/oai", requests_seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def harvest_output_dir() -> Path:
    output_dir = Path.cwd() / ".codex-tmp" / f"harvest-test-{uuid4().hex}"
    output_dir.mkdir(parents=True)
    try:
        yield output_dir
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_two_page_worker_harvest_persists_catalog_and_completes_job_idempotently(
    pgvector_connection_factory: Callable[[], Any],
    fake_oai_repository,
    harvest_output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, requests_seen = fake_oai_repository
    monkeypatch.setattr(catalog, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(job_service, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(job_service, "refresh_job_metrics", lambda: None)
    monkeypatch.setattr(job_worker, "get_connection", pgvector_connection_factory)
    monkeypatch.setattr(job_worker, "OUTPUT_DIR", harvest_output_dir)
    catalog.ensure_schema()
    job_service.ensure_schema()

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO repository (name, oai_endpoint) VALUES (%s, %s) RETURNING id",
                ("Synthetic repository", endpoint),
            )
            repository_id = cursor.fetchone()[0]
        connection.commit()
    finally:
        connection.close()

    embedding_calls: list[dict[str, Any]] = []
    search_calls: list[int] = []

    def service_request(method: str, url: str, **kwargs):
        path = urlparse(url).path
        if method == "GET" and path == f"/repositories/{repository_id}":
            return catalog.repository(repository_id)
        if method == "POST" and path == "/publications":
            return catalog.upsert_publication(catalog.PublicationUpsertRequest(**kwargs["json"]))
        if method == "POST" and path == f"/repositories/{repository_id}/last-harvest":
            return catalog.update_last_harvest(repository_id)
        if method == "POST" and path == "/embed/document":
            embedding_calls.append(kwargs["json"])
            return {"embedding": [0.0], "embedding_model": "synthetic-model", "embedding_dimension": 1}
        if method == "POST" and path.startswith("/publications/") and path.endswith("/embedding"):
            search_calls.append(int(path.split("/")[2]))
            return {"status": "ok"}
        raise AssertionError(f"Unexpected service request: {method} {url}")

    monkeypatch.setattr(job_worker, "request_json", service_request)

    completed_job_ids = []
    for _ in range(2):
        connection = pgvector_connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO admin_job (job_type, repository_id, status, message)
                    VALUES ('repository_harvest', %s, 'queued', 'test harvest')
                    RETURNING id
                    """,
                    (repository_id,),
                )
                completed_job_ids.append(cursor.fetchone()[0])
            connection.commit()
        finally:
            connection.close()

        job = job_worker.claim_next_job()
        assert job is not None
        job_worker.run_job(job)

    connection = pgvector_connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM publication")
            assert cursor.fetchone()[0] == 2
            cursor.execute("SELECT COUNT(*) FROM author")
            assert cursor.fetchone()[0] == 2
            cursor.execute("SELECT COUNT(*) FROM publication_author")
            assert cursor.fetchone()[0] == 2
            cursor.execute("SELECT COUNT(*) FROM repository WHERE id = %s AND last_harvest IS NOT NULL", (repository_id,))
            assert cursor.fetchone()[0] == 1
            cursor.execute(
                "SELECT status, processed_records, lease_token FROM admin_job WHERE id = ANY(%s) ORDER BY id",
                (completed_job_ids,),
            )
            assert cursor.fetchall() == [("succeeded", 2, None), ("succeeded", 2, None)]
    finally:
        connection.close()

    continuation_requests = [params for params in requests_seen if params.get("resumptionToken") == ["page-two"]]
    assert len(continuation_requests) == 2
    assert len(embedding_calls) == 4
    assert len(search_calls) == 4
    assert len(set(search_calls)) == 2
