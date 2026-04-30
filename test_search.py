import argparse


def parse_args():
    parser = argparse.ArgumentParser(description="Run a publication search query.")
    parser.add_argument(
        "query",
        nargs="+",
        help="Natural-language search query.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results to print.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    from search.query_handler import parse_query
    from search.search import semantic_search

    user_query = " ".join(args.query)

    parsed = parse_query(user_query)
    print("PARSED:", parsed)
    print("INTERPRETED:", parsed["interpreted_query"])

    results = semantic_search(
        embedding_queries=parsed["embedding_queries"],
        limit=args.limit,
        year_from=parsed["year_from"],
        year_to=parsed["year_to"],
        topic_phrases=parsed["topic_phrases"],
        ranking_phrases=parsed["ranking_phrases"],
    )

    for r in results:
        print("-" * 80)
        print("Title:", r["title"])
        print("Date:", r["date"])
        print("Similarity:", round(r["similarity"], 4))
        print("Topic boost:", round(r["topic_boost"], 4))
        print("Ranking boost:", round(r["ranking_boost"], 4))
        print("Coverage boost:", round(r["coverage_boost"], 4))
        print("Score:", round(r["score"], 4))
        print("URL:", r["source_url"])
        print("Matched query:", r["matched_query"])
        print("Best rank:", r["best_rank"])
        print("Matched queries:", r["matched_queries"])


if __name__ == "__main__":
    main()
