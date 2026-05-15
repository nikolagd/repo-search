import os

from embeddings.model import model
from etl.db import get_connection

CANDIDATE_MULTIPLIER = int(os.getenv("SEARCH_CANDIDATE_MULTIPLIER", "6"))
TOPIC_TITLE_BOOST = float(os.getenv("SEARCH_TOPIC_TITLE_BOOST", "0.04"))
TOPIC_ABSTRACT_BOOST = float(os.getenv("SEARCH_TOPIC_ABSTRACT_BOOST", "0.01"))
RANKING_PHRASE_BOOST = float(os.getenv("SEARCH_RANKING_PHRASE_BOOST", "0.02"))
QUERY_COVERAGE_BOOST = float(os.getenv("SEARCH_QUERY_COVERAGE_BOOST", "0.003"))


def embed_query(query: str):
    return model.encode(
        f"query: {query.strip()}",
        normalize_embeddings=True,
    ).tolist()


def phrase_boost(title, abstract, phrases, title_boost, abstract_boost):
    if not phrases:
        return 0.0

    title_text = (title or "").lower()
    abstract_text = (abstract or "").lower()
    boost = 0.0

    for phrase in phrases:
        phrase = phrase.lower()

        if phrase in title_text:
            boost += title_boost
        elif phrase in abstract_text:
            boost += abstract_boost

    return boost


def fetch_vector_results(query_vector, limit, year_from, year_to):
    sql = """
        SELECT
            p.id,
            p.title,
            p.abstract,
            p.source_url,
            p.date,
            p.embedding <=> %s::vector AS cosine_distance,
            r.name AS repository_name,
            COALESCE(
                ARRAY_AGG(a.full_name ORDER BY a.full_name)
                    FILTER (WHERE a.full_name IS NOT NULL),
                '{}'
            ) AS authors
        FROM publication p
        LEFT JOIN repository r ON r.id = p.repository_id
        LEFT JOIN publication_author pa ON pa.publication_id = p.id
        LEFT JOIN author a ON a.id = pa.author_id
        WHERE p.embedding IS NOT NULL
    """

    params = [query_vector]

    if year_from is not None:
        sql += " AND p.date >= %s"
        params.append(f"{year_from}-01-01")

    if year_to is not None:
        sql += " AND p.date <= %s"
        params.append(f"{year_to}-12-31")

    sql += """
        GROUP BY p.id, r.name
        ORDER BY cosine_distance ASC
        LIMIT %s
    """

    params.append(limit)

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def semantic_search(
    query: str | None = None,
    embedding_queries: list[str] | None = None,
    limit: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    topic_phrases: list[str] | None = None,
    ranking_phrases: list[str] | None = None,
):
    if embedding_queries is None:
        embedding_queries = [query] if query else []

    embedding_queries = [
        item.strip()
        for item in embedding_queries
        if item and item.strip()
    ]

    topic_phrases = topic_phrases or []
    ranking_phrases = ranking_phrases or []
    candidate_limit = max(limit, limit * CANDIDATE_MULTIPLIER)

    merged = {}

    for embedding_query in embedding_queries:
        query_vector = embed_query(embedding_query)
        rows = fetch_vector_results(query_vector, candidate_limit, year_from, year_to)

        for rank, row in enumerate(rows, start=1):
            publication_id = row[0]
            cosine_distance = float(row[5])
            cosine_similarity = 1 - cosine_distance

            existing = merged.get(publication_id)

            if existing is None:
                merged[publication_id] = {
                    "id": row[0],
                    "title": row[1],
                    "abstract": row[2],
                    "source_url": row[3],
                    "date": row[4],
                    "cosine_distance": cosine_distance,
                    "cosine_similarity": cosine_similarity,
                    "repository": row[6],
                    "authors": row[7],
                    "matched_query": embedding_query,
                    "matched_queries": {embedding_query},
                    "best_rank": rank,
                }
                continue

            existing["matched_queries"].add(embedding_query)

            if cosine_similarity > existing["cosine_similarity"]:
                existing["cosine_distance"] = cosine_distance
                existing["cosine_similarity"] = cosine_similarity
                existing["matched_query"] = embedding_query
                existing["best_rank"] = rank

    results = []

    for result in merged.values():
        topic_boost = phrase_boost(
            result["title"],
            result["abstract"],
            topic_phrases,
            TOPIC_TITLE_BOOST,
            TOPIC_ABSTRACT_BOOST,
        )

        ranking_boost = phrase_boost(
            result["title"],
            result["abstract"],
            ranking_phrases,
            RANKING_PHRASE_BOOST,
            RANKING_PHRASE_BOOST,
        )

        coverage_boost = min(
            len(result["matched_queries"]) * QUERY_COVERAGE_BOOST,
            0.015,
        )

        result["topic_boost"] = topic_boost
        result["ranking_boost"] = ranking_boost
        result["coverage_boost"] = coverage_boost
        result["score"] = result["cosine_similarity"] + topic_boost + ranking_boost + coverage_boost
        result["matched_queries"] = sorted(result["matched_queries"])

        results.append(result)

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]
