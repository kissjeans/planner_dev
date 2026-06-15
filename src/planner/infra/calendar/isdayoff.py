"""Live isdayoff.ru calendar adapter (spec section 10 / TZ section 5).

isdayoff.ru returns a per-day bitstring for a whole year: ``0`` working,
``1`` weekend/holiday, ``2`` short pre-holiday day, ``4`` working day moved
off-schedule. The solver is synchronous (spec calendar port), so this async
adapter pre-resolves the year's holidays into a :class:`SnapshotCalendar`.

Fallback chain (spec 10): SnapshotCalendar is the always-available offline base;
this adapter refreshes its holiday set when the API is reachable. The caller
keeps the snapshot when the fetch fails — the network error propagates here.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from planner.infra.calendar.snapshot import SnapshotCalendar

ISDAYOFF_URL = "https://isdayoff.ru/api/getdata"
_OFF_CODE = "1"  # 1 = weekend/holiday; 2 (short) and 4 (moved) stay working


def parse_year_offdays(year: int, data: str) -> frozenset[date]:
    """Map an isdayoff year bitstring to the set of off-days it marks."""
    start = date(year, 1, 1)
    return frozenset(
        start + timedelta(days=i) for i, ch in enumerate(data.strip()) if ch == _OFF_CODE
    )


def holidays_from_offdays(offdays: frozenset[date]) -> frozenset[date]:
    """Keep only weekday off-days as holidays; weekends are the calendar's job."""
    return frozenset(d for d in offdays if d.weekday() < 5)


class IsDayOffClient:
    """Async fetcher for the isdayoff.ru production calendar."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_year_offdays(self, year: int) -> frozenset[date]:
        resp = await self._client.get(ISDAYOFF_URL, params={"year": year})
        resp.raise_for_status()
        return parse_year_offdays(year, resp.text)

    async def build_snapshot(self, year: int) -> SnapshotCalendar:
        """Fetch the year and return a snapshot calendar seeded with its holidays."""
        offdays = await self.fetch_year_offdays(year)
        return SnapshotCalendar(holidays_from_offdays(offdays))


_HTTP_TIMEOUT_S = 10.0


async def fetch_snapshot_for_years(years: tuple[int, ...]) -> SnapshotCalendar:
    """Fetch holiday data for several years and merge into one snapshot.

    Opens its own bounded-timeout client; raises httpx errors to the caller —
    the caller decides whether to fall back to the offline snapshot.
    """
    holidays: frozenset[date] = frozenset()
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        idc = IsDayOffClient(client)
        for year in years:
            offdays = await idc.fetch_year_offdays(year)
            holidays = holidays | holidays_from_offdays(offdays)
    return SnapshotCalendar(holidays)
