from __future__ import annotations

from typing import Any

from microservices.common.author_names import canonicalize_author_name, parse_author_query


def normalize_author_tokens(author_name: str) -> list[str]:
    return canonicalize_author_name(author_name).split()


def _append_author_filters(
    sql: str,
    params: list[Any],
    author_names: list[str],
    publication_alias: str,
    author_ids: list[int] | None = None,
) -> str:
    for author_name in author_names:
        query = parse_author_query(author_name)
        sql += f"""
              AND EXISTS (
                  SELECT 1
                  FROM publication_author author_pa
                  JOIN author author_row ON author_row.id = author_pa.author_id
                  WHERE author_pa.publication_id = {publication_alias}.id
                    AND public.repo_search_author_matches(
                        author_row.full_name,
                        %s::text[],
                        %s::boolean[]
                    )
              )
        """
        params.extend((list(query.tokens), list(query.initials)))
    for author_id in author_ids or []:
        sql += f"""
              AND EXISTS (
                  SELECT 1
                  FROM publication_author selected_author_pa
                  WHERE selected_author_pa.publication_id = {publication_alias}.id
                    AND selected_author_pa.author_id = %s
              )
        """
        params.append(author_id)
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
    author_ids: list[int] | None = None,
    *,
    deterministic_ties: bool = False,
) -> list[tuple[Any, ...]]:
    ranked_order = "cosine_distance ASC, id ASC" if deterministic_ties else "cosine_distance ASC"
    final_order = "ranked.cosine_distance ASC, ranked.id ASC" if deterministic_ties else "ranked.cosine_distance ASC"
    if not author_names and not author_ids:
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
    sql = _append_author_filters(sql, params, author_names or [], "p", author_ids)

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
    author_ids: list[int] | None = None,
) -> list[tuple[Any, ...]]:
    if not author_names and not author_ids:
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
    sql = _append_author_filters(sql, params, author_names, "p", author_ids)
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
