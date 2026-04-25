from search.query_handler import parse_query
from search.search import semantic_search

user_query = "pronadji publikacije o semantickom webu posle 2015"

parsed = parse_query(user_query)
print("PARSED:", parsed)

results = semantic_search(
    query=parsed["semantic_query"],
    limit=10,
    year_from=parsed["year_from"],
    year_to=parsed["year_to"],
    must_terms=parsed["must_terms"]
)

for r in results:
    print("-" * 80)
    print("Title:", r["title"])
    print("Date:", r["date"])
    print("Distance:", round(r["distance"], 4))
    print("Similarity:", round(r["similarity"], 4))
    print("URL:", r["source_url"])