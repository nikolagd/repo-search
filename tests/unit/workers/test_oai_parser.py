from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from microservices.workers.parser import parse_oai_xml, pick_date_only


OAI_DC_METADATA = """
<oai_dc:dc
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>OAI DC title</dc:title>
  <dc:creator>Alice Author</dc:creator>
  <dc:creator>Bob Author</dc:creator>
  <dc:date>2024-03-05T12:00:00Z</dc:date>
  <dc:date>2024-03</dc:date>
  <dc:description>OAI DC abstract</dc:description>
  <dc:identifier>doi:10.1000/example</dc:identifier>
  <dc:identifier>https://example.test/records/oai-dc</dc:identifier>
  <dc:subject>information retrieval</dc:subject>
  <dc:language>en</dc:language>
</oai_dc:dc>
"""

QDC_METADATA = """
<qdc:qualifieddc
    xmlns:qdc="https://example.test/qdc"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:title>QDC title</dc:title>
  <dcterms:creator>QDC Author</dcterms:creator>
  <dcterms:issued>2023-07-08T09:10:11Z</dcterms:issued>
  <dcterms:abstract>QDC abstract</dcterms:abstract>
  <dc:identifier>https://example.test/records/qdc</dc:identifier>
  <dc:subject>open access</dc:subject>
  <dc:language>sr</dc:language>
</qdc:qualifieddc>
"""

DIM_METADATA = """
<dim:dim xmlns:dim="http://www.dspace.org/xmlns/dspace/dim">
  <dim:field element="title">DIM title</dim:field>
  <dim:field element="contributor" qualifier="author">DIM Author</dim:field>
  <dim:field element="date" qualifier="issued">2022-06-07</dim:field>
  <dim:field element="description" qualifier="abstract">DIM abstract</dim:field>
  <dim:field element="identifier" qualifier="uri">https://example.test/records/dim</dim:field>
  <dim:field element="subject">repositories</dim:field>
  <dim:field element="language">en</dim:field>
</dim:dim>
"""

MODS_METADATA = """
<mods:mods xmlns:mods="http://www.loc.gov/mods/v3">
  <mods:titleInfo><mods:title>MODS title</mods:title></mods:titleInfo>
  <mods:name><mods:namePart>MODS Author</mods:namePart></mods:name>
  <mods:originInfo><mods:dateIssued>2021</mods:dateIssued></mods:originInfo>
  <mods:abstract>MODS abstract</mods:abstract>
  <mods:identifier>https://example.test/records/mods</mods:identifier>
  <mods:subject><mods:topic>metadata</mods:topic></mods:subject>
  <mods:language><mods:languageTerm>eng</mods:languageTerm></mods:language>
</mods:mods>
"""


@pytest.mark.parametrize(
    ("metadata_prefix", "metadata_xml", "expected"),
    [
        (
            "oai_dc",
            OAI_DC_METADATA,
            {
                "title": "OAI DC title",
                "authors": ["Alice Author", "Bob Author"],
                "date": "2024-03",
                "abstract": "OAI DC abstract",
                "source_url": "https://example.test/records/oai-dc",
                "subjects": ["information retrieval"],
                "languages": ["en"],
            },
        ),
        (
            "qdc",
            QDC_METADATA,
            {
                "title": "QDC title",
                "authors": ["QDC Author"],
                "date": "2023-07-08T09:10:11Z",
                "abstract": "QDC abstract",
                "source_url": "https://example.test/records/qdc",
                "subjects": ["open access"],
                "languages": ["sr"],
            },
        ),
        (
            "dim",
            DIM_METADATA,
            {
                "title": "DIM title",
                "authors": ["DIM Author"],
                "date": "2022-06-07",
                "abstract": "DIM abstract",
                "source_url": "https://example.test/records/dim",
                "subjects": ["repositories"],
                "languages": ["en"],
            },
        ),
        (
            "mods",
            MODS_METADATA,
            {
                "title": "MODS title",
                "authors": ["MODS Author"],
                "date": "2021",
                "abstract": "MODS abstract",
                "source_url": "https://example.test/records/mods",
                "subjects": ["metadata"],
                "languages": ["eng"],
            },
        ),
    ],
)
def test_parse_supported_metadata_formats(
    oai_envelope_factory,
    metadata_prefix: str,
    metadata_xml: str,
    expected: dict,
) -> None:
    xml_text = oai_envelope_factory(metadata_xml, identifier=f"oai:test:{metadata_prefix}")

    records, token = parse_oai_xml(xml_text, metadata_prefix)

    assert token is None
    assert len(records) == 1
    assert records[0]["oai_identifier"] == f"oai:test:{metadata_prefix}"
    for field, value in expected.items():
        assert records[0][field] == value


