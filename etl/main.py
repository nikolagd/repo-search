from pathlib import Path

from etl.db import get_connection, insert_publication
from etl.oai_client import fetch_first_page
from etl.parser import parse_oai_xml


def main():
    xml_text = fetch_first_page()

    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "fon_listrecords_page1.xml"
    output_file.write_text(xml_text, encoding="utf-8")

    records, token = parse_oai_xml(xml_text)

    conn = get_connection()
    repo_id = 1  # make sure your FON repository row already exists

    inserted = 0
    for record in records:
        insert_publication(conn, repo_id, record)
        inserted += 1

    conn.close()

    print(f"Parsed records: {len(records)}")
    print(f"Resumption token: {token}")
    print(f"Processed records into DB: {inserted}")


if __name__ == "__main__":
    main()