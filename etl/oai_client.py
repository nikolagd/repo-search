import os
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OAI_BASE_URL")
METADATA_PREFIX = os.getenv("OAI_METADATA_PREFIX", "auto")
PREFERRED_METADATA_PREFIXES = ["qdc", "dim", "mods", "oai_dc"]

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
}


class OAIClientError(RuntimeError):
    pass


#test samo sa prvom stranom, bez resumption tokena
#def fetch_first_page() -> str:
#    params = {
#        "verb": "ListRecords",
#        "metadataPrefix": METADATA_PREFIX,
#    }
#
#    response = requests.get(BASE_URL, params=params, timeout=60)
#    response.raise_for_status()
#    return response.text

def request_oai(params):
    try:
        response = requests.get(BASE_URL, params=params, timeout=60)
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
        raise OAIClientError(f"OAI error {code}: {message}")

    return response.text


def list_metadata_formats() -> list[str]:
    params = {
        "verb": "ListMetadataFormats",
    }

    root = ET.fromstring(request_oai(params))
    return [
        el.text.strip()
        for el in root.findall(".//oai:metadataPrefix", NS)
        if el.text
    ]


def choose_metadata_prefix() -> str:
    configured_prefix = (METADATA_PREFIX or "auto").strip()

    if configured_prefix and configured_prefix.lower() != "auto":
        return configured_prefix

    available_prefixes = list_metadata_formats()

    for prefix in PREFERRED_METADATA_PREFIXES:
        if prefix in available_prefixes:
            return prefix

    raise RuntimeError(
        "Repository does not expose any supported metadata format: "
        + ", ".join(PREFERRED_METADATA_PREFIXES)
    )


def fetch_page(resumption_token=None, from_date=None, metadata_prefix=None) -> str:
    if resumption_token:
        params = {
            "verb": "ListRecords",
            "resumptionToken": resumption_token,
        }
    else:
        params = {
            "verb": "ListRecords",
            "metadataPrefix": metadata_prefix or METADATA_PREFIX,
        }

        if from_date:
            params["from"] = from_date

    return request_oai(params)
