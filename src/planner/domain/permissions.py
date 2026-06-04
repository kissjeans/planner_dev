"""Role rules (spec section 16): writes are admin-only, reads are open."""

from __future__ import annotations

from planner.domain.intent import WRITE_KINDS


def can_execute(kind: str, is_admin: bool) -> bool:
    """Return True if an actor with ``is_admin`` may run an intent of ``kind``."""
    if kind in WRITE_KINDS:
        return is_admin
    return True
