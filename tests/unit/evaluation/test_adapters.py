import asyncio

import pytest

from evaluation.adapters import (
    BM25BaselineAdapter,
    FullPipelineAdapter,
    KeywordBaselineAdapter,
    LanguageAwareLexicalAdapter,
    LanguageIndependentLexicalAdapter,
    language_aware_analysis,
    language_aware_comparison_tokens,
    language_aware_lexical_metadata,
    VectorOnlyAdapter,
    language_independent_character_ngrams,
    language_independent_lexical_metadata,
    language_independent_word_tokens,
    language_route,
    normalize_serbian_latin,
    normalize_serbian_latin_folded,
    tokenize,
)
from evaluation.models import EvaluationQuery, QueryMetadata


def test_keyword_baseline_ranking_is_deterministic() -> None:
    corpus = [
        {"id": "2", "title": "Open repositories", "abstract": "open science"},
        {"id": "1", "title": "Open science", "abstract": "repositories repositories"},
        {"id": "3", "title": "Unrelated", "abstract": "other"},
    ]
    adapter = KeywordBaselineAdapter(corpus)

    first = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "open repositories"), 10))
    second = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "open repositories"), 10))

    assert [item.publication_id for item in first.results] == ["2", "1"]
    assert [item.score for item in first.results] == [5.0, 4.0]
    assert [(item.publication_id, item.score) for item in first.results] == [
        (item.publication_id, item.score) for item in second.results
    ]


def test_tokenization_uses_nfkc_casefold_for_serbian_latin_and_cyrillic() -> None:
    assert tokenize("\uff36E\u0160TA\u010cKA \u041f\u0420\u0418\u041c\u0415\u041d\u0410") == [
        "ve\u0161ta\u010dka",
        "\u043f\u0440\u0438\u043c\u0435\u043d\u0430",
    ]


def test_bm25_ranking_and_publication_id_tie_break_are_deterministic() -> None:
    corpus = [
        {"id": "2", "title": "Ista tema", "abstract": None},
        {"id": "1", "title": "ISTA TEMA", "abstract": None},
        {"id": "3", "title": "Drugo", "abstract": None},
    ]
    adapter = BM25BaselineAdapter(corpus)

    first = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "ista tema"), 10))
    second = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "ista tema"), 10))

    assert [item.publication_id for item in first.results] == ["1", "2"]
    assert [item.score for item in first.results] == pytest.approx(
        [item.score for item in second.results]
    )


def test_bm25_document_frequency_and_length_normalization_affect_ranking() -> None:
    corpus = [
        {"id": "rare", "title": None, "abstract": "common rare"},
        {"id": "short", "title": None, "abstract": "common"},
        {"id": "long", "title": None, "abstract": "common " + "noise " * 30},
        {"id": "other", "title": None, "abstract": "common other"},
    ]
    adapter = BM25BaselineAdapter(corpus)

    mixed = asyncio.run(adapter.retrieve(EvaluationQuery("q1", "common rare"), 10))
    common = asyncio.run(adapter.retrieve(EvaluationQuery("q2", "common"), 10))

    assert mixed.results[0].publication_id == "rare"
    assert [item.publication_id for item in common.results].index("short") < [
        item.publication_id for item in common.results
    ].index("long")


def test_common_terms_do_not_dominate_bm25_like_legacy_frequency_baseline() -> None:
    corpus = [
        {"id": "relevant", "title": None, "abstract": "i u za retka"},
        {"id": "long", "title": None, "abstract": "i u za " * 40},
        {"id": "other-1", "title": None, "abstract": "i u za drugo"},
        {"id": "other-2", "title": None, "abstract": "i u za trece"},
    ]
    query = EvaluationQuery("q1", "i u za retka")

    legacy = asyncio.run(KeywordBaselineAdapter(corpus).retrieve(query, 10))
    bm25 = asyncio.run(BM25BaselineAdapter(corpus).retrieve(query, 10))

    assert legacy.results[0].publication_id == "long"
    assert bm25.results[0].publication_id == "relevant"


