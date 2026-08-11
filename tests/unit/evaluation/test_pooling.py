from evaluation.models import QueryRun, RetrievedItem
from evaluation.pooling import build_candidate_pool


def test_candidate_pool_deduplicates_randomizes_and_blinds_methods() -> None:
    runs = [
        QueryRun("q1", "bm25", [RetrievedItem("1", 3, "One"), RetrievedItem("2", 2, "Two")]),
        QueryRun("q1", "vector_only", [RetrievedItem("2", 0.9, "Two"), RetrievedItem("3", 0.8, "Three")]),
        QueryRun("q1", "full_pipeline", [RetrievedItem("1", 1.1, "One")], parser_mode="llm"),
    ]

    first = build_candidate_pool(runs, depth=2, seed=42, query_texts={"q1": "Synthetic query"})
    second = build_candidate_pool(runs, depth=2, seed=42, query_texts={"q1": "Synthetic query"})

    assert first == second
    assert {row["publication_id"] for row in first} == {"1", "2", "3"}
    assert len(first) == 3
    assert all("method" not in row and "score" not in row and "rank" not in row for row in first)
    assert all(row["query_text"] == "Synthetic query" for row in first)
    assert [row["candidate_id"] for row in first] == ["q1-C0001", "q1-C0002", "q1-C0003"]
