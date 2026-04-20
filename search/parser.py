import re


def parse_query(query: str) -> dict:
    query = query.lower()

    year_from = None
    year_to = None

    # srpski
    match_from = re.search(r"posle (\d{4})", query)
    if match_from:
        year_from = int(match_from.group(1))
        #query = query.replace(match_from.group(0), "")

    match_to = re.search(r"pre (\d{4})", query)
    if match_to:
        year_to = int(match_to.group(1))
        #query = query.replace(match_to.group(0), "")

    # engleski
    match_after = re.search(r"after (\d{4})", query)
    if match_after:
        year_from = int(match_after.group(1))
        #query = query.replace(match_after.group(0), "")

    match_before = re.search(r"before (\d{4})", query)
    if match_before:
        year_to = int(match_before.group(1))
        #query = query.replace(match_before.group(0), "")

    # ciscenje
    semantic_query = query.strip()

    return {
        "semantic_query": semantic_query,
        "year_from": year_from,
        "year_to": year_to,
    }