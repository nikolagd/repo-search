import re
import xml.etree.ElementTree as ET
from datetime import datetime

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dim": "http://www.dspace.org/xmlns/dspace/dim",
    "dcterms": "http://purl.org/dc/terms/",
    "mods": "http://www.loc.gov/mods/v3",
}

DATE_VALUE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}[T ].+$"), None),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{4}-\d{2}$"), "%Y-%m"),
    (re.compile(r"^\d{4}$"), "%Y"),
]


def get_texts(parent, path):
    return [el.text.strip() for el in parent.findall(path, NS) if el is not None and el.text]


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def get_direct_texts_by_name(parent, names):
    values = []
    for element in list(parent):
        if local_name(element.tag) in names and element.text:
            values.append(element.text.strip())
    return values


def pick_valid_date(dates):
    for pattern, date_format in DATE_VALUE_PATTERNS:
        for raw_value in dates:
            value = raw_value.strip()
            if not pattern.match(value):
                continue

            try:
                if date_format is None:
                    iso_value = value[:-1] + "+00:00" if value.endswith("Z") else value
                    datetime.fromisoformat(iso_value)
                else:
                    datetime.strptime(value, date_format)
            except ValueError:
                continue

            return value
    return None


def pick_date_only(dates):
    return pick_valid_date(dates)


def pick_source_url(identifiers):
    for value in identifiers:
        if value.startswith("https://rfos.fon.bg.ac.rs/handle/"):
            return value
    for value in identifiers:
        if value.startswith("http://") or value.startswith("https://"):
            return value
    return None


def get_oai_identifier(record):
    header = record.find("oai:header", NS)
    if header is None:
        return None
    identifier_el = header.find("oai:identifier", NS)
    return identifier_el.text.strip() if identifier_el is not None and identifier_el.text else None


def build_record(oai_identifier, title, authors, date, abstract, identifiers, subjects, languages):
    return {
        "oai_identifier": oai_identifier,
        "title": title,
        "authors": authors,
        "date": date,
        "abstract": abstract,
        "identifiers": identifiers,
        "subjects": subjects,
        "languages": languages,
        "source_url": pick_source_url(identifiers),
    }


def parse_oai_dc_metadata(dc_node, oai_identifier):
    creators = get_texts(dc_node, "dc:creator")
    contributors = get_texts(dc_node, "dc:contributor")
    dates = get_texts(dc_node, "dc:date")
    descriptions = get_texts(dc_node, "dc:description")
    identifiers = get_texts(dc_node, "dc:identifier")
    return build_record(
        oai_identifier=oai_identifier,
        title=(get_texts(dc_node, "dc:title") or [None])[0],
        authors=creators if creators else contributors,
        date=pick_valid_date(dates),
        abstract=descriptions[0] if descriptions else None,
        identifiers=identifiers,
        subjects=get_texts(dc_node, "dc:subject"),
        languages=get_texts(dc_node, "dc:language"),
    )


def parse_qdc_metadata(qdc_node, oai_identifier):
    creators = get_direct_texts_by_name(qdc_node, {"creator"})
    contributors = get_direct_texts_by_name(qdc_node, {"contributor"})
    issued_dates = get_direct_texts_by_name(qdc_node, {"issued"})
    dates = get_direct_texts_by_name(qdc_node, {"date"})
    descriptions = get_direct_texts_by_name(qdc_node, {"abstract", "description"})
    identifiers = get_direct_texts_by_name(qdc_node, {"identifier"})
    titles = get_direct_texts_by_name(qdc_node, {"title"})
    return build_record(
        oai_identifier=oai_identifier,
        title=titles[0] if titles else None,
        authors=creators if creators else contributors,
        date=pick_valid_date(issued_dates) or pick_valid_date(dates),
        abstract=descriptions[0] if descriptions else None,
        identifiers=identifiers,
        subjects=get_direct_texts_by_name(qdc_node, {"subject"}),
        languages=get_direct_texts_by_name(qdc_node, {"language"}),
    )


def dim_field_texts(dim_node, element, qualifier=None):
    values = []
    for field in dim_node.findall("dim:field", NS):
        if field.get("element") != element:
            continue
        if qualifier is not None and field.get("qualifier") != qualifier:
            continue
        if field.text:
            values.append(field.text.strip())
    return values


def parse_dim_metadata(dim_node, oai_identifier):
    creators = dim_field_texts(dim_node, "contributor", "author")
    contributors = dim_field_texts(dim_node, "contributor")
    issued_dates = dim_field_texts(dim_node, "date", "issued")
    dates = dim_field_texts(dim_node, "date")
    descriptions = dim_field_texts(dim_node, "description", "abstract")
    identifiers = dim_field_texts(dim_node, "identifier", "uri") + dim_field_texts(dim_node, "identifier")
    titles = dim_field_texts(dim_node, "title")
    return build_record(
        oai_identifier=oai_identifier,
        title=titles[0] if titles else None,
        authors=creators if creators else contributors,
        date=pick_valid_date(issued_dates) or pick_valid_date(dates),
        abstract=descriptions[0] if descriptions else None,
        identifiers=identifiers,
        subjects=dim_field_texts(dim_node, "subject"),
        languages=dim_field_texts(dim_node, "language"),
    )


def parse_mods_metadata(mods_node, oai_identifier):
    issued_dates = get_texts(mods_node, ".//mods:originInfo/mods:dateIssued")
    dates = get_texts(mods_node, ".//mods:originInfo/*")
    identifiers = get_texts(mods_node, ".//mods:identifier")
    titles = get_texts(mods_node, ".//mods:titleInfo/mods:title")
    abstracts = get_texts(mods_node, ".//mods:abstract")
    return build_record(
        oai_identifier=oai_identifier,
        title=titles[0] if titles else None,
        authors=get_texts(mods_node, ".//mods:name/mods:namePart"),
        date=pick_valid_date(issued_dates) or pick_valid_date(dates),
        abstract=abstracts[0] if abstracts else None,
        identifiers=identifiers,
        subjects=get_texts(mods_node, ".//mods:subject/mods:topic"),
        languages=get_texts(mods_node, ".//mods:languageTerm"),
    )


def parse_metadata(metadata, metadata_prefix, oai_identifier):
    if metadata_prefix == "qdc":
        qdc_node = next(iter(metadata), None)
        if qdc_node is not None:
            return parse_qdc_metadata(qdc_node, oai_identifier)
    if metadata_prefix == "dim":
        dim_node = metadata.find("dim:dim", NS)
        if dim_node is not None:
            return parse_dim_metadata(dim_node, oai_identifier)
    if metadata_prefix == "mods":
        mods_node = metadata.find("mods:mods", NS)
        if mods_node is not None:
            return parse_mods_metadata(mods_node, oai_identifier)

    dc_node = metadata.find("oai_dc:dc", NS)
    return parse_oai_dc_metadata(dc_node, oai_identifier) if dc_node is not None else None


def parse_oai_xml(xml_text: str, metadata_prefix="oai_dc"):
    root = ET.fromstring(xml_text)
    parsed_records = []

    for record in root.findall(".//oai:record", NS):
        metadata = record.find("oai:metadata", NS)
        if metadata is None:
            continue

        oai_identifier = get_oai_identifier(record)
        if not oai_identifier:
            continue

        parsed_record = parse_metadata(metadata, metadata_prefix, oai_identifier)
        if parsed_record is not None:
            parsed_records.append(parsed_record)

    token_el = root.find(".//oai:resumptionToken", NS)
    token = token_el.text.strip() if token_el is not None and token_el.text else None
    return parsed_records, token