def test_parse_oai_dc_extracts_identifiers_and_resumption_token(oai_envelope_factory) -> None:
    xml_text = oai_envelope_factory(OAI_DC_METADATA, token="  next-page-token  ")

    records, token = parse_oai_xml(xml_text, "oai_dc")

    assert token == "next-page-token"
    assert records[0]["identifiers"] == [
        "doi:10.1000/example",
        "https://example.test/records/oai-dc",
    ]


def test_incomplete_metadata_keeps_a_stable_record_shape(oai_envelope_factory) -> None:
    metadata_xml = """
    <oai_dc:dc
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:contributor>Fallback Contributor</dc:contributor>
      <dc:date>not-a-date</dc:date>
      <dc:identifier>doi:10.1000/incomplete</dc:identifier>
    </oai_dc:dc>
    """
    xml_text = oai_envelope_factory(metadata_xml, identifier=None)

    records, token = parse_oai_xml(xml_text, "oai_dc")

    assert token is None
    assert records == [
        {
            "oai_identifier": None,
            "title": None,
            "authors": ["Fallback Contributor"],
            "date": None,
            "abstract": None,
            "identifiers": ["doi:10.1000/incomplete"],
            "subjects": [],
            "languages": [],
            "source_url": None,
        }
    ]


@pytest.mark.parametrize(
    "envelope_options",
    [
        {"include_metadata": False},
        {"deleted": True},
    ],
)
def test_record_without_metadata_is_skipped(oai_envelope_factory, envelope_options: dict) -> None:
    xml_text = oai_envelope_factory(OAI_DC_METADATA, **envelope_options)

    records, token = parse_oai_xml(xml_text, "oai_dc")

    assert records == []
    assert token is None


def test_empty_metadata_is_skipped(oai_envelope_factory) -> None:
    records, token = parse_oai_xml(oai_envelope_factory(), "oai_dc")

    assert records == []
    assert token is None


def test_metadata_for_a_different_prefix_is_skipped(oai_envelope_factory) -> None:
    records, token = parse_oai_xml(oai_envelope_factory(MODS_METADATA), "dim")

    assert records == []
    assert token is None


def test_absent_resumption_token_returns_none(oai_envelope_factory) -> None:
    _, token = parse_oai_xml(oai_envelope_factory(OAI_DC_METADATA), "oai_dc")

    assert token is None


@pytest.mark.parametrize(
    ("raw_token", "expected"),
    [
        (None, None),
        ("  page-2  ", "page-2"),
        ("   ", ""),
    ],
)
def test_resumption_token_text_is_extracted(oai_envelope_factory, raw_token, expected) -> None:
    xml_text = oai_envelope_factory(OAI_DC_METADATA, token=raw_token)

    _, token = parse_oai_xml(xml_text, "oai_dc")

    assert token == expected


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        (["2020", "2021-05", "2022-06-07"], "2022-06-07"),
        (["2020", "2021-05"], "2021-05"),
        (["2024-99-99", "2024-01-01T00:00:00Z"], "2024-99-99"),
        (["2024-01-01T00:00:00Z", "not-a-date"], None),
        ([], None),
    ],
)
def test_pick_date_only_prefers_the_most_precise_supported_shape(dates: list[str], expected) -> None:
    assert pick_date_only(dates) == expected


def test_malformed_oai_xml_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        parse_oai_xml("<OAI-PMH><ListRecords>")
