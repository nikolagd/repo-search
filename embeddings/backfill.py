from embeddings.model import build_document_text, model
from etl.db import get_connection, update_embedding


def fetch_missing_publications(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, abstract
            FROM publication
            WHERE embedding IS NULL
        """)
        return cur.fetchall()


def embed_missing_publications(conn, batch_size=32, show_progress_bar=True):
    rows = fetch_missing_publications(conn)

    ids = []
    texts = []

    for pub_id, title, abstract in rows:
        text = build_document_text(title, abstract)
        ids.append(pub_id)
        texts.append(text)

    if not texts:
        return 0

    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=show_progress_bar
    )

    for pub_id, embedding in zip(ids, embeddings):
        update_embedding(conn, pub_id, embedding.tolist())

    return len(ids)


def main():
    conn = get_connection()

    try:
        rows = fetch_missing_publications(conn)
        print(f"Found {len(rows)} records to embed")
        embedded_count = embed_missing_publications(conn)
    finally:
        conn.close()

    print("Done.")
    print(f"Embedded records: {embedded_count}")


if __name__ == "__main__":
    main()
