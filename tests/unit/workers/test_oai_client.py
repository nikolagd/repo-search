from __future__ import annotations

from dataclasses import dataclass

import pytest

from microservices.workers import oai_client


@dataclass
class FakeResponse:
    text: str
    error: Exception | None = None

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error


def test_list_metadata_formats_extracts_prefixes(
    monkeypatch,
    metadata_formats_xml_factory,
) -> None:
    calls = []

    def fake_request(params, base_url):
        calls.append((params, base_url))
        return metadata_formats_xml_factory(["oai_dc", "mods", "dim", "qdc"])

    monkeypatch.setattr(oai_client, "request_oai", fake_request)

    prefixes = oai_client.list_metadata_formats("https://repository.test/oai")

    assert prefixes == ["oai_dc", "mods", "dim", "qdc"]
    assert calls == [({"verb": "ListMetadataFormats"}, "https://repository.test/oai")]


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (["oai_dc"], "oai_dc"),
        (["oai_dc", "mods"], "mods"),
        (["oai_dc", "mods", "dim"], "dim"),
        (["mods", "dim", "qdc"], "qdc"),
    ],
)
def test_choose_metadata_prefix_uses_fixed_preference_order(
    monkeypatch,
    available: list[str],
    expected: str,
) -> None:
    monkeypatch.setattr(oai_client, "METADATA_PREFIX", "auto")
    monkeypatch.setattr(oai_client, "list_metadata_formats", lambda base_url: available)

    assert oai_client.choose_metadata_prefix("https://repository.test/oai") == expected


def test_choose_metadata_prefix_uses_explicit_configuration_without_discovery(monkeypatch) -> None:
    monkeypatch.setattr(oai_client, "METADATA_PREFIX", "  custom_format  ")

    def unexpected_discovery(base_url):
        raise AssertionError(f"unexpected metadata discovery for {base_url}")

    monkeypatch.setattr(oai_client, "list_metadata_formats", unexpected_discovery)

    assert oai_client.choose_metadata_prefix("https://repository.test/oai") == "custom_format"


def test_choose_metadata_prefix_rejects_repository_without_supported_format(monkeypatch) -> None:
    monkeypatch.setattr(oai_client, "METADATA_PREFIX", "auto")
    monkeypatch.setattr(oai_client, "list_metadata_formats", lambda base_url: ["marc21", "etdms"])

    with pytest.raises(oai_client.OAIClientError, match="does not expose any supported metadata format"):
        oai_client.choose_metadata_prefix("https://repository.test/oai")


def test_fetch_page_builds_initial_list_records_request(monkeypatch) -> None:
    captured = []

    def fake_request(params, base_url):
        captured.append((params, base_url))
        return "<response/>"

    monkeypatch.setattr(oai_client, "request_oai", fake_request)

    result = oai_client.fetch_page(
        from_date="2024-01-01",
        metadata_prefix="oai_dc",
        base_url="https://repository.test/oai",
    )

    assert result == "<response/>"
    assert captured == [
        (
            {"verb": "ListRecords", "metadataPrefix": "oai_dc", "from": "2024-01-01"},
            "https://repository.test/oai",
        )
    ]


def test_fetch_page_builds_token_only_continuation_request(monkeypatch) -> None:
    captured = []

    def fake_request(params, base_url):
        captured.append((params, base_url))
        return "<response/>"

    monkeypatch.setattr(oai_client, "request_oai", fake_request)

    oai_client.fetch_page(
        resumption_token="page-2",
        from_date="2024-01-01",
        metadata_prefix="oai_dc",
        base_url="https://repository.test/oai",
    )

    assert captured == [
        (
            {"verb": "ListRecords", "resumptionToken": "page-2"},
            "https://repository.test/oai",
        )
    ]


def test_fetch_page_requires_base_url() -> None:
    with pytest.raises(oai_client.OAIClientError, match="base URL is required"):
        oai_client.fetch_page(metadata_prefix="oai_dc")


def test_request_oai_rejects_malformed_xml(monkeypatch) -> None:
    monkeypatch.setattr(
        oai_client,
        "observed_sync_request",
        lambda *args, **kwargs: FakeResponse("<OAI-PMH>"),
    )

    with pytest.raises(oai_client.OAIClientError, match="Could not parse OAI response"):
        oai_client.request_oai({"verb": "Identify"}, "https://repository.test/oai")


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("noRecordsMatch", oai_client.OAINoRecordsMatch),
        ("badArgument", oai_client.OAIClientError),
    ],
)
def test_request_oai_maps_protocol_errors(monkeypatch, code: str, error_type: type[Exception]) -> None:
    xml_text = f"""
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <error code="{code}">Protocol failure</error>
    </OAI-PMH>
    """
    monkeypatch.setattr(
        oai_client,
        "observed_sync_request",
        lambda *args, **kwargs: FakeResponse(xml_text),
    )

    with pytest.raises(error_type, match=f"OAI error {code}: Protocol failure"):
        oai_client.request_oai({"verb": "ListRecords"}, "https://repository.test/oai")


def test_request_oai_wraps_http_errors(monkeypatch) -> None:
    response = FakeResponse("", oai_client.requests.HTTPError("503 Service Unavailable"))
    monkeypatch.setattr(oai_client, "observed_sync_request", lambda *args, **kwargs: response)

    with pytest.raises(oai_client.OAIClientError, match="OAI request failed: 503 Service Unavailable"):
        oai_client.request_oai({"verb": "Identify"}, "https://repository.test/oai")
