from embeddings.model import model
from etl.db import get_connection


def embed_query(query: str):
    vector = model.encode(
        f"query: {query.strip()}",
        normalize_embeddings=True
    )
    return vector.tolist()


def semantic_search(query: str, limit: int = 10):
    conn = get_connection()
    query_vector = embed_query(query)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                title,
                abstract,
                source_url,
                date,
                embedding <-> %s::vector AS distance
            FROM publication
            WHERE embedding IS NOT NULL
            ORDER BY embedding <-> %s::vector
            LIMIT %s
            """,
            (query_vector, query_vector, limit),
        )
        rows = cur.fetchall()

    conn.close()
    return rows