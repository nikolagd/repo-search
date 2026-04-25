from embeddings.model import model
from etl.db import get_connection


def embed_query(query: str):
    return model.encode(
        f"query: {query.strip()}",
        normalize_embeddings=True
    ).tolist()


def semantic_search(query: str, limit: int = 10, year_from=None, year_to=None, must_terms=None):
    conn = get_connection()
    query_vector = embed_query(query)
    must_terms = must_terms or []

    sql = """
        SELECT
            p.id,
            p.title,
            p.abstract,
            p.source_url,
            p.date,
            p.embedding <=> %s::vector AS distance
        FROM publication p
        WHERE p.embedding IS NOT NULL
    """

    params = [query_vector]

    if year_from is not None:
        sql += " AND p.date >= %s"
        params.append(f"{year_from}-01-01")

    if year_to is not None:
        sql += " AND p.date <= %s"
        params.append(f"{year_to}-12-31")

    # obavezni termini?
    #for term in must_terms:
     #   sql += """
      #      AND (
       #         LOWER(COALESCE(p.title, '')) LIKE %s
        #        OR LOWER(COALESCE(p.abstract, '')) LIKE %s
         #   )
        #"""
        #like_term = f"%{term.lower()}%"
        #params.append(like_term)
        #params.append(like_term)

    sql += """
        ORDER BY p.embedding <=> %s::vector
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
        distance = float(row[5])

        results.append({
            "id": row[0],
            "title": row[1],
            "abstract": row[2],
            "source_url": row[3],
            "date": row[4],
            "distance": distance,
            "similarity": 1 - distance
        })

    return results