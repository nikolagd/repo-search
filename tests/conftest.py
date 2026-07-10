from __future__ import annotations

from html import escape
from pathlib import Path

import pytest


TESTS_ROOT = Path(__file__).parent
TOKEN_ABSENT = object()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply the suite marker from the first directory below tests/."""
    for item in items:
        try:
            suite = Path(str(item.path)).resolve().relative_to(TESTS_ROOT.resolve()).parts[0]
        except (IndexError, ValueError):
            continue

        if suite == "unit":
            item.add_marker(pytest.mark.unit)
        elif suite == "integration":
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def oai_envelope_factory():
    def build(
        metadata_xml: str = "",
        *,
        identifier: str | None = "oai:test:1",
        include_metadata: bool = True,
        deleted: bool = False,
        token: object = TOKEN_ABSENT,
    ) -> str:
        status = ' status="deleted"' if deleted else ""
        identifier_xml = f"<identifier>{escape(identifier)}</identifier>" if identifier is not None else ""
        metadata = f"<metadata>{metadata_xml}</metadata>" if include_metadata and not deleted else ""

        if token is TOKEN_ABSENT:
            token_xml = ""
        elif token is None:
            token_xml = "<resumptionToken/>"
        else:
            token_xml = f"<resumptionToken>{escape(str(token))}</resumptionToken>"

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header{status}>{identifier_xml}</header>
      {metadata}
    </record>
    {token_xml}
  </ListRecords>
</OAI-PMH>
"""

    return build


@pytest.fixture
def metadata_formats_xml_factory():
    def build(prefixes: list[str]) -> str:
        formats = "".join(
            f"<metadataFormat><metadataPrefix>{escape(prefix)}</metadataPrefix></metadataFormat>"
            for prefix in prefixes
        )
        return f"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListMetadataFormats>{formats}</ListMetadataFormats>
</OAI-PMH>"""

    return build


@pytest.fixture
def valid_query_plan() -> dict:
    return {
        "embedding_queries": ["information retrieval", "academic search"],
        "topic_phrases": ["information retrieval"],
        "year_from": 2018,
        "year_to": 2024,
        "ranking_phrases": ["open access"],
        "interpreted_query": "Academic information retrieval research",
    }
