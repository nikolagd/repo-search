from __future__ import annotations

import pytest

from microservices.common.author_names import (
    author_name_key,
    canonicalize_author_name,
    parse_author_query,
    rank_author_suggestions,
)


@pytest.mark.parametrize(
    ("cyrillic", "latin"),
    [
        ("Ђорђе Шарић", "Djordje Saric"),
        ("Љиљана Његован", "Ljiljana Njegovan"),
        ("Џејн Човић", "Džejn Čović"),
        ("Његош Ђурић", "Njegoš Djurić"),
    ],
)
def test_serbian_cyrillic_latin_digraph_and_diacritic_equivalence(
    cyrillic: str,
    latin: str,
) -> None:
    assert canonicalize_author_name(cyrillic) == canonicalize_author_name(latin)


def test_punctuation_spacing_and_reversed_order_have_stable_keys() -> None:
    assert canonicalize_author_name("  Petrović,\tPetar. ") == "petrovic petar"
    assert author_name_key("Petar Petrović") == author_name_key("Petrović, Petar")


@pytest.mark.parametrize("value", ["P. Petrović", "P Petrović", "Petrović P."])
def test_single_initial_is_marked_only_with_a_full_token(value: str) -> None:
    query = parse_author_query(value)
    assert query.tokens in {("p", "petrovic"), ("petrovic", "p")}
    assert query.initials in {(True, False), (False, True)}


def test_broad_initials_are_rejected_and_short_tokens_are_exact() -> None:
    with pytest.raises(ValueError, match="full surname"):
        parse_author_query("P. P.")
    with pytest.raises(ValueError, match="full surname"):
        parse_author_query("P")

    assert parse_author_query("Pe Petrović").initials == (False, False)


def test_typo_suggestion_ranking_is_deterministic_and_keeps_variants_separate() -> None:
    candidates = [
        {"id": 4, "display_name": "Petra Petrović", "publication_count": 9},
        {"id": 2, "display_name": "Petar Petrović", "publication_count": 3},
        {"id": 7, "display_name": "Petar Jovanović", "publication_count": 20},
        {"id": 9, "display_name": "Petar Petrovic", "publication_count": 3},
    ]

    ranked = rank_author_suggestions("Petar Petrovci", candidates, 3)

    assert [item["id"] for item in ranked] == [9, 2, 4]
    assert [item["display_name"] for item in ranked[:2]] == ["Petar Petrovic", "Petar Petrović"]