def test_bm25_title_score_has_documented_two_times_boost() -> None:
    corpus = [
        {"id": "title", "title": "repozitorijum", "abstract": None},
        {"id": "abstract", "title": None, "abstract": "repozitorijum"},
    ]
    run = asyncio.run(BM25BaselineAdapter(corpus).retrieve(EvaluationQuery("q1", "repozitorijum"), 10))

    assert [item.publication_id for item in run.results] == ["title", "abstract"]
    assert run.results[0].score == pytest.approx(2 * run.results[1].score)


def test_language_independent_analysis_is_unicode_aware_and_preserves_scripts() -> None:
    text = "\uff21I, VE\u0160TA\u010cKA; \u0412\u0415\u0428\u0422\u0410\u0427\u041a\u0410 na\u00efve \u0111ak!"

    assert language_independent_word_tokens(text) == [
        "ai",
        "ve\u0161ta\u010dka",
        "\u0432\u0435\u0448\u0442\u0430\u0447\u043a\u0430",
        "na\u00efve",
        "\u0111ak",
    ]
    assert language_independent_character_ngrams("AI C++ ime") == ["ai", "c", "ime"]
    assert language_independent_character_ngrams("repo-search") == [
        "repo",
        "sear",
        "earc",
        "arch",
    ]


def test_language_independent_analysis_handles_compatibility_and_combining_marks() -> None:
    assert language_independent_word_tokens("\uff36E\u0160TA\u010cKA") == ["ve\u0161ta\u010dka"]
    assert language_independent_word_tokens("A\u030A") == ["\u00e5"]
    assert language_independent_word_tokens(None) == []
    assert language_independent_character_ngrams("") == []
    with pytest.raises(ValueError, match="positive integer"):
        language_independent_character_ngrams("tekst", size=0)


def test_language_independent_lexical_ranking_is_deterministic_for_mixed_text() -> None:
    corpus = [
        {
            "id": "2",
            "title": "Digitalni repozitorijumi",
            "abstract": "open science and research data",
        },
        {
            "id": "1",
            "title": "\u0414\u0438\u0433\u0438\u0442\u0430\u043b\u043d\u0438 \u0440\u0435\u043f\u043e\u0437\u0438\u0442\u043e\u0440\u0438\u0458\u0443\u043c\u0438",
            "abstract": "\u043e\u0442\u0432\u043e\u0440\u0435\u043d\u0430 \u043d\u0430\u0443\u043a\u0430",
        },
        {"id": "3", "title": "AI", "abstract": None},
        {"id": "4", "title": "Nepovezano", "abstract": "druga tema"},
    ]
    adapter = LanguageIndependentLexicalAdapter(corpus)
    query = EvaluationQuery("q1", "digitalni repozitorijumi open science")

    first = asyncio.run(adapter.retrieve(query, 10))
    second = asyncio.run(adapter.retrieve(query, 10))

    assert first.method == "language_independent_lexical"
    assert first.results[0].publication_id == "2"
    assert [(item.publication_id, item.score) for item in first.results] == [
        (item.publication_id, item.score) for item in second.results
    ]
    assert all(item.publication_id != "1" for item in first.results)


def test_language_independent_lexical_handles_short_query_and_missing_abstract() -> None:
    corpus = [
        {"id": "2", "title": "AI", "abstract": None},
        {"id": "1", "title": "AI", "abstract": ""},
        {"id": "3", "title": "ML", "abstract": None},
    ]
    run = asyncio.run(
        LanguageIndependentLexicalAdapter(corpus).retrieve(EvaluationQuery("q1", "AI"), 5)
    )

    assert [item.publication_id for item in run.results] == ["1", "2"]


def _language_metadata(query_id: str, language: str, script: str) -> QueryMetadata:
    return QueryMetadata(query_id, language, script, "test", "test")


def test_serbian_normalization_covers_precise_and_ascii_comparison_examples() -> None:
    precise = normalize_serbian_latin(
        "Milić Čačak veštačka učenje Đorđević машинско учење"
    )
    folded = normalize_serbian_latin_folded(
        "Milić Čačak veštačka učenje Đorđević машинско учење"
    )

    assert precise == "milić čačak veštačka učenje đorđević mašinsko učenje"
    assert folded == "milic cacak vestacka ucenje djordjevic masinsko ucenje"


