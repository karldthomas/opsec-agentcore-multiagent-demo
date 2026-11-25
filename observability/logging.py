"""Correlation-aware JSON logging without leaking hidden scenario state."""

from __future__ import annotations

import json
import logging
from typing import Any

from contracts.models import _validate_no_hidden_keys


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("run_id", "request_id", "task_id", "agent", "tool", "trace_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        extra = getattr(record, "details", None)
        if isinstance(extra, dict):
            _validate_no_hidden_keys(extra, "log.details")
            payload["details"] = extra
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("opsfusion")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger
