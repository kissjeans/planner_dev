"""Gantt timeline PNG for a plan (spec section 7.4 / 4.5)."""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from planner.domain.models import Assignment


def render_gantt(
    assignments: list[Assignment],
    origin: date,
    label_for: Callable[[Assignment], str] | None = None,
) -> bytes:
    """Render task bars positioned by day-offset from ``origin``."""
    label_for = label_for or (lambda a: str(a.task_id)[:8])
    fig, ax = plt.subplots(figsize=(8, max(2, len(assignments) * 0.45)))

    try:
        for i, a in enumerate(assignments):
            start = (a.start_date - origin).days
            length = max((a.end_date - a.start_date).days + 1, 1)
            ax.barh(i, length, left=start, height=0.6, color="#4C78A8")
            ax.text(start + length / 2, i, label_for(a), ha="center", va="center",
                    color="white", fontsize=8)

        ax.set_yticks(range(len(assignments)))
        ax.set_yticklabels([label_for(a) for a in assignments])
        ax.invert_yaxis()
        ax.set_xlabel(f"дни от {origin.strftime('%d.%m.%Y')}")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110)
        return buf.getvalue()
    finally:
        plt.close(fig)
