import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OAI_BASE_URL")
METADATA_PREFIX = os.getenv("OAI_METADATA_PREFIX", "oai_dc")

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

def fetch_page(resumption_token=None) -> str:
    if resumption_token:
        params = {
            "verb": "ListRecords",
            "resumptionToken": resumption_token,
        }
    else:
        params = {
            "verb": "ListRecords",
            "metadataPrefix": METADATA_PREFIX,
        }

    response = requests.get(BASE_URL, params=params, timeout=60)
    response.raise_for_status()
    return response.text