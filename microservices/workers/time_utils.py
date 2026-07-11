from datetime import datetime, timezone

FULL_TIMESTAMP_GRANULARITY = "YYYY-MM-DDThh:mm:ssZ"


def utc_now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_oai_from_date(last_harvest, granularity):
    if last_harvest is None:
        return None
    if granularity == FULL_TIMESTAMP_GRANULARITY:
        return to_utc(last_harvest).strftime("%Y-%m-%dT%H:%M:%SZ")
    return last_harvest.strftime("%Y-%m-%d")
