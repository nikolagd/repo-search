import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import psycopg2

from etl.db import get_connection, get_due_repositories
from etl.main import harvest_repository
from etl.oai_client import OAIClientError


def main():
    output_dir = Path("data")
    conn = None
    failed_count = 0

    try:
        conn = get_connection()
        repositories = get_due_repositories(conn)

        if not repositories:
            print("No repositories are due for harvest.")
            return

        print(f"Repositories due for harvest: {len(repositories)}")

        for repository in repositories:
            try:
                total_processed = harvest_repository(conn, repository, output_dir=output_dir)
                print(
                    f"Repository {repository['id']} harvest complete. "
                    f"Processed records: {total_processed}"
                )
            except (OAIClientError, ET.ParseError, psycopg2.Error, RuntimeError) as exc:
                failed_count += 1
                if conn is not None:
                    conn.rollback()
                print(
                    f"Repository {repository['id']} harvest failed: {exc}",
                    file=sys.stderr,
                )

    finally:
        if conn is not None:
            conn.close()

    if failed_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