def test_language_aware_analysis_preserves_original_tokens_and_adds_folded_tokens() -> None:
    analysis = language_aware_analysis("машинско учење", "serbian")

    assert analysis["original_word_tokens"] == ["машинско", "учење"]
    assert analysis["canonical_serbian_tokens"] == ["mašinsko", "učenje"]
    assert analysis["diacritic_insensitive_tokens"] == ["masinsko", "ucenje"]
    assert analysis["original_word_tokens"] != analysis["diacritic_insensitive_tokens"]
    assert set(analysis["diacritic_insensitive_tokens"]).issubset(
        set(analysis["comparison_tokens"])
    )


def test_language_aware_analysis_preserves_repeated_source_occurrences() -> None:
    serbian = language_aware_analysis("učenje učenje", "serbian")
    assert serbian["comparison_tokens"].count("učenje") == 2
    assert serbian["comparison_tokens"].count("ucenje") == 2
    assert serbian["comparison_tokens"].count("učenj") == 2

    english = language_aware_analysis("AI AI", "english")
    assert english["comparison_tokens"] == ["ai", "ai"]


def test_language_aware_serbian_cyrillic_and_latin_variants_match() -> None:
    corpus = [
        {"id": "cyr", "title": "Машинско учење", "abstract": None},
        {"id": "latin", "title": "masinsko ucenje", "abstract": None},
        {"id": "other", "title": "Turizam", "abstract": None},
    ]
    run = asyncio.run(
        LanguageAwareLexicalAdapter(corpus).retrieve(
            EvaluationQuery("q1", "машинско учење"),
            3,
            query_metadata=_language_metadata("q1", "Serbian", "Cyrillic"),
        )
    )

    assert {item.publication_id for item in run.results[:2]} == {"cyr", "latin"}


def test_language_aware_stemming_routes_serbian_and_english_inflections() -> None:
    serbian_tokens = language_aware_comparison_tokens("učenja", "serbian")
    english_tokens = language_aware_comparison_tokens("applications", "english")

    assert "učenj" in serbian_tokens
    assert "applic" in english_tokens

    corpus = [
        {"id": "sr", "title": "Učenje", "abstract": None},
        {"id": "en", "title": "Application", "abstract": None},
    ]
    sr_run = asyncio.run(
        LanguageAwareLexicalAdapter(corpus).retrieve(
            EvaluationQuery("q-sr", "učenja"),
            2,
            query_metadata=_language_metadata("q-sr", "Serbian", "Latin"),
        )
    )
    en_run = asyncio.run(
        LanguageAwareLexicalAdapter(corpus).retrieve(
            EvaluationQuery("q-en", "applications"),
            2,
            query_metadata=_language_metadata("q-en", "English", "Latin"),
        )
    )

    assert sr_run.results[0].publication_id == "sr"
    assert en_run.results[0].publication_id == "en"


def test_language_aware_mixed_route_is_explicit_and_uses_both_stemmers() -> None:
    metadata = _language_metadata("q30", "Serbian_mixed", "Latin")
    assert language_route(metadata) == "mixed"
    tokens = language_aware_comparison_tokens("obradi applications", "mixed")

    assert "obrad" in tokens
    assert "applic" in tokens


def test_language_aware_short_terms_missing_abstracts_and_ties_are_deterministic() -> None:
    corpus = [
        {"id": "2", "title": "AI", "abstract": None},
        {"id": "1", "title": "AI", "abstract": ""},
        {"id": "3", "title": "ML", "abstract": None},
    ]
    adapter = LanguageAwareLexicalAdapter(corpus)
    query = EvaluationQuery("q1", "AI")
    metadata = _language_metadata("q1", "English", "Latin")
    first = asyncio.run(adapter.retrieve(query, 5, query_metadata=metadata))
    second = asyncio.run(adapter.retrieve(query, 5, query_metadata=metadata))

    assert [item.publication_id for item in first.results] == ["1", "2"]
    assert [(item.publication_id, item.score) for item in first.results] == [
        (item.publication_id, item.score) for item in second.results
    ]


