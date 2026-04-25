from search.llm_parser import parse_query_llm
from search.parser import parse_query as parse_query_regex


def is_valid(parsed: dict, original_query: str) -> bool:
    if not isinstance(parsed, dict):
        return False

    required_keys = ["semantic_query", "year_from", "year_to", "must_terms"]
    if not all(k in parsed for k in required_keys):
        return False

    if not isinstance(parsed["semantic_query"], str) or not parsed["semantic_query"].strip():
        return False

    if not isinstance(parsed["must_terms"], list):
        return False

    return True

def parse_query(query: str) -> dict:
    parsed = parse_query_llm(query)

    if not is_valid(parsed, query):
        print("LLM parse failed -> regex fallback")
        return parse_query_regex(query)

    return parsed