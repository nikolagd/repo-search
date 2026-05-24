from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from etl.db import get_connection


def normalize_repository_payload(name: str, oai_endpoint: str, refresh_interval: int | None) -> dict[str, Any]:
    normalized_name = name.strip()
    normalized_endpoint = oai_endpoint.strip()

    if not normalized_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Repository name cannot be empty.",
        )

    if not normalized_endpoint:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="OAI endpoint cannot be empty.",
        )

    if not normalized_endpoint.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="OAI endpoint must start with http:// or https://.",
        )

    return {
        "name": normalized_name,
        "oai_endpoint": normalized_endpoint,
        "refresh_interval": refresh_interval,
    }


def repository_from_row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "oai_endpoint": row[2],
        "last_harvest": row[3].isoformat() if row[3] else None,
        "refresh_interval": row[4],
    }


def ensure_unique_endpoint(conn, oai_endpoint: str, exclude_repo_id: int | None = None) -> None:
    with conn.cursor() as cur:
        if exclude_repo_id is None:
            cur.execute(
                """
                SELECT id
                FROM repository
                WHERE oai_endpoint = %s
                LIMIT 1
                """,
                (oai_endpoint,),
            )
        else:
            cur.execute(
                """
                SELECT id
                FROM repository
                WHERE oai_endpoint = %s
                  AND id <> %s
                LIMIT 1
                """,
                (oai_endpoint, exclude_repo_id),
            )

        if cur.fetchone() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A repository with this OAI endpoint already exists.",
            )


def create_admin_repository(name: str, oai_endpoint: str, refresh_interval: int | None) -> dict[str, Any]:
    payload = normalize_repository_payload(name, oai_endpoint, refresh_interval)
    conn = get_connection()

    try:
        ensure_unique_endpoint(conn, payload["oai_endpoint"])

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repository (name, oai_endpoint, refresh_interval)
                VALUES (%s, %s, %s)
                RETURNING id, name, oai_endpoint, last_harvest, refresh_interval
                """,
                (payload["name"], payload["oai_endpoint"], payload["refresh_interval"]),
            )
            repository = repository_from_row(cur.fetchone())
        conn.commit()
        return repository
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_admin_repository(
    repo_id: int,
    name: str,
    oai_endpoint: str,
    refresh_interval: int | None,
) -> dict[str, Any]:
    payload = normalize_repository_payload(name, oai_endpoint, refresh_interval)
    conn = get_connection()

    try:
        ensure_unique_endpoint(conn, payload["oai_endpoint"], exclude_repo_id=repo_id)

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE repository
                SET name = %s,
                    oai_endpoint = %s,
                    refresh_interval = %s
                WHERE id = %s
                RETURNING id, name, oai_endpoint, last_harvest, refresh_interval
                """,
                (payload["name"], payload["oai_endpoint"], payload["refresh_interval"], repo_id),
            )
            repository = repository_from_row(cur.fetchone())

        if repository is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository was not found.",
            )

        conn.commit()
        return repository
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
