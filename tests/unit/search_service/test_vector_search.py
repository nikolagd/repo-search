from microservices.search_service.vector_search import execute_vector_search


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


def test_evaluation_vector_search_adds_deterministic_id_tie_breaker() -> None:
    connection = Connection()
    execute_vector_search(connection, [1.0], 10, None, None, deterministic_ties=True)
    assert "ORDER BY cosine_distance ASC, id ASC" in connection.last_cursor.sql
    assert "ORDER BY ranked.cosine_distance ASC, ranked.id ASC" in connection.last_cursor.sql
