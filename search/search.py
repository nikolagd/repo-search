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

    sql = """
        SELECT
            id,
            title,
            abstract,
            source_url,
            date,
            embedding <-> %s::vector AS distance
        FROM publication
        WHERE embedding IS NOT NULL
    """

    params = [query_vector]
    # parsiranje godine iz upita
    if year_from is not None:
        sql += " AND date >= %s"
        params.append(f"{year_from}-01-01")

    sql += """
        ORDER BY embedding <-> %s::vector
        LIMIT %s
    """

    params.append(query_vector)
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    conn.close()

    results = []
    for row in rows:
        results.append({
            "id": row[0],
            "title": row[1],
            "abstract": row[2],
            "source_url": row[3],
            "date": row[4],
            "distance": float(row[5]),
        })

    return results