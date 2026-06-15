"""Slot model for the admin UI (client xlsx vision).

Hybrid model: the DB stores hours, the UI shows *slots*. 1 slot = half a day =
:data:`SLOT_HOURS` hours; a full presale day = 2 slots = 8 h. Design (external)
runs at 4 slots/day. Conversion lives only here so every view agrees.
"""

from __future__ import annotations

import math

SLOT_HOURS = 4  # 1 slot = half a working day


def hours_to_slots(hours: float) -> int:
    """Round hours up to whole slots (a partial slot still occupies the slot).

    Accepts fractional hours: callers aggregate real per-day load (which can be
    fractional) and convert once here, so the ceil is not defeated by an early
    integer round.
    """
    if hours <= 0:
        return 0
    return math.ceil(hours / SLOT_HOURS)
