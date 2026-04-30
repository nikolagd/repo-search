from pathlib import Path
from datetime import datetime
import argparse
import os
import sys
import xml.etree.ElementTree as ET
import psycopg2

from etl.db import (
    ensure_repository,
    get_connection,
    get_repository,
    get_repository_by_endpoint,
    insert_publication,
    update_last_harvest,
)
from etl.oai_client import OAIClientError, OAINoRecordsMatch, choose_metadata_prefix, fetch_page
from etl.parser import parse_oai_xml


def harvest_repository(conn, repository, output_dir=None):
    if repository is None:
        raise RuntimeError("Repository was not found.")

    if output_dir is None:
        output_dir = Path("data")

    output_dir.mkdir(exist_ok=True)

    repo_id = repository["id"]
    base_url = repository["oai_endpoint"]
    total_processed = 0
    page_num = 1
    harvest_started_at = datetime.now()
    last_harvest = repository["last_harvest"]
    metadata_prefix = choose_metadata_prefix(base_url=base_url)

    if last_harvest:
        from_date = last_harvest.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        from_date = None

    try:
        xml_text = fetch_page(
            from_date=from_date,
            metadata_prefix=metadata_prefix,
            base_url=base_url,
        )
    except OAINoRecordsMatch:
        print(f"Repository {repo_id}: {repository['name']}")
        print(f"Using metadata_prefix: {metadata_prefix}")
        print(f"Using from_date: {from_date}")
        print("No records matched the incremental harvest window.")
        update_last_harvest(conn, repo_id, harvest_started_at)
        return 0

    while True:
        # Cuva xml za proveru
        output_file = output_dir / f"repo_{repo_id}_page_{page_num}.xml"
        output_file.write_text(xml_text, encoding="utf-8")

        records, token = parse_oai_xml(xml_text, metadata_prefix)
        print(f"Repository {repo_id}: {repository['name']}")
        print(f"Using metadata_prefix: {metadata_prefix}")
        print(f"Using from_date: {from_date}")
        print(f"Page {page_num}: {len(records)} records")

        for record in records:
            insert_publication(conn, repo_id, record)
            total_processed += 1

        if not token:
            print("No more pages.")
            break

        print(f"Next token: {token}")

        xml_text = fetch_page(token, base_url=base_url)
        page_num += 1

    update_last_harvest(conn, repo_id, harvest_started_at)
    return total_processed


def parse_args():
    parser = argparse.ArgumentParser(description="Harvest one OAI-PMH repository.")
    parser.add_argument(
        "--repo-id",
        type=int,
        help="Harvest an existing repository row by database id.",
    )
    parser.add_argument(
        "--repo-url",
        default=os.getenv("OAI_BASE_URL"),
        help="OAI-PMH endpoint to find in the repository table.",
    )
    parser.add_argument(
        "--repo-name",
        default=os.getenv("OAI_REPOSITORY_NAME", "Default OAI repository"),
        help="Repository name used when creating a repository by URL.",
    )
    parser.add_argument(
        "--create-repo",
        action="store_true",
        help="Create the repository row if --repo-url does not already exist.",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=(
            int(os.getenv("OAI_REFRESH_INTERVAL"))
            if os.getenv("OAI_REFRESH_INTERVAL")
            else None
        ),
        help="Refresh interval in minutes stored for the repository row.",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where fetched XML pages are written.",
    )
    return parser.parse_args()


def resolve_repository(conn, args):
    if args.repo_id is not None:
        return get_repository(conn, args.repo_id)

    if not args.repo_url:
        raise RuntimeError("Set OAI_BASE_URL or pass --repo-url.")

    repository = get_repository_by_endpoint(conn, args.repo_url)

    if repository is not None:
        return repository

    if not args.create_repo:
        raise RuntimeError(
            "Repository URL was not found. "
            "Use --create-repo to create it, or pass --repo-id for an existing row."
        )

    return ensure_repository(
        conn,
        name=args.repo_name,
        oai_endpoint=args.repo_url,
        refresh_interval=args.refresh_interval,
    )


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    conn = None

    try:
        conn = get_connection()
        total_processed = harvest_repository(
            conn,
            resolve_repository(conn, args),
            output_dir=output_dir,
        )
    except (OAIClientError, ET.ParseError, psycopg2.Error, RuntimeError) as exc:
        print(f"ETL failed: {exc}", file=sys.stderr)
        print("last_harvest was not updated.", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if conn is not None:
            conn.close()

    print(f"\nTotal processed into DB: {total_processed}")


if __name__ == "__main__":
    main()
