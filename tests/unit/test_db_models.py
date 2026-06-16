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
    PersonRole,
    PlanVersion,
    Project,
    Role,
    RoleSkill,
    Skill,
    Task,
    TaskHistory,
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


def test_role_tablename():
    assert Role.__tablename__ == "roles"


def test_skill_tablename():
    assert Skill.__tablename__ == "skills"


def test_role_skill_tablename():
    assert RoleSkill.__tablename__ == "role_skills"


def test_person_role_tablename():
    assert PersonRole.__tablename__ == "person_roles"


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


def test_role_required_columns():
    assert {"id", "name", "description"} <= _column_names(Role)


def test_skill_required_columns():
    assert {"id", "name", "description"} <= _column_names(Skill)


def test_role_skill_required_columns():
    assert {"role_id", "skill_id"} <= _column_names(RoleSkill)


def test_person_role_required_columns():
    assert {"person_id", "role_id"} <= _column_names(PersonRole)


def test_role_skill_composite_pk():
    pk_cols = {c.name for c in inspect(RoleSkill).mapper.persist_selectable.primary_key}
    assert pk_cols == {"role_id", "skill_id"}


def test_person_role_composite_pk():
    pk_cols = {c.name for c in inspect(PersonRole).mapper.persist_selectable.primary_key}
    assert pk_cols == {"person_id", "role_id"}


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
            "is_splittable", "allow_two_assignees", "source"} <= cols


def test_task_history_tablename():
    assert TaskHistory.__tablename__ == "task_history"


def test_task_history_required_columns():
    cols = _column_names(TaskHistory)
    assert {"id", "person_id", "task_name", "project_title",
            "completed_at", "skills"} <= cols


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


def test_task_has_source_check():
    constraints = Task.__table__.constraints
    check_names = {c.name for c in constraints if hasattr(c, "name")}
    assert "ck_task_source" in check_names
