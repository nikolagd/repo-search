from embeddings.model import model
from etl.db import get_connection


def embed_query(query: str):
    return model.encode(
        f"query: {query.strip()}",
        normalize_embeddings=True
    ).tolist()


def semantic_search(query: str, limit: int = 10, year_from: int | None = None):
    conn = get_connection()
    query_vector = embed_query(query)

    base_query = """
        SELECT
            id,
            title,
            source_url,
            date,
            embedding <-> %s::vector AS distance
        FROM publication
        WHERE embedding IS NOT NULL
    """

    params = [query_vector]

    # dodavanje filtera za godinu
    if year_from:
        base_query += " AND date >= %s"
        params.append(f"{year_from}-01-01")

    # redosled
    base_query += " ORDER BY embedding <-> %s::vector LIMIT %s"

    params.append(query_vector)
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(base_query, params)
        results = cur.fetchall()

    conn.close()
    return results