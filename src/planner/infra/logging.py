"""Structlog JSON logging with a per-request correlation id (spec 6.1)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    """Generate and bind a short correlation id for the current context."""
    cid = uuid.uuid4().hex[:12]
    correlation_id.set(cid)
    return cid


def _add_correlation(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    cid = correlation_id.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(json_logs: bool = True, level: str = "INFO") -> None:
    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_correlation,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        cache_logger_on_first_use=True,
    )
