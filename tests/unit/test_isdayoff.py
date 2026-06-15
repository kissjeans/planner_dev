"""Unit tests for the isdayoff.ru calendar adapter (spec section 10)."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from planner.infra.calendar.isdayoff import (
    IsDayOffClient,
    holidays_from_offdays,
    parse_year_offdays,
)


def _year_bitstring(year: int, off_days: set[date]) -> str:
    """Build a 0/1 string for the whole year with the given days marked off."""
    start = date(year, 1, 1)
    days = (date(year, 12, 31) - start).days + 1
    return "".join(
        "1" if (start + timedelta(days=i)) in off_days else "0" for i in range(days)
    )


def test_parse_year_offdays_marks_listed_days():
    offs = {date(2026, 1, 1), date(2026, 6, 12)}
    data = _year_bitstring(2026, offs)
    assert parse_year_offdays(2026, data) == frozenset(offs)


def test_parse_ignores_short_and_moved_codes():
    # index 0 = short day (2), index 1 = moved working (4) -> neither is off
    assert parse_year_offdays(2026, "24000") == frozenset()


def test_holidays_from_offdays_drops_weekends():
    sat = date(2026, 6, 13)  # Saturday
    holiday = date(2026, 6, 12)  # Friday, public holiday
    assert holidays_from_offdays(frozenset({sat, holiday})) == frozenset({holiday})


@pytest.mark.asyncio
async def test_build_snapshot_marks_holiday_nonworking():
    offs = {date(2026, 1, 1), date(2026, 6, 12)}
    data = _year_bitstring(2026, offs)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["year"] == "2026"
        return httpx.Response(200, text=data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cal = await IsDayOffClient(client).build_snapshot(2026)

    assert cal.is_working_day(date(2026, 6, 12)) is False  # holiday
    assert cal.is_working_day(date(2026, 6, 11)) is True  # ordinary Thursday


@pytest.mark.asyncio
async def test_fetch_raises_on_http_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await IsDayOffClient(client).fetch_year_offdays(2026)


@pytest.mark.asyncio
async def test_fetch_snapshot_for_years_merges_years(monkeypatch):
    from planner.infra.calendar import isdayoff as mod

    async def fake_fetch(self, year):  # type: ignore[no-untyped-def]
        return frozenset({date(year, 3, 2)})  # one weekday off-day per year

    monkeypatch.setattr(mod.IsDayOffClient, "fetch_year_offdays", fake_fetch)
    cal = await mod.fetch_snapshot_for_years((2026, 2027))
    assert not cal.is_working_day(date(2026, 3, 2))
    assert not cal.is_working_day(date(2027, 3, 2))
