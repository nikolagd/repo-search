from datetime import datetime

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

    embedding_queries = clean_string_list(raw.get("embedding_queries"))
    if not embedding_queries:
        return None, "embedding_queries must be a non-empty list."

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
        interpreted_query = f"Searching for: {embedding_queries[0]}"

    plan = {
        "embedding_queries": embedding_queries,
        "semantic_query": embedding_queries[0],
        "topic_phrases": clean_string_list(raw.get("topic_phrases")),
        "year_from": year_from,
        "year_to": year_to,
        "ranking_phrases": clean_string_list(raw.get("ranking_phrases")),
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
