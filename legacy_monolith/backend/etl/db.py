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
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO publication (
                    repository_id,
                    oai_identifier,
                    title,
                    abstract,
                    date,
                    source_url,
                    is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (repository_id, oai_identifier) DO UPDATE SET
                    title = EXCLUDED.title,
                    abstract = EXCLUDED.abstract,
                    date = EXCLUDED.date,
                    source_url = EXCLUDED.source_url,
                    is_active = TRUE,
                    embedding = CASE
                        WHEN publication.is_active = TRUE
                         AND publication.title IS NOT DISTINCT FROM EXCLUDED.title
                         AND publication.abstract IS NOT DISTINCT FROM EXCLUDED.abstract
                        THEN publication.embedding
                        ELSE NULL
                    END
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

            cur.execute(
                """
                UPDATE publication_tombstone
                SET cleared_at = NOW()
                WHERE repository_id = %s
                  AND oai_identifier = %s
                  AND cleared_at IS NULL
                """,
                (repo_id, record["oai_identifier"]),
            )

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
    except Exception:
        conn.rollback()
        raise


def repository_from_row(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "oai_endpoint": row[2],
        "last_harvest": row[3],
        "refresh_interval": row[4],
    }


def get_repository(conn, repo_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, oai_endpoint, last_harvest, refresh_interval
            FROM repository
            WHERE id = %s
            """,
            (repo_id,)
        )
        return repository_from_row(cur.fetchone())


def get_repository_by_endpoint(conn, oai_endpoint):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, oai_endpoint, last_harvest, refresh_interval
            FROM repository
            WHERE oai_endpoint = %s
            ORDER BY id
            LIMIT 1
            """,
            (oai_endpoint,)
        )
        return repository_from_row(cur.fetchone())


def ensure_repository(conn, name, oai_endpoint, refresh_interval=None):
    repository = get_repository_by_endpoint(conn, oai_endpoint)

    if repository is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE repository
                SET name = %s,
                    refresh_interval = COALESCE(%s, refresh_interval)
                WHERE id = %s
                RETURNING id, name, oai_endpoint, last_harvest, refresh_interval
                """,
                (name, refresh_interval, repository["id"])
            )
            repository = repository_from_row(cur.fetchone())

        conn.commit()
        return repository

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repository (name, oai_endpoint, refresh_interval)
            VALUES (%s, %s, %s)
            RETURNING id, name, oai_endpoint, last_harvest, refresh_interval
            """,
            (name, oai_endpoint, refresh_interval)
        )
        repository = repository_from_row(cur.fetchone())

    conn.commit()
    return repository


def get_due_repositories(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, oai_endpoint, last_harvest, refresh_interval
            FROM repository
            WHERE last_harvest IS NULL
               OR (
                   refresh_interval IS NOT NULL
                   AND last_harvest + (refresh_interval * INTERVAL '1 minute') <= NOW()
               )
            ORDER BY id
            """
        )
        return [repository_from_row(row) for row in cur.fetchall()]


def get_last_harvest(conn, repo_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_harvest FROM repository WHERE id = %s",
            (repo_id,)
        )
        result = cur.fetchone()
        return result[0] if result else None
    
def update_last_harvest(conn, repo_id, harvested_at=None):
    with conn.cursor() as cur:
        if harvested_at is None:
            cur.execute(
                "UPDATE repository SET last_harvest = NOW() WHERE id = %s",
                (repo_id,)
            )
        else:
            cur.execute(
                "UPDATE repository SET last_harvest = %s WHERE id = %s",
                (harvested_at, repo_id)
            )
    conn.commit()

def update_embedding(conn, publication_id, embedding):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE publication
            SET embedding = %s
            WHERE id = %s
            """,
            (embedding, publication_id),
        )
    conn.commit()
