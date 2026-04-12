import os
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )


def normalize_date(date_str):
    if not date_str:
        return None

    try:
        # ISO format
        return datetime.fromisoformat(date_str.replace("Z", ""))
    except:
        pass

    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except:
            continue

    return None


def insert_publication(conn, repo_id, record):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO publication (
                repository_id,
                oai_identifier,
                title,
                abstract,
                date,
                source_url
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (oai_identifier) DO UPDATE SET
                title = EXCLUDED.title,
                abstract = EXCLUDED.abstract,
                date = EXCLUDED.date,
                source_url = EXCLUDED.source_url
            RETURNING id
            """,
            (
                repo_id,
                record["oai_identifier"],
                record["title"],
                record["abstract"],
                normalize_date(record["date"]),
                record["source_url"],
            ),
        )

        publication_id = cur.fetchone()[0]

        for author_full_name in record["authors"]:
            cur.execute("""
                INSERT INTO author (full_name)
                VALUES (%s)
                ON CONFLICT (full_name) DO NOTHING
                RETURNING id
            """, (author_full_name,))

            result = cur.fetchone()

            if result:
                author_id = result[0]
            else:
                # ako autor postoji, uzmi id
                cur.execute(
                    "SELECT id FROM author WHERE full_name = %s",
                    (author_full_name,)
                )
                author_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO publication_author (publication_id, author_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (publication_id, author_id))

    conn.commit()


def get_last_harvest(conn, repo_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_harvest FROM repository WHERE id = %s",
            (repo_id,)
        )
        result = cur.fetchone()
        return result[0] if result else None
    
def update_last_harvest(conn, repo_id):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE repository SET last_harvest = NOW() WHERE id = %s",
            (repo_id,)
        )
    conn.commit()