from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import Response, status

from microservices.common.schemas import HealthResponse, LivenessResponse, ReadinessResponse

HEALTH_OK = "ok"
HEALTH_UNAVAILABLE = "unavailable"


def check_database(connection_factory: Callable[[], Any]) -> str:
    connection = None
    database_status = HEALTH_UNAVAILABLE
    try:
        connection = connection_factory()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database_status = HEALTH_OK
    except Exception:
        database_status = HEALTH_UNAVAILABLE
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                database_status = HEALTH_UNAVAILABLE
    return database_status


def aggregate_status(dependencies: Mapping[str, str]) -> str:
    return (
        HEALTH_OK
        if all(dependency_status == HEALTH_OK for dependency_status in dependencies.values())
        else HEALTH_UNAVAILABLE
    )


def build_liveness_response() -> LivenessResponse:
    return LivenessResponse(status=HEALTH_OK)


def build_readiness_response(
    response: Response,
    dependencies: Mapping[str, str],
) -> ReadinessResponse:
    readiness_status = aggregate_status(dependencies)
    response.status_code = (
        status.HTTP_200_OK
        if readiness_status == HEALTH_OK
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return ReadinessResponse(status=readiness_status, dependencies=dict(dependencies))


def build_health_response(
    database: str,
    dependencies: Mapping[str, str],
) -> HealthResponse:
    return HealthResponse(status=aggregate_status(dependencies), database=database)
