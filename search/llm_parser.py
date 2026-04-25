import requests
import json
import re

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.1:8b"


def extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def parse_query_llm(query: str) -> dict:
    prompt = f"""
You are a strict JSON generator.

Extract search parameters from the query.

Return ONLY valid JSON with this exact schema:
{{
  "semantic_query": string,
  "year_from": integer or null,
  "year_to": integer or null,
  "must_terms": string[]
}}

Rules:
- semantic_query must contain only the main topic
- remove filler phrases like "radovi o", "pronađi radove o", "about papers on"
- normalize Serbian words to base form if possible
- must_terms should contain exact important phrases explicitly required by the user
- if the user explicitly mentions a phrase like "semantic web", include it in must_terms
- if there are no must_terms, return []
- do NOT explain anything
- output ONLY JSON

Examples:

Query: "radovi o ontologijama posle 2021"
Output:
{{
  "semantic_query": "ontologije",
  "year_from": 2021,
  "year_to": null,
  "must_terms": []
}}

Query: "radovi o ontologijama posle 2021 koji pominju semantic web"
Output:
{{
  "semantic_query": "ontologije",
  "year_from": 2021,
  "year_to": null,
  "must_terms": ["semantic web"]
}}

Query: "semantic web after 2020"
Output:
{{
  "semantic_query": "Semantic Web",
  "year_from": 2020,
  "year_to": null,
  "must_terms": ["semantic web"]
}}

Query:
"{query}"
"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",  # add this
                "options": {"temperature": 0}
            },
            timeout=30
        )
        response.raise_for_status()

        text = response.json()["response"].strip()
        print("LLM RAW RESPONSE:", text)

        try:
            return json.loads(text)
        except Exception:
            pass

        json_text = extract_json(text)
        if json_text:
            return json.loads(json_text)

    except Exception as e:
        print("LLM ERROR:", e)

    return {
        "semantic_query": query.strip(),
        "year_from": None,
        "year_to": None,
        "must_terms": []
    }