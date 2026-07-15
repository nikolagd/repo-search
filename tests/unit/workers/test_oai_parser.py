from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from microservices.workers.parser import parse_oai_page, parse_oai_xml, pick_date_only, pick_valid_date


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


def metadata_with_dates(metadata_prefix: str, values: list[str]) -> str:
    if metadata_prefix == "oai_dc":
        dates = "".join(f"<dc:date>{value}</dc:date>" for value in values)
        return f"""<oai_dc:dc
            xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/">{dates}</oai_dc:dc>"""
    if metadata_prefix == "qdc":
        dates = "".join(f"<dcterms:issued>{value}</dcterms:issued>" for value in values)
        return f"""<qdc:qualifieddc
            xmlns:qdc="https://example.test/qdc"
            xmlns:dcterms="http://purl.org/dc/terms/">{dates}</qdc:qualifieddc>"""
    if metadata_prefix == "dim":
        dates = "".join(
            f'<dim:field element="date" qualifier="issued">{value}</dim:field>' for value in values
        )
        return f'<dim:dim xmlns:dim="http://www.dspace.org/xmlns/dspace/dim">{dates}</dim:dim>'
    if metadata_prefix == "mods":
        dates = "".join(f"<mods:dateIssued>{value}</mods:dateIssued>" for value in values)
        return f"""<mods:mods xmlns:mods="http://www.loc.gov/mods/v3">
            <mods:originInfo>{dates}</mods:originInfo>
        </mods:mods>"""
    raise AssertionError(f"Unsupported test metadata prefix: {metadata_prefix}")


@pytest.mark.parametrize(
    ("metadata_prefix", "metadata_xml", "expected"),
    [
        (
            "oai_dc",
            OAI_DC_METADATA,
            {
                "title": "OAI DC title",
                "authors": ["Alice Author", "Bob Author"],
                "date": "2024-03-05T12:00:00Z",
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
    xml_text = oai_envelope_factory(metadata_xml, identifier="oai:test:incomplete")

    records, token = parse_oai_xml(xml_text, "oai_dc")

    assert token is None
    assert records == [
        {
            "oai_identifier": "oai:test:incomplete",
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


def test_record_without_identifier_is_skipped_without_losing_valid_page_records() -> None:
    xml_text = f"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <header/>
          <metadata>{OAI_DC_METADATA}</metadata>
        </record>
        <record>
          <header><identifier>oai:test:valid</identifier></header>
          <metadata>{OAI_DC_METADATA}</metadata>
        </record>
        <resumptionToken>next-page</resumptionToken>
      </ListRecords>
    </OAI-PMH>"""

    records, token = parse_oai_xml(xml_text, "oai_dc")

    assert token == "next-page"
    assert len(records) == 1
    assert records[0]["oai_identifier"] == "oai:test:valid"
    assert records[0]["title"] == "OAI DC title"


def test_page_outcomes_count_actual_valid_deleted_missing_identifier_and_empty_metadata_records() -> None:
    xml_text = f"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <header><identifier>oai:test:valid</identifier></header>
          <metadata>{OAI_DC_METADATA}</metadata>
        </record>
        <record>
          <header status="deleted"><identifier>oai:test:deleted</identifier></header>
        </record>
        <record>
          <header/>
          <metadata>{OAI_DC_METADATA}</metadata>
        </record>
        <record>
          <header><identifier>oai:test:empty</identifier></header>
          <metadata>
            <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                       xmlns:dc="http://purl.org/dc/elements/1.1/"/>
          </metadata>
        </record>
        <resumptionToken completeListSize="999">next-page</resumptionToken>
      </ListRecords>
    </OAI-PMH>"""

    page = parse_oai_page(xml_text, "oai_dc")

    assert [record["oai_identifier"] for record in page.records] == ["oai:test:valid"]
    assert page.resumption_token == "next-page"
    assert page.received_records == 4
    assert page.parsed_records == 1
    assert page.skipped_records == 2
    assert page.deleted_records == 1


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
        (["2024-99-99", "2024-01-01T00:00:00Z"], "2024-01-01T00:00:00Z"),
        (["2023-02-29", "2024-02-29"], "2024-02-29"),
        (["2024-13", "2024-04-31", "2024-02-30T00:00:00Z"], None),
        ([], None),
    ],
)
def test_pick_valid_date_prefers_precision_and_rejects_invalid_calendar_values(
    dates: list[str],
    expected,
) -> None:
    assert pick_valid_date(dates) == expected


def test_pick_date_only_compatibility_wrapper_uses_calendar_validation() -> None:
    assert pick_date_only(["2024-99-99", "2024-02-29"]) == "2024-02-29"


@pytest.mark.parametrize("metadata_prefix", ["oai_dc", "qdc", "dim", "mods"])
@pytest.mark.parametrize(
    "date_value",
    [
        "2024",
        "2024-05",
        "2024-02-29",
        "2024-02-29T23:59:59Z",
        "2024-02-29T23:59:59.123456+02:00",
    ],
)
def test_supported_formats_preserve_valid_catalog_compatible_dates(
    oai_envelope_factory,
    metadata_prefix: str,
    date_value: str,
) -> None:
    metadata_xml = metadata_with_dates(metadata_prefix, [date_value])

    records, _ = parse_oai_xml(oai_envelope_factory(metadata_xml), metadata_prefix)

    assert records[0]["date"] == date_value


@pytest.mark.parametrize("metadata_prefix", ["oai_dc", "qdc", "dim", "mods"])
@pytest.mark.parametrize(
    ("invalid_value", "valid_value"),
    [
        ("2024-13", "2024-12"),
        ("2024-04-31", "2024-04-30"),
        ("2023-02-29", "2024-02-29"),
        ("2024-02-30T12:00:00Z", "2024-02-29T12:00:00Z"),
    ],
)
def test_supported_formats_skip_invalid_date_and_use_later_valid_candidate(
    oai_envelope_factory,
    metadata_prefix: str,
    invalid_value: str,
    valid_value: str,
) -> None:
    metadata_xml = metadata_with_dates(metadata_prefix, [invalid_value, valid_value])

    records, _ = parse_oai_xml(oai_envelope_factory(metadata_xml), metadata_prefix)

    assert records[0]["date"] == valid_value


def test_malformed_oai_xml_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        parse_oai_xml("<OAI-PMH><ListRecords>")
