from datetime import datetime
import re
import unicodedata

from microservices.query_service.llm_parser import parse_query_llm, repair_query_plan
from microservices.query_service.parser import extract_year_constraints, parse_query_fallback

CURRENT_YEAR = datetime.now().year


def clean_string_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    cleaned = []
    for item in value:
        if isinstance(item, str) and item.strip():
            normalized = item.strip()
            if normalized not in cleaned:
                cleaned.append(normalized)
    return cleaned


def _author_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(sorted(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)))


def clean_author_names(value) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in clean_string_list(value):
        tokens = re.findall(r"[^\W\d_]+(?:[.'’-][^\W\d_]+)*", name, flags=re.UNICODE)
        comparable = re.sub(r"[,\s]+", " ", name).strip()
        if not (1 <= len(tokens) <= 6) or len(name) > 200:
            continue
        if " ".join(tokens).casefold() != comparable.casefold():
            continue
        key = _author_key(name)
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result[:10]


def sanitize_topic_text(value: str, author_names: list[str]) -> str:
    clean = unicodedata.normalize("NFKC", value)
    for author_name in author_names:
        tokens = re.findall(r"[^\W\d_]+", author_name, flags=re.UNICODE)
        variants = [tokens, list(reversed(tokens))] if len(tokens) > 1 else [tokens]
        for variant in variants:
            pattern = r"[\s,.;:]+".join(re.escape(token) for token in variant)
            clean = re.sub(pattern, " ", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"\b(?:radovi|publikacije)\s+autora\b|\bpapers\s+by\b|\bautor\s*:",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"\s+", " ", clean).strip(" ,;:-")
    if author_names:
        clean = re.sub(r"^(?:o|about|on)\s+", "", clean, flags=re.IGNORECASE)
    return clean


def derive_search_mode(embedding_queries: list[str], author_names: list[str]) -> str:
    if author_names and not embedding_queries:
        return "author"
    if author_names:
        return "hybrid"
    return "semantic"


def apply_explicit_year_constraints(plan: dict, original_query: str) -> dict:
    parsed_years = extract_year_constraints(original_query)
    if parsed_years["year_from"] is not None or parsed_years["year_to"] is not None:
        plan["year_from"] = parsed_years["year_from"]
        plan["year_to"] = parsed_years["year_to"]

    if plan["year_from"] is not None and plan["year_to"] is not None:
        if plan["year_from"] > plan["year_to"]:
            plan["year_from"], plan["year_to"] = plan["year_to"], plan["year_from"]

    return plan


def normalize_plan(raw: dict | None, original_query: str) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, "LLM response is not a JSON object."

    author_names = clean_author_names(raw.get("author_names"))
    embedding_queries = [
        clean
        for item in clean_string_list(raw.get("embedding_queries"))
        if (clean := sanitize_topic_text(item, author_names))
    ]
    embedding_queries = clean_string_list(embedding_queries)
    if not embedding_queries and not author_names:
        return None, "embedding_queries may be empty only when author_names is non-empty."

    year_from = raw.get("year_from")
    year_to = raw.get("year_to")
    if year_from is not None and not isinstance(year_from, int):
        return None, "year_from must be an integer or null."
    if year_to is not None and not isinstance(year_to, int):
        return None, "year_to must be an integer or null."

    for year in (year_from, year_to):
        if year is not None and not (1800 <= year <= CURRENT_YEAR + 1):
            return None, "Extracted year is outside the allowed range."

    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from

    interpreted_query = raw.get("interpreted_query")
    if not isinstance(interpreted_query, str) or not interpreted_query.strip():
        understood = embedding_queries[0] if embedding_queries else ", ".join(author_names)
        interpreted_query = f"Searching for: {understood}"

    plan = {
        "embedding_queries": embedding_queries,
        "semantic_query": embedding_queries[0] if embedding_queries else "",
        "author_names": author_names,
        "search_mode": derive_search_mode(embedding_queries, author_names),
        "topic_phrases": [
            clean
            for item in clean_string_list(raw.get("topic_phrases"))
            if (clean := sanitize_topic_text(item, author_names))
        ],
        "year_from": year_from,
        "year_to": year_to,
        "ranking_phrases": [
            clean
            for item in clean_string_list(raw.get("ranking_phrases"))
            if (clean := sanitize_topic_text(item, author_names))
        ],
        "interpreted_query": interpreted_query.strip(),
        "used_fallback": False,
        "parser_mode": "llm",
    }
    return apply_explicit_year_constraints(plan, original_query), None


def parse_query(query: str) -> dict:
    raw_plan = parse_query_llm(query)
    plan, reason = normalize_plan(raw_plan, query)
    if plan is not None:
        return plan

    if raw_plan is not None:
        repaired_plan = repair_query_plan(query, raw_plan, reason or "Invalid query plan.")
        plan, _ = normalize_plan(repaired_plan, query)
        if plan is not None:
            plan["parser_mode"] = "llm_repaired"
            return plan

    return parse_query_fallback(query)