def test_language_aware_does_not_treat_unrelated_words_as_synonyms() -> None:
    assert not set(language_aware_comparison_tokens("učenje", "serbian")).intersection(
        language_aware_comparison_tokens("turizam", "serbian")
    )
    run = asyncio.run(
        LanguageAwareLexicalAdapter(
            [{"id": "unrelated", "title": "Turizam", "abstract": None}]
        ).retrieve(
            EvaluationQuery("q1", "učenje"),
            5,
            query_metadata=_language_metadata("q1", "Serbian", "Latin"),
        )
    )
    assert run.results == []


def test_language_independent_metadata_is_complete_and_explicitly_non_cross_lingual() -> None:
    metadata = language_independent_lexical_metadata()

    assert metadata["method_id"] == "language_independent_lexical"
    assert metadata["bm25_parameters"] == {"k1": 1.2, "b": 0.75}
    assert metadata["character_ngrams"]["minimum_n"] == 4
    assert metadata["fusion"] == {
        "method": "reciprocal_rank_fusion",
        "k": 60,
        "components": ["word_bm25", "character_4gram_bm25"],
        "component_weights": "equal; one reciprocal-rank contribution per component",
        "missing_document_contribution": 0.0,
    }
    assert metadata["semantic_components"] == []
    assert metadata["cross_lingual_retrieval"] is False
    assert metadata["cross_language_mapping"] is None


def test_language_aware_metadata_records_stemmer_routing_and_fusion_completely() -> None:
    metadata = language_aware_lexical_metadata()

    assert metadata["method_id"] == "language_aware_lexical"
    assert metadata["stemmer_library"] == "snowballstemmer"
    assert metadata["stemmer_library_version"] == "3.1.1"
    assert metadata["stemmer_algorithms"] == {"serbian": "serbian", "english": "english"}
    assert metadata["query_routing"]["source"].endswith("no LLM detection")
    assert metadata["precise_original_channel"]["folded_forms_replace_original"] is False
    assert metadata["fusion"]["k"] == 60
    assert metadata["fusion"]["component_weights"].startswith("equal;")
    assert metadata["serbian_diacritic_insensitive_mapping"]["\u0111"] == "dj"
    assert metadata["comparison_channel"]["document_level_deduplication"] is False
    assert "repeated source-token occurrences remain repeated" in (
        metadata["comparison_channel"]["token_occurrence_policy"]
    )
    assert metadata["comparison_channel"]["synonyms"] is None
    assert metadata["comparison_channel"]["lemmatization"] is None


def test_vector_adapter_uses_original_query_and_existing_fetch_shape() -> None:
    calls = []

    async def embed(text):
        calls.append(("embed", text))
        return [0.1, 0.2]

    def fetch(vector, limit, year_from, year_to):
        calls.append(("fetch", vector, limit, year_from, year_to))
        return [(7, "Title", "Abstract", "https://example.test/7", None, 0.25, "Repo", [])]

    run = asyncio.run(VectorOnlyAdapter(embed, fetch).retrieve(EvaluationQuery("q1", "semantic query"), 5))

    assert calls == [("embed", "semantic query"), ("fetch", [0.1, 0.2], 5, None, None)]
    assert run.method == "vector_only"
    assert run.results[0].publication_id == "7"
    assert run.results[0].score == 0.75


def test_vector_adapter_accepts_similarity_dict_without_eager_distance_lookup() -> None:
    run = asyncio.run(
        VectorOnlyAdapter(
            lambda _text: [0.1],
            lambda *_args: [{"id": 4, "cosine_similarity": 0.8, "title": "Result"}],
        ).retrieve(EvaluationQuery("q1", "query"), 1)
    )
    assert run.results[0].publication_id == "4"
    assert run.results[0].score == 0.8


def test_full_pipeline_adapter_preserves_parser_mode_from_mocked_service() -> None:
    async def search(text, limit):
        assert (text, limit) == ("query", 3)
        return {
            "plan": {"parser_mode": "fallback_service_error"},
            "results": [{"id": 9, "score": 0.8, "title": "Result", "abstract": None}],
        }

    run = asyncio.run(FullPipelineAdapter(search).retrieve(EvaluationQuery("q1", "query"), 3))

    assert run.parser_mode == "fallback_service_error"
    assert [item.publication_id for item in run.results] == ["9"]
