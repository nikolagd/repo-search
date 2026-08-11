import json
import os

from microservices.common.app_logging import emit_app_event
from microservices.common.http import observed_sync_request
from microservices.common.observability import trace_span

LLM_URL = os.getenv("LLM_URL", "http://localhost:11434/api/generate")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4:12b")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
LLM_WARMUP_ENABLED = os.getenv("LLM_WARMUP_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def parse_json_response(text: str) -> dict | None:
    return json.loads(text.strip())


def call_ollama_json(prompt: str) -> dict | None:
    with trace_span(
        "llm.call",
        {
            "repo_search.llm.provider": LLM_PROVIDER,
            "repo_search.llm.model": LLM_MODEL,
            "repo_search.prompt_length": len(prompt),
        },
    ):
        response = observed_sync_request(
            "POST",
            LLM_URL,
            service_name="query-service",
            upstream_service=LLM_PROVIDER,
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
        return parse_json_response(response.json().get("response", ""))


def call_openai_compatible_json(prompt: str) -> dict | None:
    with trace_span(
        "llm.call",
        {
            "repo_search.llm.provider": LLM_PROVIDER,
            "repo_search.llm.model": LLM_MODEL,
            "repo_search.prompt_length": len(prompt),
        },
    ):
        response = observed_sync_request(
            "POST",
            LLM_URL,
            service_name="query-service",
            upstream_service=LLM_PROVIDER,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=LLM_TIMEOUT,
        )
        response.raise_for_status()
        return parse_json_response(response.json()["choices"][0]["message"]["content"])


def call_llm_json(prompt: str) -> dict | None:
    if LLM_PROVIDER in {"openai", "openai_compatible", "llama_cpp", "llamacpp"}:
        return call_openai_compatible_json(prompt)
    return call_ollama_json(prompt)


def warm_up_llm() -> None:
    if not LLM_WARMUP_ENABLED:
        emit_app_event(
            "query.llm_warmup_skipped",
            "query-service",
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
            reason="disabled",
        )
        return

    if LLM_PROVIDER != "ollama":
        emit_app_event(
            "query.llm_warmup_skipped",
            "query-service",
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
            reason="provider_not_ollama",
        )
        return

    try:
        plan = call_ollama_json('Return exactly this JSON object: {"ok": true}')
        emit_app_event(
            "query.llm_warmup_completed",
            "query-service",
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
            returned_json=plan is not None,
        )
    except Exception as exc:
        emit_app_event(
            "query.llm_warmup_failed",
            "query-service",
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
            error=str(exc),
        )


def parse_query_llm(query: str) -> dict | None:
    prompt = f"""
You are the query understanding layer for an academic publication search engine.

Return ONLY valid JSON with this exact shape:

{{
  "embedding_queries": string[],
  "author_names": string[],
  "author_match": "any" | "all",
  "topic_phrases": string[],
  "year_from": integer or null,
  "year_to": integer or null,
  "ranking_phrases": string[],
  "interpreted_query": string
}}

Rules:
- embedding_queries are used for vector search.
- embedding_queries must contain only the research topic, not date constraints, author names, or author-intent wording.
- Put explicitly identified personal names only in author_names, never in embedding_queries, topic_phrases, or ranking_phrases.
- Set author_match to "any" for "or", "ili", "any author", and general or ambiguous author lists such as "radovi autora A i B".
- Set author_match to "all" only for explicit shared-authorship intent such as "zajedni\u010dki radovi", "koautorski radovi", "oba autora", "written by both", or "coauthored by".
- embedding_queries may be empty only when author_names contains at least one valid name.
- Never include temporal phrases in embedding_queries.
- Extract dates only into year_from and year_to.
- For Serbian and English temporal expressions:
  - words meaning after/later-than set year_from to the year after the mentioned year.
  - words meaning since/from set year_from to the mentioned year.
  - words meaning before/older-than/earlier-than set year_to to the year before the mentioned year.
  - words meaning until/to set year_to to the mentioned year.
- For topical searches, return at least 1 and at most 4 embedding_queries when useful.
- Include a standard academic English version when useful.
- topic_phrases are the main content phrases for soft ranking.
- ranking_phrases are only extra user-emphasized phrases.
- Do not invent or narrow topics that are not in the user query.
- interpreted_query should briefly explain what you understood.

User query:
{query}
"""
    with trace_span("llm.parse_query", {"repo_search.query_length": len(query)}) as span:
        try:
            plan = call_llm_json(prompt)
            if span is not None:
                span.set_attribute("repo_search.llm.returned_plan", plan is not None)
            return plan
        except Exception as exc:
            if span is not None:
                span.set_attribute("repo_search.llm.failed", True)
            emit_app_event(
                "query.parser_failed",
                "query-service",
                provider=LLM_PROVIDER,
                model=LLM_MODEL,
                query_length=len(query),
                error=str(exc),
                fallback_used=True,
            )
            return None


def repair_query_plan(query: str, bad_plan: dict, reason: str) -> dict | None:
    prompt = f"""
You repair invalid JSON search plans for an academic publication search engine.

Return ONLY valid JSON with this exact shape:

{{
  "embedding_queries": string[],
  "author_names": string[],
  "author_match": "any" | "all",
  "topic_phrases": string[],
  "year_from": integer or null,
  "year_to": integer or null,
  "ranking_phrases": string[],
  "interpreted_query": string
}}

Repair rules:
- Put explicitly identified personal names only in author_names.
- author_match must be exactly "any" or "all"; use "any" unless the query explicitly requests shared authorship by every named author.
- Remove author names and author-intent wording from embedding_queries, topic_phrases, and ranking_phrases.
- embedding_queries may be empty only when author_names is non-empty.

Invalid reason:
{reason}

User query:
{query}

Bad plan:
{json.dumps(bad_plan, ensure_ascii=False)}
"""
    with trace_span(
        "llm.repair_query_plan",
        {
            "repo_search.query_length": len(query),
            "repo_search.repair_reason": reason,
        },
    ) as span:
        try:
            plan = call_llm_json(prompt)
            if span is not None:
                span.set_attribute("repo_search.llm.returned_plan", plan is not None)
            return plan
        except Exception as exc:
            if span is not None:
                span.set_attribute("repo_search.llm.failed", True)
            emit_app_event(
                "query.parser_failed",
                "query-service",
                provider=LLM_PROVIDER,
                model=LLM_MODEL,
                query_length=len(query),
                repair_reason=reason,
                error=str(exc),
                fallback_used=True,
            )
            return None
