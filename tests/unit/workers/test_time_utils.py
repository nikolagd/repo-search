from __future__ import annotations

from datetime import datetime, timedelta, timezone

from microservices.workers.time_utils import FULL_TIMESTAMP_GRANULARITY, format_oai_from_date


def test_format_oai_from_date_preserves_none() -> None:
    assert format_oai_from_date(None, FULL_TIMESTAMP_GRANULARITY) is None


def test_format_oai_from_date_converts_aware_datetime_to_utc_timestamp() -> None:
    source = datetime(2024, 3, 1, 1, 30, 45, tzinfo=timezone(timedelta(hours=2)))

    assert format_oai_from_date(source, FULL_TIMESTAMP_GRANULARITY) == "2024-02-29T23:30:45Z"


def test_format_oai_from_date_treats_naive_database_timestamp_as_utc() -> None:
    source = datetime(2024, 3, 1, 1, 30, 45)

    assert format_oai_from_date(source, FULL_TIMESTAMP_GRANULARITY) == "2024-03-01T01:30:45Z"


def test_format_oai_from_date_uses_date_for_day_granularity() -> None:
    source = datetime(2024, 3, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))

    assert format_oai_from_date(source, "YYYY-MM-DD") == "2024-03-01"
