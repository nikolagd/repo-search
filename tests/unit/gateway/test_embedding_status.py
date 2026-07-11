from microservices.gateway.main import build_admin_embedding_status


def test_admin_embedding_status_reports_current_missing_and_stale_counts() -> None:
    result = build_admin_embedding_status(
        10,
        {
            "publications_with_embeddings": 8,
            "current_embeddings": 6,
            "missing_embeddings": 2,
            "stale_embeddings": 2,
        },
        [{"id": 7}],
    )
    assert result == {
        "current_embeddings": 6,
        "missing_embeddings": 2,
        "stale_embeddings": 2,
        "embedding_job": {"id": 7},
    }
