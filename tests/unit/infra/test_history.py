"""Tests for ChatHistory in-process per-chat message buffer."""

from __future__ import annotations

from planner.infra.history import ChatHistory


def test_recent_empty_when_no_messages():
    h = ChatHistory()
    assert h.recent(1) == ()


def test_record_then_recent_oldest_to_newest():
    h = ChatHistory()
    h.record(1, "first")
    h.record(1, "second")
    assert h.recent(1) == ("first", "second")


def test_record_strips_and_ignores_empty():
    h = ChatHistory()
    h.record(1, "  spaced  ")
    h.record(1, "   ")
    h.record(1, "")
    assert h.recent(1) == ("spaced",)


def test_maxlen_evicts_oldest():
    h = ChatHistory(max_turns=2)
    h.record(1, "a")
    h.record(1, "b")
    h.record(1, "c")
    assert h.recent(1) == ("b", "c")


def test_history_is_per_chat():
    h = ChatHistory()
    h.record(1, "chat-one")
    h.record(2, "chat-two")
    assert h.recent(1) == ("chat-one",)
    assert h.recent(2) == ("chat-two",)
