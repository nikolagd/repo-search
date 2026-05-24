import os
import xml.etree.ElementTree as ET

import requests

METADATA_PREFIX = os.getenv("OAI_METADATA_PREFIX", "auto")
PREFERRED_METADATA_PREFIXES = ["qdc", "dim", "mods", "oai_dc"]

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
}


class OAIClientError(RuntimeError):
    pass


class OAINoRecordsMatch(OAIClientError):
    pass


def request_oai(params, base_url):
    try:
        response = requests.get(base_url, params=params, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OAIClientError(f"OAI request failed: {exc}") from exc

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise OAIClientError(f"Could not parse OAI response: {exc}") from exc

    error_el = root.find(".//oai:error", NS)
    if error_el is not None:
        code = error_el.get("code", "unknown")
        message = error_el.text.strip() if error_el.text else "No error message"
        if code == "noRecordsMatch":
            raise OAINoRecordsMatch(f"OAI error {code}: {message}")
        raise OAIClientError(f"OAI error {code}: {message}")

    return response.text


def list_metadata_formats(base_url) -> list[str]:
    root = ET.fromstring(request_oai({"verb": "ListMetadataFormats"}, base_url=base_url))
    return [
        el.text.strip()
        for el in root.findall(".//oai:metadataPrefix", NS)
        if el.text
    ]


def identify_repository(base_url) -> dict[str, str | None]:
    root = ET.fromstring(request_oai({"verb": "Identify"}, base_url=base_url))
    identify = root.find(".//oai:Identify", NS)
    if identify is None:
        raise OAIClientError("OAI Identify response did not include an Identify element.")

    def get_text(tag: str) -> str | None:
        element = identify.find(f"oai:{tag}", NS)
        return element.text.strip() if element is not None and element.text else None

    return {
        "repository_name": get_text("repositoryName"),
        "base_url": get_text("baseURL"),
        "earliest_datestamp": get_text("earliestDatestamp"),
        "deleted_record": get_text("deletedRecord"),
        "granularity": get_text("granularity"),
    }


def get_granularity(base_url) -> str:
    return identify_repository(base_url=base_url).get("granularity") or "YYYY-MM-DD"


def choose_metadata_prefix(base_url) -> str:
    configured_prefix = (METADATA_PREFIX or "auto").strip()
    if configured_prefix and configured_prefix.lower() != "auto":
        return configured_prefix

    available_prefixes = list_metadata_formats(base_url=base_url)
    for prefix in PREFERRED_METADATA_PREFIXES:
        if prefix in available_prefixes:
            return prefix

    raise OAIClientError(
        "Repository does not expose any supported metadata format: "
        + ", ".join(PREFERRED_METADATA_PREFIXES)
    )


def fetch_page(resumption_token=None, from_date=None, metadata_prefix=None, base_url=None) -> str:
    if not base_url:
        raise OAIClientError("OAI base URL is required.")

    if resumption_token:
        params = {"verb": "ListRecords", "resumptionToken": resumption_token}
    else:
        params = {"verb": "ListRecords", "metadataPrefix": metadata_prefix or METADATA_PREFIX}
        if from_date:
            params["from"] = from_date

    return request_oai(params, base_url=base_url)
