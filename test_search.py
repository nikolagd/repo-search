from search.search import semantic_search

results = semantic_search(
    "semantic web",
    limit=10,
    year_from=2024
)

for row in results:
    print(row)