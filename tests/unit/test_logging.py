"""Unit tests for correlation-id logging helpers (spec 6.1)."""

from planner.infra.logging import (
    _add_correlation,
    configure_logging,
    correlation_id,
    new_correlation_id,
)


def test_new_correlation_id_is_short_hex():
    cid = new_correlation_id()
    assert len(cid) == 12
    assert correlation_id.get() == cid
    int(cid, 16)  # raises if not hex


def test_add_correlation_injects_when_set():
    new_correlation_id()
    out = _add_correlation(None, "info", {"event": "x"})
    assert out["correlation_id"] == correlation_id.get()


def test_add_correlation_omitted_when_empty():
    correlation_id.set("")
    out = _add_correlation(None, "info", {"event": "x"})
    assert "correlation_id" not in out


def test_configure_logging_runs():
    configure_logging(json_logs=True, level="INFO")
    configure_logging(json_logs=False, level="DEBUG")
