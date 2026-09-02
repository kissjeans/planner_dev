"""Unit tests for seed YAML files (customer update 2026-08).

No database or network required — validates structure and internal consistency
against docs/customer-update-2026-08.md and docs/customer-variants-2026-08.md.
"""

from pathlib import Path

import pytest
import yaml

SEED_DIR = Path(__file__).parent.parent.parent / "seed"

PAIR_MODES = {"none", "optional", "required", "each"}
# Milestones: the send-off and the client feedback carry no work, only waiting.
MILESTONE_ORDS = {21, 22}
# Hours on these are paid by EACH assignee, not shared (async review / meeting).
PER_PERSON_ORDS = {6, 20}


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
def variants() -> dict:
    return yaml.safe_load((SEED_DIR / "variants.yaml").read_text())


@pytest.fixture(scope="module")
def team_names(team) -> set[str]:
    return {p["name"] for p in team["people"]}


def _task(tasks: dict, ord_: int) -> dict:
    return next(t for t in tasks["tasks"] if t["ord"] == ord_)


# ---------------------------------------------------------------------------
# team.yaml
# ---------------------------------------------------------------------------


def test_team_includes_the_real_core_people(team_names):
    core = {"Андрей", "Рай", "Тоня", "Айгуль", "Алиса", "Антон", "Даня", "Дизайн"}
    assert core <= team_names


def test_lyosha_was_replaced_by_anton(team_names):
    """Лёша is off the roster; Антон carries the technical tasks now."""
    assert "Лёша" not in team_names
    assert "Антон" in team_names


def test_team_has_at_least_one_admin(team):
    assert any(p["is_admin"] for p in team["people"])


def test_capacity_is_per_person_not_a_flat_eight(team):
    """Presale is not a full day for everyone (customer update §1)."""
    expected = {
        "Андрей": 4,
        "Рай": 4,
        "Тоня": 8,
        "Айгуль": 6,
        "Алиса": 8,
        "Антон": 4,
        "Даня": 4,
        "Дизайн": 4,
    }
    actual = {p["name"]: p["capacity_h"] for p in team["people"]}
    assert actual == expected


def test_designer_is_no_longer_external(team):
    """The designer now consumes team capacity like everyone else."""
    designer = next(p for p in team["people"] if p["name"] == "Дизайн")
    assert designer["is_external"] is False
    assert designer["capacity_h"] > 0


def test_team_names_are_unique(team):
    names = [p["name"] for p in team["people"]]
    assert len(names) == len(set(names))


def test_team_required_fields_present(team):
    for person in team["people"]:
        for field in ("name", "role_label", "capacity_h", "is_admin", "is_active"):
            assert field in person, f"{person.get('name')} missing {field}"


# ---------------------------------------------------------------------------
# tasks_standard.yaml
# ---------------------------------------------------------------------------


def test_standard_is_tasks_1_through_22(tasks_standard):
    assert {t["ord"] for t in tasks_standard["tasks"]} == set(range(1, 23))


def test_standard_deps_reference_valid_ords(tasks_standard):
    valid = {t["ord"] for t in tasks_standard["tasks"]}
    for task in tasks_standard["tasks"]:
        for dep in task.get("depends_on", []):
            assert _dep_ord(dep) in valid, f"#{task['ord']} -> {dep}"


