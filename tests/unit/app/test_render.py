"""Unit tests for PNG renderers and LoadSummaryUseCase (spec 7.4)."""

from datetime import date
from uuid import uuid4

from planner.app.load_summary import LoadSummaryUseCase
from planner.app.render.gantt import render_gantt
from planner.app.render.heatmap import render_heatmap
from planner.domain.models import Assignment, DayAllocation, Person

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
START = date(2026, 6, 1)


def test_heatmap_returns_png():
    png = render_heatmap(["A", "B"], [START], [[4], [10]], capacity=8)
    assert png.startswith(_PNG_MAGIC)


def test_heatmap_colorbar_labelled_in_days_not_hours():
    """Spec §6: the load scale is shown to users in working days, not hours."""
    from planner.app.render.heatmap import COLORBAR_LABEL

    assert "дн" in COLORBAR_LABEL  # days
    assert "час" not in COLORBAR_LABEL  # not "часы"


def test_gantt_returns_png():
    a = Assignment(uuid4(), uuid4(), START, date(2026, 6, 3), allocations=())
    png = render_gantt([a], START)
    assert png.startswith(_PNG_MAGIC)


def test_load_summary_aggregates_and_renders():
    p = Person(id=uuid4(), name="Андрей", capacity_h=8)
    allocs = [
        DayAllocation(p.id, START, 8),
        DayAllocation(p.id, date(2026, 6, 2), 4),
    ]
    png = LoadSummaryUseCase().execute([p], allocs, START, days=14)
    assert png.startswith(_PNG_MAGIC)


def _boom(_a):
    raise ValueError("boom")


def test_render_gantt_closes_figure_on_error():
    # regression (plan 024): an exception mid-render must still close the figure
    # (finally), so pyplot's global registry does not leak.
    import matplotlib.pyplot as plt
    import pytest

    before = set(plt.get_fignums())
    a = Assignment(uuid4(), uuid4(), START, date(2026, 6, 3), allocations=())
    with pytest.raises(ValueError):
        render_gantt([a], START, label_for=_boom)
    assert set(plt.get_fignums()) == before  # no figure leaked
