from search.search import semantic_search
from search.query_handler import parse_query

user_query = "pronadji radove o semantickom vebu posle 2019"

parsed = parse_query(user_query)

print("PARSED:", parsed)

results = semantic_search(
    parsed["semantic_query"],
    limit=10,
    year_from=parsed["year_from"],
    year_to=parsed["year_to"],
)

for r in results:
    print("-" * 80)
    print("Title:", r["title"])
    print("Date:", r["date"])
    print("Distance:", round(r["distance"], 4))
    print("URL:", r["source_url"])
    