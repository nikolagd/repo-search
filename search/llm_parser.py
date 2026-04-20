import requests
import json
import re

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3:8b"


def extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def parse_query_llm(query: str) -> dict:
    prompt = f"""
You are a strict JSON generator.

Extract search parameters from the query.

Return ONLY valid JSON:
{{
  "semantic_query": string,
  "year_from": integer or null,
  "year_to": integer or null
}}

Rules:
- semantic_query must be the MAIN TOPIC only
- normalize words to base form (lemma)
  (example: "ontologijama" → "ontologije")
- remove filler words (like "radovi o", "about", etc.)
- semantic_query should contain key terms describing the topic (3 to 6 words)
- include related concepts if useful (e.g., ontology → semantic web, knowledge representation)
- do NOT explain anything
- output ONLY JSON

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
                "options": {"temperature": 0}
            },
            timeout=30
        )

        text = response.json()["response"].strip()

        # direktno parsiranje
        try:
            return json.loads(text)
        except:
            pass

        # json blok
        json_text = extract_json(text)
        if json_text:
            return json.loads(json_text)

    except Exception:
        pass

    # ako llm pukne
    return {
        "semantic_query": query,
        "year_from": None,
        "year_to": None,
    }