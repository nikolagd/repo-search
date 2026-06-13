from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from microservices.common.observability import current_trace_ids, utc_timestamp

LOGGER_NAME = "repo_search.application"


def _logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def app_observability_log_query_text() -> bool:
    return os.getenv("APP_OBSERVABILITY_LOG_QUERY_TEXT", "false").strip().lower() in {"1", "true", "yes", "on"}


def emit_app_event(event: str, service: str, **fields: Any) -> None:
    trace_fields = current_trace_ids()
    payload = {
        "timestamp": utc_timestamp(),
        "service": service,
        "event": event,
        **trace_fields,
        **{key: value for key, value in fields.items() if value is not None},
    }
    _logger().info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
