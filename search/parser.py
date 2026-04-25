import re

# rucni parser ako LLM pukne, samo regex i pravila
def parse_query(query: str) -> dict:
    query_lower = query.lower()
    clean = query_lower

    # izvlaci godine
    year_from, year_to = None, None
    for pattern, key in [
        (r"posle (\d{4})", "from"), (r"after (\d{4})", "from"),
        (r"pre (\d{4})", "to"), (r"before (\d{4})", "to")
    ]:
        m = re.search(pattern, clean)
        if m:
            if key == "from": year_from = int(m.group(1))
            else: year_to = int(m.group(1))
            clean = re.sub(pattern, "", clean)

    # uklanja filler fraze
    for filler in ["radovi o", "pronađi radove o", "papers about", "find papers on"]:
        clean = clean.replace(filler, "")

    return {
        "semantic_query": clean.strip(),
        "year_from": year_from,
        "year_to": year_to,
        "must_terms": []
    }