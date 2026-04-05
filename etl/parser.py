import xml.etree.ElementTree as ET


NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}


def get_texts(parent, path):
    elements = parent.findall(path, NS)
    return [el.text.strip() for el in elements if el is not None and el.text]


def pick_source_url(identifiers):
    for value in identifiers:
        if value.startswith("https://rfos.fon.bg.ac.rs/handle/"):
            return value
    for value in identifiers:
        if value.startswith("http://") or value.startswith("https://"):
            return value
    return None


def parse_oai_xml(xml_text: str):
    root = ET.fromstring(xml_text)

    parsed_records = []

    for record in root.findall(".//oai:record", NS):
        metadata = record.find("oai:metadata", NS)
        if metadata is None:
            continue

        dc_node = metadata.find("oai_dc:dc", NS)
        if dc_node is None:
            continue

        titles = get_texts(dc_node, "dc:title")
        creators = get_texts(dc_node, "dc:creator")
        dates = get_texts(dc_node, "dc:date")
        descriptions = get_texts(dc_node, "dc:description")
        identifiers = get_texts(dc_node, "dc:identifier")
        subjects = get_texts(dc_node, "dc:subject")
        languages = get_texts(dc_node, "dc:language")

        header = record.find("oai:header", NS)
        oai_identifier = None
        if header is not None:
            identifier_el = header.find("oai:identifier", NS)
            if identifier_el is not None and identifier_el.text:
                oai_identifier = identifier_el.text.strip()

        parsed_records.append(
            {
                "oai_identifier": oai_identifier,
                "title": titles[0] if titles else None,
                "authors": creators,
                "date": dates[0] if dates else None,
                "abstract": descriptions[0] if descriptions else None,
                "identifiers": identifiers,
                "subjects": subjects,
                "languages": languages,
                "source_url": pick_source_url(identifiers),
            }
        )

    token_el = root.find(".//oai:resumptionToken", NS)
    resumption_token = None
    if token_el is not None and token_el.text:
        resumption_token = token_el.text.strip()

    return parsed_records, resumption_token