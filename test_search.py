from search.search import semantic_search
from search.parser import parse_query

user_query = "radovi o ontologijama posle 2024"

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