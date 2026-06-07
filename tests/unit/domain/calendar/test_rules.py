"""Unit tests for the weekend calendar and working-day helpers."""

from datetime import date

from planner.domain.calendar.rules import (
    WeekendCalendar,
    first_working_day,
    nth_working_day,
)

MON = date(2026, 6, 1)  # Monday
SAT = date(2026, 6, 6)  # Saturday
SUN = date(2026, 6, 7)  # Sunday


def test_weekday_is_working():
    assert WeekendCalendar().is_working_day(MON) is True


def test_weekend_is_not_working():
    cal = WeekendCalendar()
    assert cal.is_working_day(SAT) is False
    assert cal.is_working_day(SUN) is False


def test_holiday_is_not_working():
    cal = WeekendCalendar(frozenset({MON}))
    assert cal.is_working_day(MON) is False


def test_next_working_day_skips_weekend():
    cal = WeekendCalendar()
    # Friday 2026-06-05 -> next working day is Monday 2026-06-08
    assert cal.next_working_day(date(2026, 6, 5)) == date(2026, 6, 8)


def test_business_days_between_one_week():
    cal = WeekendCalendar()
    # Mon..next Mon exclusive = 5 working days
    assert cal.business_days_between(MON, date(2026, 6, 8)) == 5


def test_first_working_day_on_weekend_rolls_forward():
    cal = WeekendCalendar()
    assert first_working_day(cal, SAT) == date(2026, 6, 8)


def test_first_working_day_on_workday_is_same():
    cal = WeekendCalendar()
    assert first_working_day(cal, MON) == MON


def test_nth_working_day_counts_inclusive():
    cal = WeekendCalendar()
    # 5th working day from Monday = Friday 2026-06-05
    assert nth_working_day(cal, MON, 5) == date(2026, 6, 5)
    # 6th skips the weekend to Monday
    assert nth_working_day(cal, MON, 6) == date(2026, 6, 8)


def test_business_days_between_zero_when_reversed():
    cal = WeekendCalendar()
    assert cal.business_days_between(MON, MON) == 0
    assert cal.business_days_between(date(2026, 6, 5), MON) == 0


def test_nth_working_day_raises_on_zero():
    import pytest
    cal = WeekendCalendar()
    with pytest.raises(ValueError, match="n must be >= 1"):
        nth_working_day(cal, MON, 0)