def test_standard_deps_no_self_reference(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert task["ord"] not in {_dep_ord(d) for d in task.get("depends_on", [])}


def test_standard_assignees_in_team(tasks_standard, team_names):
    for task in tasks_standard["tasks"]:
        for a in task["assignees"]:
            assert a["name"] in team_names, f"#{task['ord']}: {a['name']}"


def test_standard_strictness_and_pair_mode_values(tasks_standard):
    for task in tasks_standard["tasks"]:
        assert task["pair_mode"] in PAIR_MODES
        for a in task["assignees"]:
            assert a["strictness"] in {"A", "B", "C"}


def test_milestones_have_zero_duration(tasks_standard):
    """Sending and client feedback are milestones, not work (customer §2)."""
    for ord_ in MILESTONE_ORDS:
        assert _task(tasks_standard, ord_)["duration_hours"] == 0


def test_working_tasks_have_positive_duration(tasks_standard):
    for task in tasks_standard["tasks"]:
        if task["ord"] in MILESTONE_ORDS:
            continue
        assert 1 <= task["duration_hours"] <= 8, f"#{task['ord']}"


def test_feedback_task_has_five_working_day_lag(tasks_standard):
    """Client feedback starts 5 working days after the proposal is sent."""
    deps = _task(tasks_standard, 22)["depends_on"]
    assert deps == [{"ord": 21, "lag_days": 5}]


def test_anton_estimate_is_waited_on_for_one_day(tasks_standard):
    """Отправка waits 1 working day on Антон's answer (customer §3)."""
    deps = _task(tasks_standard, 21)["depends_on"]
    assert {"ord": 17, "lag_days": 1} in deps


def test_anton_estimate_starts_early_to_absorb_the_wait(tasks_standard):
    """#17 hangs off the draft (#5), not off the late chain, so the wait
    is absorbed by other work instead of extending the deadline."""
    assert _task(tasks_standard, 17)["depends_on"] == [5]


def test_joint_proofread_is_required_pair_of_tonya_and_alisa(tasks_standard):
    t20 = _task(tasks_standard, 20)
    assert t20["pair_mode"] == "required"
    assert {a["name"] for a in t20["assignees"]} == {"Тоня", "Алиса"}


def test_design_is_ordinary_work_not_a_window(tasks_standard):
    """The designer is scheduled against capacity now, not as a fixed window."""
    t15 = _task(tasks_standard, 15)
    assert t15.get("duration_is_window") is not True
    assert t15["duration_hours"] == 8
    assert [a["name"] for a in t15["assignees"]] == ["Дизайн"]


def test_reference_cases_have_no_predecessor(tasks_standard):
    """#7 can be done any time, but must be ready before the assembly (#14)."""
    assert _task(tasks_standard, 7)["depends_on"] == []
    assert 7 in {_dep_ord(d) for d in _task(tasks_standard, 14)["depends_on"]}


def test_send_off_waits_for_every_deliverable(tasks_standard):
    """Everything the client sees must be finished before #21."""
    deps = {_dep_ord(d) for d in _task(tasks_standard, 21)["depends_on"]}
    assert deps == {10, 11, 12, 13, 15, 17, 19, 20}


def test_priority_executor_is_listed_first(tasks_standard):
    for task in tasks_standard["tasks"]:
        priorities = [a["priority"] for a in task["assignees"]]
        assert priorities == sorted(priorities), f"#{task['ord']}"


# ---------------------------------------------------------------------------
# variants.yaml
# ---------------------------------------------------------------------------


def _variant_hours(variant: dict, base: dict, per_person_counts: dict) -> int:
    excluded, overrides = set(variant["excluded"]), variant["hours"]
    total = 0
    for ord_, hours in base.items():
        if ord_ in excluded:
            continue
        h = overrides.get(ord_, hours)
        total += h * per_person_counts.get(ord_, 1)
    return total


def test_three_variants_are_defined(variants):
    assert [v["code"] for v in variants["variants"]] == ["max", "mid", "min"]


def test_variant_hours_match_the_customer_targets(variants, tasks_standard):
    """Each variant's hours add up to the number the customer signed off."""
    base = {t["ord"]: t["duration_hours"] for t in tasks_standard["tasks"]}
    counts = {6: 4, 20: 2}  # people who each pay the task's hours
    for variant in variants["variants"]:
        assert _variant_hours(variant, base, counts) == variant["target_hours"], (
            f"variant {variant['code']}"
        )


def test_variant_excluded_and_overridden_ords_exist(variants, tasks_standard):
    valid = {t["ord"] for t in tasks_standard["tasks"]}
    for variant in variants["variants"]:
        assert set(variant["excluded"]) <= valid
        assert set(variant["hours"]) <= valid
        assert set(variant.get("renames", {})) <= valid


def test_per_person_tasks_are_declared(variants):
    assert set(variants["per_person_tasks"]) == PER_PERSON_ORDS


def test_per_person_tasks_use_a_multi_assignee_mode(tasks_standard):
    """Hours paid by each assignee need a mode that charges them all.

    #6 is an asynchronous review ("each"), #20 is a meeting ("required").
    A plain "none" would bill a single person and lose the other hours.
    """
    assert _task(tasks_standard, 6)["pair_mode"] == "each"
    assert _task(tasks_standard, 20)["pair_mode"] == "required"


def test_max_variant_uses_the_base_template_unchanged(variants):
    mx = next(v for v in variants["variants"] if v["code"] == "max")
    assert mx["excluded"] == [] and mx["hours"] == {}


# ---------------------------------------------------------------------------
# tasks_lite.yaml — generated from the "min" variant
# ---------------------------------------------------------------------------


def test_lite_matches_the_min_variant_scope(tasks_lite, variants, tasks_standard):
    mn = next(v for v in variants["variants"] if v["code"] == "min")
    expected = {t["ord"] for t in tasks_standard["tasks"]} - set(mn["excluded"])
    assert {t["ord"] for t in tasks_lite["tasks"]} == expected


def test_lite_applies_the_min_variant_hours(tasks_lite, variants):
    mn = next(v for v in variants["variants"] if v["code"] == "min")
    for ord_, hours in mn["hours"].items():
        assert _task(tasks_lite, ord_)["duration_hours"] == hours


def test_lite_renames_the_assembly_to_notion(tasks_lite):
    assert _task(tasks_lite, 14)["name"] == "Сборка страницы в Ноушене"


def test_lite_deps_reference_valid_ords(tasks_lite):
    valid = {t["ord"] for t in tasks_lite["tasks"]}
    for task in tasks_lite["tasks"]:
        for dep in task.get("depends_on", []):
            assert _dep_ord(dep) in valid, f"#{task['ord']} -> {dep}"


def test_lite_assignees_in_team(tasks_lite, team_names):
    for task in tasks_lite["tasks"]:
        for a in task["assignees"]:
            assert a["name"] in team_names, f"#{task['ord']}: {a['name']}"
