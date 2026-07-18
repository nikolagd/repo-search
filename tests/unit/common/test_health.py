from __future__ import annotations

from fastapi import Response, status

from microservices.common.health import (
    HEALTH_OK,
    HEALTH_UNAVAILABLE,
    build_health_response,
    build_liveness_response,
    build_readiness_response,
    check_database,
)


class DatabaseCursor:
    def __init__(self, *, fail_query: bool = False) -> None:
        self.fail_query = fail_query
        self.executed: list[str] = []

    def __enter__(self) -> "DatabaseCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str) -> None:
        self.executed.append(statement)
        if self.fail_query:
            raise RuntimeError("database query failed")

    def fetchone(self) -> tuple[int]:
        return (1,)


class DatabaseConnection:
    def __init__(self, *, fail_query: bool = False, fail_close: bool = False) -> None:
        self.cursor_instance = DatabaseCursor(fail_query=fail_query)
        self.fail_close = fail_close
        self.closed = False

    def cursor(self) -> DatabaseCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("database close failed")


def test_check_database_runs_lightweight_query_and_closes_connection() -> None:
    connection = DatabaseConnection()

    result = check_database(lambda: connection)

    assert result == HEALTH_OK
    assert connection.cursor_instance.executed == ["SELECT 1"]
    assert connection.closed is True


def test_check_database_reports_connection_failure() -> None:
    def unavailable_connection() -> DatabaseConnection:
        raise RuntimeError("database connection failed")

    assert check_database(unavailable_connection) == HEALTH_UNAVAILABLE


def test_check_database_reports_query_failure_and_still_closes_connection() -> None:
    connection = DatabaseConnection(fail_query=True)

    result = check_database(lambda: connection)

    assert result == HEALTH_UNAVAILABLE
    assert connection.closed is True


def test_check_database_reports_close_failure() -> None:
    connection = DatabaseConnection(fail_close=True)

    assert check_database(lambda: connection) == HEALTH_UNAVAILABLE


def test_health_response_builders_keep_liveness_separate_from_readiness() -> None:
    assert build_liveness_response().model_dump() == {"status": HEALTH_OK}

    ready_response = Response()
    ready = build_readiness_response(ready_response, {"database": HEALTH_OK})
    assert ready_response.status_code == status.HTTP_200_OK
    assert ready.model_dump() == {"status": HEALTH_OK, "dependencies": {"database": HEALTH_OK}}

    unavailable_response = Response()
    unavailable = build_readiness_response(
        unavailable_response,
        {"database": HEALTH_UNAVAILABLE},
    )
    assert unavailable_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert unavailable.model_dump() == {
        "status": HEALTH_UNAVAILABLE,
        "dependencies": {"database": HEALTH_UNAVAILABLE},
    }

    compatibility = build_health_response(
        HEALTH_UNAVAILABLE,
        {"database": HEALTH_UNAVAILABLE},
    )
    assert compatibility.model_dump() == {
        "status": HEALTH_UNAVAILABLE,
        "database": HEALTH_UNAVAILABLE,
    }
