from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

from etl.db import get_connection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
MIGRATION_LOCK_ID = 870341239


@dataclass(frozen=True)
class Migration:
    version: str
    filename: str
    checksum: str
    sql: str


def read_migrations() -> list[Migration]:
    migrations = []
    seen_versions = set()

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = normalize_sql(path.read_text(encoding="utf-8"))
        version = path.stem.split("_", 1)[0]

        if version in seen_versions:
            raise RuntimeError(f"Duplicate migration version: {version}")

        seen_versions.add(version)
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(
            Migration(
                version=version,
                filename=path.name,
                checksum=checksum,
                sql=sql,
            )
        )

    return migrations


def normalize_sql(sql: str) -> str:
    return sql.replace("\r\n", "\n").replace("\r", "\n")


def ensure_migration_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
            )
            """
        )
    conn.commit()


def get_applied_migrations(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations")
        return {version: checksum for version, checksum in cur.fetchall()}


def acquire_migration_lock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))


def release_migration_lock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))


def apply_migration(conn, migration: Migration) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(migration.sql)
            cur.execute(
                """
                INSERT INTO schema_migrations (version, filename, checksum)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.filename, migration.checksum),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate() -> list[Migration]:
    migrations = read_migrations()
    conn = get_connection()

    try:
        ensure_migration_table(conn)
        acquire_migration_lock(conn)

        try:
            applied = get_applied_migrations(conn)
            pending = []

            for migration in migrations:
                applied_checksum = applied.get(migration.version)

                if applied_checksum is None:
                    pending.append(migration)
                    continue

                if applied_checksum != migration.checksum:
                    raise RuntimeError(
                        f"Migration {migration.filename} was already applied with a different checksum."
                    )

            for migration in pending:
                print(f"Applying migration {migration.filename}")
                apply_migration(conn, migration)

            return pending
        finally:
            release_migration_lock(conn)

    finally:
        conn.close()


def status() -> tuple[list[Migration], dict[str, str]]:
    conn = get_connection()

    try:
        ensure_migration_table(conn)
        return read_migrations(), get_applied_migrations(conn)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply database migrations.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("migrate", "status"),
        default="migrate",
        help="Command to run. Defaults to migrate.",
    )
    args = parser.parse_args()

    if args.command == "status":
        migrations, applied = status()
        for migration in migrations:
            state = "applied" if migration.version in applied else "pending"
            print(f"{migration.filename}: {state}")
        return

    applied_now = migrate()

    if not applied_now:
        print("No pending migrations.")
        return

    print(f"Applied {len(applied_now)} migration(s).")


if __name__ == "__main__":
    main()
