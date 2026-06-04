"""Unit tests for the write-gate (spec section 16)."""

from planner.domain.permissions import can_execute


def test_writes_require_admin():
    assert can_execute("add_project", is_admin=False) is False
    assert can_execute("add_project", is_admin=True) is True


def test_reads_open_to_everyone():
    assert can_execute("load", is_admin=False) is True
    assert can_execute("clarify", is_admin=False) is True
