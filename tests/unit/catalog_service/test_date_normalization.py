from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from microservices.catalog_service.main import normalize_date


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("2024", datetime(2024, 1, 1)),
        ("2024-05", datetime(2024, 5, 1)),
        ("2024-05-06", datetime(2024, 5, 6)),
        ("  2024-05-06T07:08:09Z  ", datetime(2024, 5, 6, 7, 8, 9)),
        ("2024-05-06T07:08:09.123456", datetime(2024, 5, 6, 7, 8, 9, 123456)),
        (
            "2024-05-06T07:08:09+02:00",
            datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone(timedelta(hours=2))),
        ),
        ("2024-99-99", None),
        ("not-a-date", None),
    ],
)
def test_normalize_date_handles_supported_and_invalid_values(raw_value, expected) -> None:
    assert normalize_date(raw_value) == expected
