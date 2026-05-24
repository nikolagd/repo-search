import re
import unicodedata


FILLERS = [
    "radovi o",
    "radove o",
    "publikacije o",
    "pronadji publikacije o",
    "pronađi publikacije o",
    "pronadji radove o",
    "pronađi radove o",
    "find papers about",
    "find papers on",
    "papers about",
    "papers on",
]

YEAR_PATTERNS = [
    (r"\b(?:posle|nakon|after)\s+(\d{4})\b", "from", 1),
    (r"\b(?:since|od|from)\s+(\d{4})\b", "from", 0),
    (r"\b(?:pre|prije|before)\s+(\d{4})\b", "to", -1),
    (r"\b(?:until|do|to)\s+(\d{4})\b", "to", 0),
]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def remove_fillers(text: str) -> str:
    clean = text
    for filler in FILLERS:
        clean = re.sub(rf"\b{re.escape(filler)}\b", " ", clean, flags=re.IGNORECASE)
    return normalize_text(clean)


def extract_soft_terms(text: str) -> list[str]:
    terms = []

    for quoted in re.findall(r'"([^"]+)"|\'([^\']+)\'', text):
        term = quoted[0] or quoted[1]
        if term.strip():
            terms.append(term.strip())

    phrase_patterns = [
        r"\b(?:koji pominju|koji pominje|gde se pominje|gdje se pominje)\s+(.+)$",
        r"\b(?:that mention|mentioning|containing)\s+(.+)$",
    ]

    for pattern in phrase_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            terms.append(match.group(1).strip())

    return list(dict.fromkeys(terms))


def extract_year_constraints(query: str) -> dict:
    clean = normalize_text(query.lower())
    year_from = None
    year_to = None

    for pattern, key, offset in YEAR_PATTERNS:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            year = int(match.group(1)) + offset
            if key == "from":
                year_from = year
            else:
                year_to = year
            clean = re.sub(pattern, " ", clean, flags=re.IGNORECASE)

    clean = normalize_text(clean)

    return {
        "clean_query": clean,
        "year_from": year_from,
        "year_to": year_to,
    }


def parse_query(query: str) -> dict:
    parsed_years = extract_year_constraints(query)
    clean = parsed_years["clean_query"]

    soft_terms = extract_soft_terms(clean)
    clean = remove_fillers(clean)

    for term in soft_terms:
        clean = clean.replace(term.lower(), " ")

    clean = normalize_text(clean)

    return {
        "semantic_query": clean or query.strip(),
        "year_from": parsed_years["year_from"],
        "year_to": parsed_years["year_to"],
        "soft_terms": soft_terms,
    }

# samo se ova funkcija koristi, kod iznad se ne koristi
def parse_query_fallback(query: str) -> dict:
    parsed = parse_query(query)
    semantic_query = parsed["semantic_query"]

    return {
        "embedding_queries": [semantic_query],
        "semantic_query": semantic_query,
        "topic_phrases": [],
        "year_from": parsed["year_from"],
        "year_to": parsed["year_to"],
        "ranking_phrases": [],
        "interpreted_query": f"LLM parsing was unavailable, so I searched using: {semantic_query}",
        "used_fallback": True,
    }
