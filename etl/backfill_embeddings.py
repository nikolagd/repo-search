from etl.embeddings import build_document_text, model
from etl.db import get_connection, update_embedding


def main():
    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, abstract
            FROM publication
            WHERE embedding IS NULL
        """)
        rows = cur.fetchall()

    print(f"Found {len(rows)} records to embed")

    # priprema batcha
    ids = []
    texts = []

    for pub_id, title, abstract in rows:
        text = build_document_text(title, abstract)
        ids.append(pub_id)
        texts.append(text)

    # batchevi 
    embeddings = model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True
    )

    # cuvanje rezultata
    for pub_id, embedding in zip(ids, embeddings):
        update_embedding(conn, pub_id, embedding.tolist())

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()