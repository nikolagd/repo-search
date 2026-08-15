from evaluation.language_aware_query_coverage import diagnose_top_five_coverage
from evaluation.models import EvaluationQuery, QueryMetadata, QueryRun, RetrievedItem


def test_coverage_audit_is_word_only_and_label_blind() -> None:
    queries = [EvaluationQuery("q1", "korišćenje internet platformi")]
    metadata = [QueryMetadata("q1", "Serbian", "Latin", "test", "test")]
    publications = [
        {
            "id": "one",
            "title": "Korišćenje",
            "abstract": None,
        },
        {
            "id": "two",
            "title": "Korišćenje internet",
            "abstract": None,
        },
    ]
    runs = [
        QueryRun(
            "q1",
            "language_aware_lexical",
            [
                RetrievedItem("one", 0.5, "Korišćenje", None),
                RetrievedItem("two", 0.4, "Korišćenje internet", None),
            ],
        )
    ]

    report = diagnose_top_five_coverage(runs, queries, metadata, publications)

    assert report["top_five_positions"] == 2
    assert report["one_content_concept_result_count"] == 1
    assert all(row["coverage_uses_character_ngrams"] is False for row in report["rows"])
    assert report["rows"][0]["matched_concept_count"] == 1
    assert report["rows"][1]["matched_concept_count"] == 2
