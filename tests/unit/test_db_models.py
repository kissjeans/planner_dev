"""Unit tests for SQLAlchemy ORM model mapper configuration.

No database connection is required — these tests verify that
SQLAlchemy can map all model classes without errors, and that
each table has the expected columns.
"""

from sqlalchemy import inspect

from planner.infra.db.models import (
    Assignment,
    DayOverride,
    Dependency,
    Person,
    PlanVersion,
    Project,
    Task,
    Template,
    TemplateDependency,
    TemplateTask,
    TemplateTaskAssignee,
)

# ---------------------------------------------------------------------------
# __tablename__ checks
# ---------------------------------------------------------------------------


def test_person_tablename():
    assert Person.__tablename__ == "people"


def test_template_tablename():
    assert Template.__tablename__ == "templates"


def test_template_task_tablename():
    assert TemplateTask.__tablename__ == "template_tasks"


def test_template_task_assignee_tablename():
    assert TemplateTaskAssignee.__tablename__ == "template_task_assignees"


def test_template_dependency_tablename():
    assert TemplateDependency.__tablename__ == "template_dependencies"


def test_project_tablename():
    assert Project.__tablename__ == "projects"


def test_task_tablename():
    assert Task.__tablename__ == "tasks"


def test_assignment_tablename():
    assert Assignment.__tablename__ == "assignments"


def test_dependency_tablename():
    assert Dependency.__tablename__ == "dependencies"


def test_day_override_tablename():
    assert DayOverride.__tablename__ == "day_overrides"


def test_plan_version_tablename():
    assert PlanVersion.__tablename__ == "plan_versions"


# ---------------------------------------------------------------------------
# Column presence checks (using SQLAlchemy table introspection)
# ---------------------------------------------------------------------------


def _column_names(model):
    """Return the set of column names mapped to a model."""
    return {c.key for c in inspect(model).mapper.column_attrs}


def test_person_required_columns():
    cols = _column_names(Person)
    assert {"id", "tg_user_id", "name", "role_label", "capacity_h",
            "is_admin", "is_active", "is_external"} <= cols


def test_template_required_columns():
    cols = _column_names(Template)
    assert {"id", "code", "name"} <= cols


def test_template_task_required_columns():
    cols = _column_names(TemplateTask)
    assert {"id", "template_id", "ord", "name", "duration_hours",
            "duration_is_window", "is_splittable", "allow_two_assignees",
            "optional_in_lite"} <= cols


def test_template_task_assignee_required_columns():
    cols = _column_names(TemplateTaskAssignee)
    assert {"template_task_id", "person_id", "strictness"} <= cols


def test_template_dependency_required_columns():
    cols = _column_names(TemplateDependency)
    assert {"template_task_id", "depends_on_id", "link_type"} <= cols


def test_project_required_columns():
    cols = _column_names(Project)
    assert {"id", "title", "template_id", "brief_return_date", "deadline",
            "status", "created_at", "created_by"} <= cols


def test_task_required_columns():
    cols = _column_names(Task)
    assert {"id", "project_id", "template_task_id", "name", "duration_hours",
            "start_date", "end_date", "status", "is_preliminary",
            "is_splittable", "allow_two_assignees"} <= cols


def test_assignment_required_columns():
    cols = _column_names(Assignment)
    assert {"task_id", "person_id", "hours"} <= cols


def test_dependency_required_columns():
    cols = _column_names(Dependency)
    assert {"task_id", "depends_on_id", "link_type"} <= cols


def test_day_override_required_columns():
    cols = _column_names(DayOverride)
    assert {"person_id", "day", "capacity_h", "reason"} <= cols


def test_plan_version_required_columns():
    cols = _column_names(PlanVersion)
    assert {"id", "project_id", "status", "created_at", "created_by", "payload"} <= cols


# ---------------------------------------------------------------------------
# Primary key checks
# ---------------------------------------------------------------------------


def test_person_pk_is_uuid():
    pk_cols = [c.name for c in inspect(Person).mapper.persist_selectable.primary_key]
    assert pk_cols == ["id"]


def test_assignment_composite_pk():
    pk_cols = {c.name for c in inspect(Assignment).mapper.persist_selectable.primary_key}
    assert pk_cols == {"task_id", "person_id"}


def test_day_override_composite_pk():
    pk_cols = {c.name for c in inspect(DayOverride).mapper.persist_selectable.primary_key}
    assert pk_cols == {"person_id", "day"}


def test_dependency_composite_pk():
    pk_cols = {c.name for c in inspect(Dependency).mapper.persist_selectable.primary_key}
    assert pk_cols == {"task_id", "depends_on_id"}


# ---------------------------------------------------------------------------
# Constraint checks
# ---------------------------------------------------------------------------


def test_template_task_assignee_has_strictness_check():
    constraints = TemplateTaskAssignee.__table__.constraints
    check_names = {c.name for c in constraints if hasattr(c, "name")}
    assert "ck_strictness" in check_names


def test_dependency_has_link_type_check():
    constraints = Dependency.__table__.constraints
    check_names = {c.name for c in constraints if hasattr(c, "name")}
    assert "ck_link_type_dep" in check_names


def test_template_dependency_has_link_type_check():
    constraints = TemplateDependency.__table__.constraints
    check_names = {c.name for c in constraints if hasattr(c, "name")}
    assert "ck_link_type_template" in check_names
