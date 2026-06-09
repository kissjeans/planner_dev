"""LoadSummaryUseCase (spec section 7.4): 14-day load heatmap PNG."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from planner.app.render.heatmap import render_heatmap
from planner.domain.models import DayAllocation, Person

DEFAULT_DAYS = 14


class LoadSummaryUseCase:
    def execute(
        self,
        people: list[Person],
        allocations: list[DayAllocation],
        start: date,
        days: int = DEFAULT_DAYS,
    ) -> bytes:
        day_list = [start + timedelta(days=i) for i in range(days)]
        index = {d: i for i, d in enumerate(day_list)}

        used: dict[tuple[UUID, int], int] = defaultdict(int)
        for a in allocations:
            if a.day in index:
                used[(a.person_id, index[a.day])] += a.hours

        labels = [p.name for p in people]
        matrix = [
            [used[(p.id, j)] for j in range(days)] for p in people
        ]
        capacity = people[0].capacity_h if people else 8
        return render_heatmap(labels, day_list, matrix, capacity=capacity)
