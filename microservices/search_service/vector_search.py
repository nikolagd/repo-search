from __future__ import annotations

import re
import unicodedata
from typing import Any


AUTHOR_TRANSLATE_SOURCE = "čćžšđ"
AUTHOR_TRANSLATE_TARGET = "cczsd"


def normalize_author_tokens(author_name: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", author_name.casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    normalized = normalized.translate(str.maketrans({"đ": "d"}))
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def _append_author_filters(
    sql: str,
    params: list[Any],
    author_names: list[str],
    publication_alias: str,
) -> str:
    for author_name in author_names:
        tokens = normalize_author_tokens(author_name)
        if not tokens:
            raise ValueError("author filters must contain at least one searchable token")
        sql += f"""
              AND EXISTS (
                  SELECT 1
                  FROM publication_author author_pa
                  JOIN author author_row ON author_row.id = author_pa.author_id
                  WHERE author_pa.publication_id = {publication_alias}.id
                    AND (
                        SELECT bool_and(
                            filter_token = ANY(
                                regexp_split_to_array(
                                    trim(
                                        regexp_replace(
                                            translate(lower(author_row.full_name),
                                                      '{AUTHOR_TRANSLATE_SOURCE}',
                                                      '{AUTHOR_TRANSLATE_TARGET}'),
                                            '[^[:alnum:]]+', ' ', 'g'
                                        )
                                    ),
                                    '\\s+'
                                )
                            )
                        )
                        FROM unnest(%s::text[]) AS filter_token
                    )
              )
        """
        params.append(tokens)
    return sql


def _result_projection(ranked_order: str) -> str:
    return f"""
        SELECT ranked.id, ranked.title, ranked.abstract, ranked.source_url, ranked.date,
               ranked.cosine_distance, r.name,
               COALESCE(
                   ARRAY_AGG(a.full_name ORDER BY a.full_name)
                       FILTER (WHERE a.full_name IS NOT NULL),
                   '{{}}'
               ) AS authors
        FROM ranked
        LEFT JOIN repository r ON r.id = ranked.repository_id
        LEFT JOIN publication_author pa ON pa.publication_id = ranked.id
        LEFT JOIN author a ON a.id = pa.author_id
        GROUP BY ranked.id, ranked.title, ranked.abstract, ranked.source_url, ranked.date,
                 ranked.cosine_distance, r.name
        ORDER BY {ranked_order}
    """


def execute_vector_search(
    connection: Any,
    query_vector: list[float],
    limit: int,
    year_from: int | None,
    year_to: int | None,
    author_names: list[str] | None = None,
    *,
    deterministic_ties: bool = False,
) -> list[tuple[Any, ...]]:
    ranked_order = "cosine_distance ASC, id ASC" if deterministic_ties else "cosine_distance ASC"
    final_order = "ranked.cosine_distance ASC, ranked.id ASC" if deterministic_ties else "ranked.cosine_distance ASC"
    if not author_names:
        sql = """
            WITH ranked AS (
                SELECT id, repository_id, title, abstract, source_url, date,
                       embedding <=> %s::vector AS cosine_distance
                FROM publication
                WHERE is_active = TRUE
                  AND embedding IS NOT NULL
        """
        params: list[Any] = [query_vector]
        if year_from is not None:
            sql += " AND date >= %s"
            params.append(f"{year_from}-01-01")
        if year_to is not None:
            sql += " AND date <= %s"
            params.append(f"{year_to}-12-31")
        sql += f"""
                ORDER BY {ranked_order}
                LIMIT %s
            )
        """
        params.append(limit)
        sql += _result_projection(final_order)
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    sql = """
        WITH filtered AS MATERIALIZED (
            SELECT p.id, p.repository_id, p.title, p.abstract, p.source_url, p.date, p.embedding
            FROM publication p
            WHERE p.is_active = TRUE
              AND p.embedding IS NOT NULL
    """
    params: list[Any] = []

    if year_from is not None:
        sql += " AND p.date >= %s"
        params.append(f"{year_from}-01-01")
    if year_to is not None:
        sql += " AND p.date <= %s"
        params.append(f"{year_to}-12-31")
    sql = _append_author_filters(sql, params, author_names, "p")

    sql += f"""
        ), ranked AS (
            SELECT id, repository_id, title, abstract, source_url, date,
                   embedding <=> %s::vector AS cosine_distance
            FROM filtered
            ORDER BY {ranked_order}
            LIMIT %s
        )
    """
    params.extend((query_vector, limit))
    sql += _result_projection(final_order)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def execute_author_search(
    connection: Any,
    limit: int,
    year_from: int | None,
    year_to: int | None,
    author_names: list[str],
) -> list[tuple[Any, ...]]:
    if not author_names:
        raise ValueError("author-only search requires at least one author filter")
    sql = """
        WITH ranked AS (
            SELECT p.id, p.repository_id, p.title, p.abstract, p.source_url, p.date,
                   NULL::double precision AS cosine_distance
            FROM publication p
            WHERE p.is_active = TRUE
    """
    params: list[Any] = []
    if year_from is not None:
        sql += " AND p.date >= %s"
        params.append(f"{year_from}-01-01")
    if year_to is not None:
        sql += " AND p.date <= %s"
        params.append(f"{year_to}-12-31")
    sql = _append_author_filters(sql, params, author_names, "p")
    sql += """
            ORDER BY p.date DESC NULLS LAST, lower(COALESCE(p.title, '')) ASC, p.id ASC
            LIMIT %s
        )
    """
    params.append(limit)
    sql += _result_projection(
        "ranked.date DESC NULLS LAST, lower(COALESCE(ranked.title, '')) ASC, ranked.id ASC"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()
