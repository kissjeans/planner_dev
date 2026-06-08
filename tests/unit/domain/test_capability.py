"""Unit tests for capability-based assignee suggestion (spec section 5)."""

from uuid import uuid4

from planner.domain.capability import (
    AssigneeSuggestion,
    Candidate,
    suggest_assignees,
)


def _candidate(name, skills, *, external=False, load=0):
    return Candidate(
        person_id=uuid4(),
        name=name,
        skills=frozenset(skills),
        is_external=external,
        load_hours=load,
    )


def test_full_coverage_ranks_above_partial():
    full = _candidate("Full", {"Копирайтинг", "Редактура"})
    partial = _candidate("Partial", {"Копирайтинг"})
    out = suggest_assignees(["Копирайтинг", "Редактура"], [partial, full])
    assert [s.name for s in out] == ["Full", "Partial"]
    assert out[0].coverage == 1.0
    assert out[1].coverage == 0.5


def test_covered_and_missing_skills_reported():
    c = _candidate("X", {"Копирайтинг"})
    (s,) = suggest_assignees(["Копирайтинг", "Редактура"], [c])
    assert s.covered_skills == ("Копирайтинг",)
    assert s.missing_skills == ("Редактура",)


def test_matching_is_case_insensitive():
    c = _candidate("X", {"копирайтинг"})
    (s,) = suggest_assignees(["  КОПИРАЙТИНГ "], [c])
    assert s.coverage == 1.0
    assert s.missing_skills == ()


def test_ties_break_by_lower_load_then_name():
    a = _candidate("Борис", {"S"}, load=5)
    b = _candidate("Анна", {"S"}, load=5)
    c = _candidate("Вера", {"S"}, load=2)
    out = suggest_assignees(["S"], [a, b, c])
    # all full coverage -> freer first (Вера load=2), then name asc among equal load
    assert [s.name for s in out] == ["Вера", "Анна", "Борис"]


def test_external_excluded_by_default():
    ext = _candidate("Катя", {"Дизайн макетов"}, external=True)
    out = suggest_assignees(["Дизайн макетов"], [ext])
    assert out == ()


def test_external_included_when_flagged():
    ext = _candidate("Катя", {"Дизайн макетов"}, external=True)
    out = suggest_assignees(["Дизайн макетов"], [ext], include_external=True)
    assert [s.name for s in out] == ["Катя"]


def test_no_required_skills_ranks_by_load_with_full_coverage():
    a = _candidate("A", set(), load=9)
    b = _candidate("B", set(), load=1)
    out = suggest_assignees([], [a, b])
    assert [s.name for s in out] == ["B", "A"]
    assert all(s.coverage == 1.0 for s in out)


def test_limit_truncates_ranked_list():
    cands = [_candidate(f"P{i}", {"S"}, load=i) for i in range(5)]
    out = suggest_assignees(["S"], cands, limit=2)
    assert len(out) == 2
    assert [s.name for s in out] == ["P0", "P1"]


def test_returns_assignee_suggestion_instances():
    c = _candidate("X", {"S"})
    (s,) = suggest_assignees(["S"], [c])
    assert isinstance(s, AssigneeSuggestion)
    assert s.person_id == c.person_id
