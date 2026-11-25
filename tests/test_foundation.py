from __future__ import annotations

import json
import logging

import pytest

from observability.logging import JsonLogFormatter, configure_logging
from simulator.persistence import JsonRunStore, PersistenceError


def test_json_run_store_round_trips_atomically(tmp_path) -> None:
    store = JsonRunStore(tmp_path)
    store.save("run-1", {"stage": "parallel_investigation", "version": 2})
    assert store.load("run-1")["version"] == 2
    with pytest.raises(PersistenceError, match="safe"):
        store.save("../escape", {})


def test_structured_formatter_rejects_hidden_fields() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord("opsfusion", logging.INFO, __file__, 1, "hello", (), None)
    record.details = {"hidden_root_cause": "not allowed"}
    with pytest.raises(ValueError, match="hidden evaluator"):
        formatter.format(record)


def test_configure_logging_is_idempotent() -> None:
    logger = configure_logging()
    before = len(logger.handlers)
    assert configure_logging().handlers
    assert len(logger.handlers) == before
