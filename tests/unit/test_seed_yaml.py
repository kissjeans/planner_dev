"""Unit tests for seed YAML files.

No database or network required — validates structure and internal consistency.
"""

from pathlib import Path

import pytest
import yaml

SEED_DIR = Path(__file__).parent.parent.parent / "seed"


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


# ---------------------------------------------------------------------------
# team.yaml
# ---------------------------------------------------------------------------


def test_team_has_six_people(team):
    assert len(team["people"]) == 6


def test_team_has_at_least_one_admin(team):
    admins = [p for p in team["people"] if p.get("is_admin")]
    assert len(admins) >= 1


def test_team_all_have_capacity_8(team):
    for person in team["people"]:
        assert person["capacity_h"] == 8, f"{person['name']} has unexpected capacity"


def test_team_names_are_unique(team):
    names = [p["name"] for p in team["people"]]
    assert len(names) == len(set(names))


def test_team_required_fields_present(team):
    required = {"name", "role_label", "capacity_h", "is_admin", "is_active", "is_external"}
    for person in team["people"]:
        missing = required - person.keys()
        assert not missing, f"{person.get('name')} missing fields: {missing}"


# ---------------------------------------------------------------------------
# tasks_standard.yaml
# ---------------------------------------------------------------------------


def test_standard_has_exactly_20_tasks(tasks_standard):
    assert len(tasks_standard["tasks"]) == 20


def test_standard_ord_sequence(tasks_standard):
    ords = [t["ord"] for t in tasks_standard["tasks"]]
    assert sorted(ords) == list(range(1, 21))


def test_standard_deps_reference_valid_ords(tasks_standard):
    valid_ords = {t["ord"] for t in tasks_standard["tasks"]}
    for task in tasks_standard["tasks"]:
        for dep in task.get("depends_on", []):
            assert dep in valid_ords, (
                f"Task ord={task['ord']} depends on unknown ord={dep}"
            )


def test_standard_deps_no_self_reference(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert task["ord"] not in task.get("depends_on", []), (
            f"Task ord={task['ord']} depends on itself"
        )


def test_standard_assignees_in_team(tasks_standard, team_names):
    for task in tasks_standard["tasks"]:
        for asgn in task.get("assignees", []):
            assert asgn["name"] in team_names, (
                f"Task ord={task['ord']}: unknown assignee '{asgn['name']}'"
            )


def test_standard_strictness_values(tasks_standard):
    valid = {"A", "B", "C"}
    for task in tasks_standard["tasks"]:
        for asgn in task.get("assignees", []):
            assert asgn["strictness"] in valid, (
                f"Task ord={task['ord']}: invalid strictness '{asgn['strictness']}'"
            )


def test_standard_duration_in_range(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert 1 <= task["duration_hours"] <= 40, (
            f"Task ord={task['ord']} has unusual duration {task['duration_hours']}h"
        )


def test_standard_no_optional_in_lite(tasks_standard):
    """All standard tasks have optional_in_lite=False."""
    for task in tasks_standard["tasks"]:
        assert task.get("optional_in_lite", False) is False, (
            f"Task ord={task['ord']} unexpectedly marked optional_in_lite"
        )


# ---------------------------------------------------------------------------
# tasks_lite.yaml
# ---------------------------------------------------------------------------


def test_lite_has_10_to_12_tasks(tasks_lite):
    count = len(tasks_lite["tasks"])
    assert 10 <= count <= 12, f"Expected 10-12 tasks, got {count}"


def test_lite_ord_are_unique(tasks_lite):
    ords = [t["ord"] for t in tasks_lite["tasks"]]
    assert len(ords) == len(set(ords)), "Duplicate ord values in lite template"


def test_lite_deps_reference_valid_ords(tasks_lite):
    valid_ords = {t["ord"] for t in tasks_lite["tasks"]}
    for task in tasks_lite["tasks"]:
        for dep in task.get("depends_on", []):
            assert dep in valid_ords, (
                f"Lite task ord={task['ord']} depends on unknown ord={dep}"
            )


def test_lite_deps_no_self_reference(tasks_lite):
    for task in tasks_lite["tasks"]:
        assert task["ord"] not in task.get("depends_on", []), (
            f"Lite task ord={task['ord']} depends on itself"
        )


def test_lite_assignees_in_team(tasks_lite, team_names):
    for task in tasks_lite["tasks"]:
        for asgn in task.get("assignees", []):
            assert asgn["name"] in team_names, (
                f"Lite task ord={task['ord']}: unknown assignee '{asgn['name']}'"
            )


def test_lite_strictness_values(tasks_lite):
    valid = {"A", "B", "C"}
    for task in tasks_lite["tasks"]:
        for asgn in task.get("assignees", []):
            assert asgn["strictness"] in valid, (
                f"Lite task ord={task['ord']}: invalid strictness '{asgn['strictness']}'"
            )


def test_lite_has_some_optional_tasks(tasks_lite):
    optional = [t for t in tasks_lite["tasks"] if t.get("optional_in_lite")]
    assert len(optional) >= 1, "Lite template should have at least one optional task"
