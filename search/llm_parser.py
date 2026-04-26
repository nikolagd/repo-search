import json
import os

import requests

LLM_URL = os.getenv("LLM_URL", "http://localhost:11434/api/generate")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:8b")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))


def call_llm_json(prompt: str) -> dict | None:
    response = requests.post(
        LLM_URL,
        json={
            "model": LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        },
        timeout=LLM_TIMEOUT,
    )
    response.raise_for_status()
    return json.loads(response.json().get("response", "").strip())


def parse_query_llm(query: str) -> dict | None:
    #glavni prompt za parsiranje korisnikovog upita u strukturirani plan pretrage
    prompt = f"""
You are the query understanding layer for an academic publication search engine.

Return ONLY valid JSON with this exact shape:

{{
  "embedding_queries": string[],
  "topic_phrases": string[],
  "year_from": integer or null,
  "year_to": integer or null,
  "ranking_phrases": string[],
  "interpreted_query": string
}}

Rules:
- embedding_queries are used for vector search.
- embedding_queries must contain only the research topic, not date constraints.
- Never include temporal phrases in embedding_queries.
- Extract dates only into year_from and year_to.
- For Serbian and English temporal expressions:
  - words meaning after/later-than set year_from to the year after the mentioned year.
  - words meaning since/from set year_from to the mentioned year.
  - words meaning before/older-than/earlier-than set year_to to the year before the mentioned year.
  - words meaning until/to set year_to to the mentioned year.
  - standalone words meaning "year" do not change the direction of the date constraint.
- Every year constraint mentioned by the user must be represented in year_from or year_to.
- If interpreted_query mentions an after/since/later-than condition, year_from must be set consistently.
- If interpreted_query mentions a before/older-than/earlier-than condition, year_to must be set consistently.
- Do a final consistency check before returning JSON: interpreted_query must not contain a date condition that is missing from year_from/year_to.
- Return at least 2 and at most 4 embedding_queries. If the topic has a Serbian/English variant, include both the user's topic wording and the standard academic English wording.
- The first embedding query should preserve the user's topic wording with filler removed.
- Include a standard academic English version when useful.
- Include a fuller topic formulation, but still without dates.
- topic_phrases are the main content phrases for soft ranking, not filtering.
- ranking_phrases are only extra user-emphasized phrases, such as quoted phrases or phrases after "mentions", "contains", "pominje", or "sadrzi".
- The main topic belongs in topic_phrases, not ranking_phrases.
- Remove generic request words like "find", "search", "papers", "publications", "radovi", "publikacije", "potrebne su mi", "pronadji", and "pronađi".
- If the user uses Serbian inflection, Serbian spelling, or ASCII transliteration for a known academic concept, include the standard academic English phrase.
- Preserve broad fields as broad fields. Do not narrow "artificial intelligence" into a subtopic like vector search, retrieval, neural networks, or machine learning unless the user explicitly asks for that subtopic.
- Do not invent concepts that are not in the user query.
- interpreted_query should briefly explain what you understood.
- Do not invent topics that are not present in the user query.
- If the user asks for a broad field, keep it broad.
- Do not narrow artificial intelligence to vector search, retrieval, neural networks, etc. unless explicitly requested.
- Date constraints must appear only in year_from/year_to, never in embedding_queries.
- Before returning JSON, silently perform this checklist:
  1. Remove request/filler words from embedding_queries.
  2. Remove all temporal language from embedding_queries.
  3. Preserve the user's topic without inventing a narrower topic.
  4. Put every date constraint into year_from/year_to.
  5. Ensure interpreted_query agrees with year_from/year_to.

User query:
{query}
"""

    try:
        return call_llm_json(prompt)
    except Exception as exc:
        print("LLM parser failed:", exc)
        return None


def repair_query_plan(query: str, bad_plan: dict, reason: str) -> dict | None:
    prompt = f"""
You repair invalid JSON search plans for an academic publication search engine.

Return ONLY valid JSON with this exact shape:

{{
  "embedding_queries": string[],
  "topic_phrases": string[],
  "year_from": integer or null,
  "year_to": integer or null,
  "ranking_phrases": string[],
  "interpreted_query": string
}}

Repair rules:
- Keep only the user's research topic in embedding_queries.
- Remove date wording from embedding_queries.
- Do not invent or narrow the topic.
- If the user asks for a broad field, keep it broad.
- For Serbian and English temporal expressions, words meaning after/later-than set year_from to the year after the mentioned year.
- For Serbian and English temporal expressions, words meaning since/from set year_from to the mentioned year.
- For Serbian and English temporal expressions, words meaning before/older-than/earlier-than set year_to to the year before the mentioned year.
- For Serbian and English temporal expressions, words meaning until/to set year_to to the mentioned year.
- Every year constraint mentioned by the user must be represented in year_from or year_to.
- interpreted_query must agree with year_from/year_to.
- Return 2 to 4 embedding queries.

Invalid reason:
{reason}

User query:
{query}

Bad plan:
{json.dumps(bad_plan, ensure_ascii=False)}
"""

    try:
        return call_llm_json(prompt)
    except Exception as exc:
        print("LLM repair failed:", exc)
        return None
