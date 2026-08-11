from __future__ import annotations

import pytest
from pydantic import ValidationError

from microservices.common.schemas import SearchRequest


def test_search_request_accepts_topic_author_or_both() -> None:
    assert SearchRequest(query=" topic ").query == "topic"
    assert SearchRequest(author_names=["  Ime   Prezime  "]).author_names == ["Ime Prezime"]
    request = SearchRequest(query="topic", author_names=["Ime Prezime"])
    assert (request.query, request.author_names) == ("topic", ["Ime Prezime"])


def test_search_request_rejects_empty_blank_invalid_and_excessive_authors() -> None:
    with pytest.raises(ValidationError, match="nonblank query"):
        SearchRequest()
    with pytest.raises(ValidationError, match="must not be blank"):
        SearchRequest(author_names=["   "])
    with pytest.raises(ValidationError, match="at least one letter"):
        SearchRequest(author_names=["..."])
    with pytest.raises(ValidationError, match="must be strings"):
        SearchRequest(author_names=[42])  # type: ignore[list-item]
    with pytest.raises(ValidationError, match="at most 10"):
        SearchRequest(author_names=[f"Author {index}" for index in range(11)])
    with pytest.raises(ValidationError, match="200 characters"):
        SearchRequest(author_names=["a" * 201])


def test_search_request_deduplicates_explicit_authors_case_insensitively() -> None:
    request = SearchRequest(author_names=["Ime Prezime", " ime prezime ", "Drugi Autor"])

    assert request.author_names == ["Ime Prezime", "Drugi Autor"]


def test_search_request_accepts_deduplicates_and_validates_selected_author_ids() -> None:
    request = SearchRequest(author_ids=[3, 3, 7])
    assert request.author_ids == [3, 7]

    for invalid in ([0], [-1], [True], ["3"]):
        with pytest.raises(ValidationError):
            SearchRequest(author_ids=invalid)  # type: ignore[arg-type]


def test_search_request_rejects_initial_only_author_filters() -> None:
    with pytest.raises(ValidationError, match="full surname"):
        SearchRequest(author_names=["P. P."])
