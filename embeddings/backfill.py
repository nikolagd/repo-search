import os

from embeddings.model import build_document_text, model
from etl.db import get_connection, update_embedding


DEFAULT_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))


def fetch_missing_publications(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, abstract
            FROM publication
            WHERE embedding IS NULL
        """)
        return cur.fetchall()


def iter_batches(items, batch_size):
    for index in range(0, len(items), batch_size):
        yield items[index:index + batch_size]


def embed_missing_publications(conn, batch_size=DEFAULT_BATCH_SIZE, show_progress_bar=True):
    rows = fetch_missing_publications(conn)
    total_embedded = 0

    if not rows:
        return 0

    for batch in iter_batches(rows, batch_size):
        ids = []
        texts = []

        for pub_id, title, abstract in batch:
            text = build_document_text(title, abstract)
            ids.append(pub_id)
            texts.append(text)

        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress_bar
        )

        for pub_id, embedding in zip(ids, embeddings):
            update_embedding(conn, pub_id, embedding.tolist())
            total_embedded += 1

    return total_embedded


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
