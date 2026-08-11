from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable


MAX_AUTHOR_QUERY_TOKENS = 6

_SERBIAN_CYRILLIC = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "ђ": "dj",
        "е": "e",
        "ж": "z",
        "з": "z",
        "и": "i",
        "ј": "j",
        "к": "k",
        "л": "l",
        "љ": "lj",
        "м": "m",
        "н": "n",
        "њ": "nj",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "ћ": "c",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "c",
        "џ": "dz",
        "ш": "s",
        "č": "c",
        "ć": "c",
        "š": "s",
        "ž": "z",
        "đ": "dj",
    }
)


@dataclass(frozen=True)
class AuthorQuery:
    tokens: tuple[str, ...]
    initials: tuple[bool, ...]


def canonicalize_author_name(value: str) -> str:
    """Return a deterministic Serbian Latin search representation."""
    normalized = unicodedata.normalize("NFC", value.casefold()).translate(_SERBIAN_CYRILLIC)
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def parse_author_query(value: str) -> AuthorQuery:
    tokens = tuple(canonicalize_author_name(value).split())
    if not 1 <= len(tokens) <= MAX_AUTHOR_QUERY_TOKENS:
        raise ValueError(f"author names must contain between 1 and {MAX_AUTHOR_QUERY_TOKENS} tokens")
    initials = tuple(len(token) == 1 for token in tokens)
    if all(initials):
        raise ValueError("author initials require a full surname token")
    return AuthorQuery(tokens=tokens, initials=initials)


def author_name_key(value: str) -> tuple[str, ...]:
    return tuple(sorted(parse_author_query(value).tokens))


def _suggestion_tier(query: str, candidate: str) -> int:
    if candidate == query:
        return 0
    candidate_tokens = candidate.split()
    if query in candidate_tokens:
        return 1
    if candidate.startswith(query) or any(token.startswith(query) for token in candidate_tokens):
        return 2
    return 3


def rank_author_suggestions(
    query: str,
    candidates: Iterable[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    canonical_query = canonicalize_author_name(query)

    def key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        canonical_candidate = canonicalize_author_name(str(candidate["display_name"]))
        similarity = SequenceMatcher(None, canonical_query, canonical_candidate, autojunk=False).ratio()
        token_similarity = max(
            (SequenceMatcher(None, canonical_query, token, autojunk=False).ratio() for token in canonical_candidate.split()),
            default=0.0,
        )
        return (
            _suggestion_tier(canonical_query, canonical_candidate),
            -max(similarity, token_similarity),
            -int(candidate["publication_count"]),
            str(candidate["display_name"]).casefold(),
            int(candidate["id"]),
        )

    return sorted(candidates, key=key)[:limit]


# Catalog owns the author table. Both Catalog and Search apply this idempotent
# fragment so deployments remain safe regardless of service startup order.
AUTHOR_SEARCH_SCHEMA_SQL = r"""
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

CREATE OR REPLACE FUNCTION public.repo_search_author_canonical(value TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
RETURNS NULL ON NULL INPUT
AS $$
    SELECT trim(regexp_replace(
        translate(
            replace(replace(replace(replace(replace(
                lower(value),
                'ђ', 'dj'), 'љ', 'lj'), 'њ', 'nj'), 'џ', 'dz'), 'đ', 'dj'),
            'абвгдежзијклмнопрстћуфхцчшčćšž',
            'abvgdezzijklmnoprstcufhccsccsz'
        ),
        '[^[:alnum:]]+', ' ', 'g'
    ));
$$;

CREATE OR REPLACE FUNCTION public.repo_search_author_token_matches(
    stored_token TEXT,
    query_token TEXT,
    query_is_initial BOOLEAN
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN query_is_initial THEN stored_token LIKE query_token || '%'
        ELSE stored_token = query_token
          OR replace(stored_token, 'dj', 'd') = replace(query_token, 'dj', 'd')
    END;
$$;

CREATE OR REPLACE FUNCTION public.repo_search_author_matches(
    stored_name TEXT,
    query_tokens TEXT[],
    query_initials BOOLEAN[]
)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    stored_tokens TEXT[] := regexp_split_to_array(public.repo_search_author_canonical(stored_name), '\s+');
    query_count INTEGER := cardinality(query_tokens);
    stored_count INTEGER := cardinality(stored_tokens);
    query_offset INTEGER;
    stored_offset INTEGER;
    matched BOOLEAN;
    used_positions INTEGER[] := '{}';
BEGIN
    IF query_count IS NULL OR query_count = 0 OR query_count <> cardinality(query_initials)
       OR stored_count < query_count OR array_position(query_initials, FALSE) IS NULL THEN
        RETURN FALSE;
    END IF;

    -- Match full tokens first, then initials. Each query component consumes a
    -- distinct stored-name token, preserving the existing order-independent
    -- exact behavior without allowing an initial to reuse the surname token.
    FOR query_offset IN 1..query_count LOOP
        IF NOT query_initials[query_offset] THEN
            matched := FALSE;
            FOR stored_offset IN 1..stored_count LOOP
                CONTINUE WHEN stored_offset = ANY(used_positions);
                IF public.repo_search_author_token_matches(
                    stored_tokens[stored_offset],
                    query_tokens[query_offset],
                    FALSE
                ) THEN
                    matched := TRUE;
                    used_positions := array_append(used_positions, stored_offset);
                    EXIT;
                END IF;
            END LOOP;
            IF NOT matched THEN
                RETURN FALSE;
            END IF;
        END IF;
    END LOOP;

    FOR query_offset IN 1..query_count LOOP
        IF query_initials[query_offset] THEN
            matched := FALSE;
            FOR stored_offset IN 1..stored_count LOOP
                CONTINUE WHEN stored_offset = ANY(used_positions);
                IF public.repo_search_author_token_matches(
                    stored_tokens[stored_offset],
                    query_tokens[query_offset],
                    TRUE
                ) THEN
                    matched := TRUE;
                    used_positions := array_append(used_positions, stored_offset);
                    EXIT;
                END IF;
            END LOOP;
            IF NOT matched THEN
                RETURN FALSE;
            END IF;
        END IF;
    END LOOP;
    RETURN TRUE;
END;
$$;

ALTER TABLE author
    ADD COLUMN IF NOT EXISTS search_name TEXT
    GENERATED ALWAYS AS (public.repo_search_author_canonical(full_name)) STORED;

CREATE INDEX IF NOT EXISTS idx_author_search_name_trgm
    ON author USING gin (search_name gin_trgm_ops);
"""
