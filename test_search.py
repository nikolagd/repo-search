from search.search import semantic_search

results = semantic_search("ontologije", limit=10, year_from=2025)

for r in results:
    print("-" * 80)
    print("ID:", r["id"])
    print("Title:", r["title"])
    print("Date:", r["date"])
    print("Distance:", round(r["distance"], 4))
    print("URL:", r["source_url"])