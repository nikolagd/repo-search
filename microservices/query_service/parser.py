import re
import unicodedata


FILLERS = [
    "radovi o",
    "radove o",
    "publikacije o",
    "pronađi publikacije o",
    "pronadji publikacije o",
    "pronađi radove o",
    "pronadji radove o",
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

AUTHOR_PATTERNS = [
    re.compile(
        r"^(?:radovi|publikacije)\s+autora\s+(.+?)(?:\s+(?:o|na\s+temu)\s+(.+))?$",
        re.IGNORECASE,
    ),
    re.compile(r"^papers\s+by\s+(.+?)(?:\s+(?:about|on)\s+(.+))?$", re.IGNORECASE),
]
AUTHOR_TOKEN_PATTERN = re.compile(r"[^\W\d_]+(?:[.'’-][^\W\d_]+)*", re.UNICODE)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _valid_author_name(value: str) -> str | None:
    name = normalize_text(value.strip(" ,;:"))
    tokens = AUTHOR_TOKEN_PATTERN.findall(name)
    if not (1 <= len(tokens) <= 6) or " ".join(tokens).casefold() != re.sub(
        r"[,\s]+", " ", name
    ).strip().casefold():
        return None
    return name if len(name) <= 200 else None


def extract_author_constraints(text: str) -> dict:
    clean = normalize_text(text)
    explicit = re.search(r"(?:^|\s)autor\s*:\s*(.+?)(?:\s*;\s*(.*)|$)", clean, re.IGNORECASE)
    if explicit:
        name = _valid_author_name(explicit.group(1))
        if name:
            before = clean[: explicit.start()].strip()
            after = (explicit.group(2) or "").strip()
            return {"clean_query": normalize_text(f"{before} {after}"), "author_names": [name]}

    for pattern in AUTHOR_PATTERNS:
        match = pattern.fullmatch(clean)
        if not match:
            continue
        name = _valid_author_name(match.group(1))
        if name:
            return {
                "clean_query": normalize_text(match.group(2) or ""),
                "author_names": [name],
            }

    return {"clean_query": clean, "author_names": []}


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

    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from

    return {
        "clean_query": normalize_text(clean),
        "year_from": year_from,
        "year_to": year_to,
    }


def parse_query_fallback(query: str) -> dict:
    parsed_authors = extract_author_constraints(query)
    parsed_years = extract_year_constraints(parsed_authors["clean_query"])
    soft_terms = extract_soft_terms(parsed_years["clean_query"])
    clean = remove_fillers(parsed_years["clean_query"])

    for term in soft_terms:
        clean = clean.replace(term.lower(), " ")

    semantic_query = normalize_text(clean)
    if not semantic_query and not parsed_authors["author_names"]:
        semantic_query = query.strip()
    embedding_queries = [semantic_query] if semantic_query else []
    understood = semantic_query or ", ".join(parsed_authors["author_names"])

    return {
        "embedding_queries": embedding_queries,
        "semantic_query": semantic_query,
        "author_names": parsed_authors["author_names"],
        "search_mode": "hybrid" if embedding_queries and parsed_authors["author_names"] else (
            "author" if parsed_authors["author_names"] else "semantic"
        ),
        "topic_phrases": [],
        "year_from": parsed_years["year_from"],
        "year_to": parsed_years["year_to"],
        "ranking_phrases": [],
        "interpreted_query": f"LLM parsing was unavailable, so I searched using: {understood}",
        "used_fallback": True,
        "parser_mode": "fallback",
    }
