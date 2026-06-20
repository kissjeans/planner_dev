"""Unit tests for seed YAML files (real presales data, plan C1 / §6).

No database or network required — validates structure and internal consistency.
"""

from pathlib import Path

import pytest
import yaml

SEED_DIR = Path(__file__).parent.parent.parent / "seed"

# Working-day windows excluded from the lite template (§6).
STANDARD_ONLY_ORDS = {5, 9, 11, 12}
LITE_ONLY_ORDS = {19}
PAIR_MODES = {"none", "optional", "required"}


def _dep_ord(dep) -> int:
    """A dependency is a bare ord (int) or a mapping with an ``ord`` key."""
    return dep["ord"] if isinstance(dep, dict) else dep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def team() -> dict:
    return yaml.safe_load((SEED_DIR / "team.yaml").read_text())


@pytest.fixture(scope="module")
def tasks_standard() -> dict:
    return yaml.safe_load((SEED_DIR / "tasks_standard.yaml").read_text())


@pytest.fixture(scope="module")
def tasks_lite() -> dict:
    return yaml.safe_load((SEED_DIR / "tasks_lite.yaml").read_text())


@pytest.fixture(scope="module")
def team_names(team) -> set[str]:
    return {p["name"] for p in team["people"]}


def _task(tasks: dict, ord_: int) -> dict:
    return next(t for t in tasks["tasks"] if t["ord"] == ord_)


# ---------------------------------------------------------------------------
# team.yaml
# ---------------------------------------------------------------------------


def test_team_includes_the_real_core_people(team_names):
    core = {"Андрей", "Рай", "Тоня", "Айгуль", "Алиса", "Лёша", "Дизайн"}
    assert core <= team_names


def test_team_has_at_least_one_admin(team):
    assert any(p.get("is_admin") for p in team["people"])


def test_internal_people_have_capacity_8_external_has_zero(team):
    for person in team["people"]:
        if person["is_external"]:
            assert person["capacity_h"] == 0, f"{person['name']} external but capacity != 0"
        else:
            assert person["capacity_h"] == 8, f"{person['name']} unexpected capacity"


def test_design_is_the_external_resource(team):
    design = next(p for p in team["people"] if p["name"] == "Дизайн")
    assert design["is_external"] is True
    assert design["capacity_h"] == 0


def test_team_names_are_unique(team):
    names = [p["name"] for p in team["people"]]
    assert len(names) == len(set(names))


def test_team_required_fields_present(team):
    required = {"name", "role_label", "capacity_h", "is_admin", "is_active", "is_external"}
    for person in team["people"]:
        missing = required - person.keys()
        assert not missing, f"{person.get('name')} missing fields: {missing}"


# ---------------------------------------------------------------------------
# tasks_standard.yaml — tasks 1..18
# ---------------------------------------------------------------------------


def test_standard_is_tasks_1_through_18(tasks_standard):
    ords = sorted(t["ord"] for t in tasks_standard["tasks"])
    assert ords == list(range(1, 19))


def test_standard_deps_reference_valid_ords(tasks_standard):
    valid_ords = {t["ord"] for t in tasks_standard["tasks"]}
    for task in tasks_standard["tasks"]:
        for dep in task.get("depends_on", []):
            assert _dep_ord(dep) in valid_ords, (
                f"Task ord={task['ord']} depends on unknown ord={_dep_ord(dep)}"
            )


def test_standard_deps_no_self_reference(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert task["ord"] not in [_dep_ord(d) for d in task.get("depends_on", [])]


def test_standard_assignees_in_team(tasks_standard, team_names):
    for task in tasks_standard["tasks"]:
        for asgn in task.get("assignees", []):
            assert asgn["name"] in team_names, (
                f"Task ord={task['ord']}: unknown assignee '{asgn['name']}'"
            )


def test_standard_strictness_and_pair_mode_values(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert task.get("pair_mode", "none") in PAIR_MODES
        for asgn in task.get("assignees", []):
            assert asgn["strictness"] in {"A", "B", "C"}


def test_standard_duration_in_range(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert 1 <= task["duration_hours"] <= 40


def test_standard_optional_in_lite_marks_only_5_9_11_12(tasks_standard):
    flagged = {t["ord"] for t in tasks_standard["tasks"] if t.get("optional_in_lite")}
    assert flagged == STANDARD_ONLY_ORDS


# --- acceptance-anchored invariants -----------------------------------------


def test_feedback_task_has_five_working_day_lag(tasks_standard):
    """R1: #18 follows #17 with a 5-working-day lag."""
    deps = _task(tasks_standard, 18)["depends_on"]
    assert deps == [{"ord": 17, "lag_days": 5}]


def test_joint_proofread_is_required_pair_of_tonya_and_alisa(tasks_standard):
    """R2: #16 is strictly Тоня + Алиса together."""
    t16 = _task(tasks_standard, 16)
    assert t16["pair_mode"] == "required"
    assert {a["name"] for a in t16["assignees"]} == {"Тоня", "Алиса"}


def test_design_task_is_a_window_on_external_resource(tasks_standard):
    t12 = _task(tasks_standard, 12)
    assert t12["duration_is_window"] is True
    assert [a["name"] for a in t12["assignees"]] == ["Дизайн"]


def test_priority_executor_is_listed_first(tasks_standard):
    """R3/R4: the starred priority person leads the assignee list (priority 0)."""
    for ord_, lead in [(2, "Тоня"), (4, "Айгуль"), (6, "Тоня"), (9, "Тоня")]:
        first = _task(tasks_standard, ord_)["assignees"][0]
        assert first["name"] == lead and first.get("priority", 0) == 0


# ---------------------------------------------------------------------------
# tasks_lite.yaml
# ---------------------------------------------------------------------------


def test_lite_ord_set_matches_spec(tasks_lite):
    """§6: lite = {1,2,3,4,6,7,8,10,13,14,15,16,17,18,19}."""
    expected = {1, 2, 3, 4, 6, 7, 8, 10, 13, 14, 15, 16, 17, 18, 19}
    assert {t["ord"] for t in tasks_lite["tasks"]} == expected


def test_lite_excludes_standard_only_tasks(tasks_lite):
    ords = {t["ord"] for t in tasks_lite["tasks"]}
    assert ords.isdisjoint(STANDARD_ONLY_ORDS)


def test_lite_includes_notion_rollup(tasks_lite):
    assert LITE_ONLY_ORDS.issubset({t["ord"] for t in tasks_lite["tasks"]})


def test_lite_deps_reference_valid_ords(tasks_lite):
    valid_ords = {t["ord"] for t in tasks_lite["tasks"]}
    for task in tasks_lite["tasks"]:
        for dep in task.get("depends_on", []):
            assert _dep_ord(dep) in valid_ords, (
                f"Lite task ord={task['ord']} depends on unknown ord={_dep_ord(dep)}"
            )


def test_lite_drops_proofread_dependency_on_design(tasks_lite):
    """R5: #16 keeps its required pair but loses its #12 dependency in lite."""
    t16 = _task(tasks_lite, 16)
    assert t16["depends_on"] == []
    assert t16["pair_mode"] == "required"


def test_lite_assignees_in_team(tasks_lite, team_names):
    for task in tasks_lite["tasks"]:
        for asgn in task.get("assignees", []):
            assert asgn["name"] in team_names
