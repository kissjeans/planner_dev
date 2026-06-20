"""C2 solver extensions: dependency lag, executor priority, required pair, window.

Acceptance refs: R1 (lag), R2 (required pair), R3 (priority executor).
"""

from datetime import date
from uuid import uuid4

from planner.domain.calendar.rules import WeekendCalendar, nth_working_day
from planner.domain.models import (
    DayAllocation,
    DayOverride,
    Dependency,
    Person,
    PlanRequest,
    Task,
)
from planner.domain.solver.greedy import GreedySolver

START = date(2026, 6, 1)  # Monday
CAL = WeekendCalendar()


def _person(name="P", cap=8):
    return Person(id=uuid4(), name=name, capacity_h=cap)


def _task(person_ids, hours=8, name="T", **kw):
    return Task(
        id=uuid4(),
        name=name,
        duration_hours=hours,
        allowed_person_ids=tuple(person_ids),
        **kw,
    )


def _solve(people, tasks, deps=(), **kw):
    req = PlanRequest(
        people=tuple(people),
        tasks=tuple(tasks),
        dependencies=tuple(deps),
        horizon_start=START,
        **kw,
    )
    return GreedySolver(CAL).plan(req)


# --- R1: FS dependency with a working-day lag -------------------------------

def test_fs_lag_starts_successor_n_working_days_after_predecessor():
    p = _person()
    t0 = _task([p.id], name="t0")
    t1 = _task([p.id], name="t1")
    dep = Dependency(t1.id, t0.id, "FS", lag_working_days=5)
    res = _solve([p], [t0, t1], [dep])
    by = res.by_task()
    end0 = by[t0.id].end_date
    # successor starts on the 5th working day after the predecessor's end
    assert by[t1.id].start_date == nth_working_day(CAL, CAL.next_working_day(end0), 5)
    # for START=Mon Jun1, that is Mon Jun8
    assert by[t1.id].start_date == date(2026, 6, 8)


def test_fs_no_lag_is_unchanged_next_working_day():
    p = _person()
    t0, t1 = _task([p.id], name="t0"), _task([p.id], name="t1")
    dep = Dependency(t1.id, t0.id, "FS")  # lag defaults to 0
    res = _solve([p], [t0, t1], [dep])
    by = res.by_task()
    assert by[t1.id].start_date == CAL.next_working_day(by[t0.id].end_date)


# --- R3: priority executor ---------------------------------------------------

def test_priority_executor_taken_first_when_free():
    pri, other = _person("pri"), _person("other")
    t = _task([pri.id, other.id])  # pri listed first = higher priority
    res = _solve([pri, other], [t])
    assert res.by_task()[t.id].person_id == pri.id


def test_priority_falls_through_when_blocked():
    pri, other = _person("pri"), _person("other")
    t = _task([pri.id, other.id])
    # pri is on vacation the only early slot; other is free at START
    override = DayOverride(person_id=pri.id, day=START, capacity_h=0)
    res = _solve([pri, other], [t], day_overrides=(override,))
    a = res.by_task()[t.id]
    assert a.person_id == other.id
    assert a.start_date == START


# --- R2: required pair -------------------------------------------------------

def test_required_pair_places_both_same_day_no_speedup():
    p1, p2 = _person("p1"), _person("p2")
    t = _task([p1.id, p2.id], hours=8, pair_mode="required")
    res = _solve([p1, p2], [t])
    a = res.by_task()[t.id]
    # one calendar day, both people allocated that day at full duration
    assert a.start_date == a.end_date
    days = {al.day for al in a.allocations}
    people = {al.person_id for al in a.allocations}
    assert days == {a.start_date}
    assert people == {p1.id, p2.id}
    assert all(al.hours == 8 for al in a.allocations)
    assert res.overloads() == ()


def test_required_pair_waits_until_both_free():
    p1, p2 = _person("p1"), _person("p2")
    t = _task([p1.id, p2.id], hours=8, pair_mode="required")
    # p1 fully booked on START via an existing allocation → pair must wait a day
    busy = (DayAllocation(p1.id, START, 8),)
    res = _solve([p1, p2], [t], existing_allocations=busy)
    a = res.by_task()[t.id]
    assert a.start_date == CAL.next_working_day(START)


# --- window / external resource ----------------------------------------------

def test_window_task_spans_days_without_consuming_capacity():
    ext = _person("Дизайн", cap=0)
    t = _task([ext.id], hours=16, duration_is_window=True)  # ~2 working days
    res = _solve([ext], [t])
    a = res.by_task()[t.id]
    assert a.start_date == START
    assert a.end_date == nth_working_day(CAL, START, 2)  # spans 2 working days
    assert a.allocations == ()  # external resource burns no team capacity
    assert res.overloads() == ()


def test_window_successor_starts_after_window_end():
    ext = _person("Дизайн", cap=0)
    p = _person("p")
    w = _task([ext.id], hours=16, name="design", duration_is_window=True)
    nxt = _task([p.id], name="after")
    dep = Dependency(nxt.id, w.id, "FS")
    res = _solve([ext, p], [w, nxt], [dep])
    by = res.by_task()
    assert by[nxt.id].start_date == CAL.next_working_day(by[w.id].end_date)
