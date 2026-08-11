import pytest

from microservices.search_service.vector_search import execute_author_search, execute_vector_search, normalize_author_tokens


class Cursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return []


class Connection:
    def __init__(self):
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = Cursor()
        return self.last_cursor


def test_production_vector_search_retains_existing_ordering() -> None:
    connection = Connection()
    execute_vector_search(connection, [1.0], 10, None, None)
    assert "ORDER BY cosine_distance ASC\n" in connection.last_cursor.sql
    assert "ORDER BY ranked.cosine_distance ASC\n" in connection.last_cursor.sql
    assert "cosine_distance ASC, id ASC" not in connection.last_cursor.sql
    assert "WHERE is_active = TRUE" in connection.last_cursor.sql
    assert "MATERIALIZED" not in connection.last_cursor.sql
    assert connection.last_cursor.params == [[1.0], 10]


def test_semantic_year_filters_keep_direct_vector_query_shape() -> None:
    connection = Connection()
    execute_vector_search(connection, [1.0], 10, 2020, 2024)

    assert "MATERIALIZED" not in connection.last_cursor.sql
    assert "FROM publication\n" in connection.last_cursor.sql
    assert "embedding <=> %s::vector" in connection.last_cursor.sql
    assert connection.last_cursor.params == [[1.0], "2020-01-01", "2024-12-31", 10]


def test_evaluation_vector_search_adds_deterministic_id_tie_breaker() -> None:
    connection = Connection()
    execute_vector_search(connection, [1.0], 10, None, None, deterministic_ties=True)
    assert "ORDER BY cosine_distance ASC, id ASC" in connection.last_cursor.sql
    assert "ORDER BY ranked.cosine_distance ASC, ranked.id ASC" in connection.last_cursor.sql


def test_author_filters_use_token_level_all_semantics() -> None:
    connection = Connection()
    execute_vector_search(
        connection,
        [1.0],
        10,
        2020,
        2024,
        ["Ime Prezime", "Drugi Autor"],
        author_match="all",
    )

    assert connection.last_cursor.sql.count("AND EXISTS") == 2
    assert connection.last_cursor.sql.count("repo_search_author_matches") == 2
    assert "WITH filtered AS MATERIALIZED" in connection.last_cursor.sql
    assert connection.last_cursor.params == [
        "2020-01-01",
        "2024-12-31",
        ["ime", "prezime"],
        [False, False],
        ["drugi", "autor"],
        [False, False],
        [1.0],
        10,
    ]


def test_any_author_names_share_one_grouped_exists_condition() -> None:
    connection = Connection()
    execute_vector_search(
        connection,
        [1.0],
        10,
        None,
        None,
        ["Ime Prezime", "Drugi Autor"],
        author_match="any",
    )

    assert connection.last_cursor.sql.count("AND EXISTS") == 1
    assert connection.last_cursor.sql.count("repo_search_author_matches") == 2
    assert " OR " in connection.last_cursor.sql
    assert connection.last_cursor.params == [
        ["ime", "prezime"],
        [False, False],
        ["drugi", "autor"],
        [False, False],
        [1.0],
        10,
    ]


@pytest.mark.parametrize("author_match", ["any", "all"])
def test_selected_ids_obey_author_match(author_match: str) -> None:
    connection = Connection()
    execute_author_search(
        connection, 5, None, None, [], [42, 84], author_match=author_match
    )

    expected_exists = 1 if author_match == "any" else 2
    assert connection.last_cursor.sql.count("AND EXISTS") == expected_exists
    assert connection.last_cursor.params == [42, 84, 5]


@pytest.mark.parametrize("author_match", ["any", "all"])
def test_mixed_name_and_id_use_one_selected_mode(author_match: str) -> None:
    connection = Connection()
    execute_author_search(
        connection,
        5,
        None,
        None,
        ["Ime Prezime"],
        [42],
        author_match=author_match,
    )

    expected_exists = 1 if author_match == "any" else 2
    assert connection.last_cursor.sql.count("AND EXISTS") == expected_exists
    assert connection.last_cursor.params == [["ime", "prezime"], [False, False], 42, 5]


def test_one_author_has_equivalent_parameters_under_either_mode() -> None:
    any_connection = Connection()
    all_connection = Connection()
    execute_author_search(any_connection, 5, None, None, ["Ime Prezime"], author_match="any")
    execute_author_search(all_connection, 5, None, None, ["Ime Prezime"], author_match="all")

    assert any_connection.last_cursor.params == all_connection.last_cursor.params
    assert any_connection.last_cursor.sql.count("repo_search_author_matches") == 1
    assert all_connection.last_cursor.sql.count("repo_search_author_matches") == 1


def test_invalid_author_match_never_reaches_sql() -> None:
    with pytest.raises(ValueError, match="author_match"):
        execute_author_search(
            Connection(), 5, None, None, ["Ime Prezime"], author_match="both"  # type: ignore[arg-type]
        )


def test_author_only_order_and_normalization_are_deterministic() -> None:
    connection = Connection()
    execute_author_search(connection, 5, None, None, ["PREZIME, Čedomir"])

    assert "p.date DESC NULLS LAST" in connection.last_cursor.sql
    assert "lower(COALESCE(p.title, '')) ASC, p.id ASC" in connection.last_cursor.sql
    assert connection.last_cursor.params == [["prezime", "cedomir"], [False, False], 5]
    assert normalize_author_tokens("Đorđe Šarić") == ["djordje", "saric"]


def test_initials_are_conservative_and_selected_ids_are_exact_constraints() -> None:
    connection = Connection()
    execute_author_search(connection, 5, None, None, ["P. Petrović"], [42])

    assert "repo_search_author_matches" in connection.last_cursor.sql
    assert "any_author_pa.author_id = %s" in connection.last_cursor.sql
    assert connection.last_cursor.params == [["p", "petrovic"], [True, False], 42, 5]

    with pytest.raises(ValueError, match="full surname"):
        execute_author_search(connection, 5, None, None, ["P. P."])
