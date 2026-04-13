from search.search import semantic_search

results = semantic_search("semantic web ontologies", limit=10)

for row in results:
    pub_id, title, abstract, source_url, date, distance = row

    print("-" * 80)
    print("ID:", pub_id)
    print("Title:", title)
    print("Date:", date)
    print("Distance:", distance)
    print("URL:", source_url)