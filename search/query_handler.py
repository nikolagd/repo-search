from search.llm_parser import parse_query_llm
from search.parser import parse_query as parse_query_regex


def is_valid(parsed: dict) -> bool:
    if not isinstance(parsed, dict):
        return False

    if "semantic_query" not in parsed:
        return False

    if not isinstance(parsed["semantic_query"], str):
        return False

    return True


def parse_query(query: str) -> dict:
    parsed = parse_query_llm(query)

    if not is_valid(parsed):
        return parse_query_regex(query)

    return parsed