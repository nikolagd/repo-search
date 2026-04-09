from pathlib import Path
from datetime import datetime
from etl.db import get_connection, insert_publication, get_last_harvest, update_last_harvest
from etl.oai_client import fetch_page
from etl.parser import parse_oai_xml


def main():
    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)

    conn = get_connection()
    repo_id = 1

    total_processed = 0
    page_num = 1
    last_harvest = get_last_harvest(conn, repo_id)

    if last_harvest:
        from_date = last_harvest.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        from_date = None

    xml_text = fetch_page(from_date=from_date)

    while True:
        # Cuva xml za proveru
        output_file = output_dir / f"fon_page_{page_num}.xml"
        output_file.write_text(xml_text, encoding="utf-8")

        records, token = parse_oai_xml(xml_text)
        print(f"Using from_date: {from_date}")
        print(f"Page {page_num}: {len(records)} records")

        for record in records:
            insert_publication(conn, repo_id, record)
            total_processed += 1

        if not token:
            print("No more pages.")
            break

        print(f"Next token: {token}")

        xml_text = fetch_page(token)
        page_num += 1

    update_last_harvest(conn, repo_id)
    
    conn.close()

    print(f"\nTotal processed into DB: {total_processed}")


if __name__ == "__main__":
    main()