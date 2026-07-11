from __future__ import annotations

from typing import Any


def execute_vector_search(
    connection: Any,
    query_vector: list[float],
    limit: int,
    year_from: int | None,
    year_to: int | None,
    *,
    deterministic_ties: bool = False,
) -> list[tuple[Any, ...]]:
    sql = """
        WITH ranked AS (
            SELECT id, repository_id, title, abstract, source_url, date,
                   embedding <=> %s::vector AS cosine_distance
            FROM publication
            WHERE embedding IS NOT NULL
    """
    params: list[Any] = [query_vector]

    if year_from is not None:
        sql += " AND date >= %s"
        params.append(f"{year_from}-01-01")
    if year_to is not None:
        sql += " AND date <= %s"
        params.append(f"{year_to}-12-31")

    ranked_order = "cosine_distance ASC, id ASC" if deterministic_ties else "cosine_distance ASC"
    final_order = "ranked.cosine_distance ASC, ranked.id ASC" if deterministic_ties else "ranked.cosine_distance ASC"
    sql += f"""
            ORDER BY {ranked_order}
            LIMIT %s
        )
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
        ORDER BY {final_order}
    """
    params.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()
