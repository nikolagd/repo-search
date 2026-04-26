from pathlib import Path
from datetime import datetime
import sys
import xml.etree.ElementTree as ET
import psycopg2

from etl.db import get_connection, get_repository, insert_publication, update_last_harvest
from etl.oai_client import OAIClientError, choose_metadata_prefix, fetch_page
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

    xml_text = fetch_page(
        from_date=from_date,
        metadata_prefix=metadata_prefix,
        base_url=base_url,
    )

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


def main():
    output_dir = Path("data")
    repo_id = 1
    conn = None

    try:
        conn = get_connection()
        total_processed = harvest_repository(
            conn,
            get_repository(conn, repo_id),
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
