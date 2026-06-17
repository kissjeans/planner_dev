"""SQLAlchemy ORM models for all planner domain tables."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from planner.infra.db.base import Base


class Person(Base):
    """A team member or external collaborator."""

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tg_user_id: Mapped[int | None] = mapped_column(unique=True, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    capacity_h: Mapped[int] = mapped_column(nullable=False, default=8)
    is_admin: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    is_external: Mapped[bool] = mapped_column(nullable=False, default=False)


class Role(Base):
    """A named role (e.g. 'Разработчик'). Bundles standard skills."""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Skill(Base):
    """An atomic ability with a short description (what it is / what it's for)."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RoleSkill(Base):
    """Skill that a role implies (standard skill set of the role)."""

    __tablename__ = "role_skills"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id"), primary_key=True
    )


class PersonRole(Base):
    """A person holding a role. Capability of a person = union of their roles' skills."""

    __tablename__ = "person_roles"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True
    )


class Template(Base):
    """A reusable project template (e.g. 'standard', 'lite')."""

    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    tasks: Mapped[list["TemplateTask"]] = relationship(back_populates="template")


class TemplateTask(Base):
    """A task definition within a template."""

    __tablename__ = "template_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id"), nullable=False
    )
    ord: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    duration_hours: Mapped[int] = mapped_column(nullable=False)
    duration_is_window: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_splittable: Mapped[bool] = mapped_column(nullable=False, default=False)
    allow_two_assignees: Mapped[bool] = mapped_column(nullable=False, default=False)
    optional_in_lite: Mapped[bool] = mapped_column(nullable=False, default=False)

    template: Mapped["Template"] = relationship(back_populates="tasks")


class TemplateTaskAssignee(Base):
    """Default assignee for a template task, with a strictness level."""

    __tablename__ = "template_task_assignees"

    template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), primary_key=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True
    )
    strictness: Mapped[str] = mapped_column(String(1), nullable=False)

    __table_args__ = (
        CheckConstraint("strictness IN ('A','B','C')", name="ck_strictness"),
    )


class TemplateDependency(Base):
    """Dependency between two template tasks."""

    __tablename__ = "template_dependencies"

    template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), primary_key=True
    )
    depends_on_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), primary_key=True
    )
    link_type: Mapped[str] = mapped_column(String(2), nullable=False)

    __table_args__ = (
        CheckConstraint("link_type IN ('FS','SS')", name="ck_link_type_template"),
    )


class Project(Base):
    """A presales project instance."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id"), nullable=True
    )
    brief_return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="medium")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planning")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )

    tasks: Mapped[list["Task"]] = relationship(back_populates="project")
    plan_versions: Mapped[list["PlanVersion"]] = relationship(back_populates="project")


class Task(Base):
    """A concrete task within a project."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    template_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    duration_hours: Mapped[int] = mapped_column(nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="not_done")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="bot_formed")
    required_skills: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    is_preliminary: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_splittable: Mapped[bool] = mapped_column(nullable=False, default=False)
    allow_two_assignees: Mapped[bool] = mapped_column(nullable=False, default=False)

    project: Mapped["Project"] = relationship(back_populates="tasks")

    __table_args__ = (
        CheckConstraint(
            "source IN ('bot_formed','template')", name="ck_task_source"
        ),
    )


class Assignment(Base):
    """Assignment of a person to a task with an hour allocation."""

    __tablename__ = "assignments"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True
    )
    hours: Mapped[int] = mapped_column(nullable=False)


class Dependency(Base):
    """Dependency between two concrete tasks in a project."""

    __tablename__ = "dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True
    )
    depends_on_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True
    )
    link_type: Mapped[str] = mapped_column(String(2), nullable=False)

    __table_args__ = (
        CheckConstraint("link_type IN ('FS','SS')", name="ck_link_type_dep"),
    )


class DayOverride(Base):
    """Per-person per-day capacity override (e.g. holiday, partial day)."""

    __tablename__ = "day_overrides"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    capacity_h: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlanVersion(Base):
    """A snapshot of a project schedule (proposed or committed)."""

    __tablename__ = "plan_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="plan_versions")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class TaskHistory(Base):
    """Completed tasks per person — groundwork for history-based matching (spec Ф4).

    No use case wires this yet; it exists so future executor suggestion can
    factor in what a person has done before (spec section 5, point 2).
    """

    __tablename__ = "task_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    task_name: Mapped[str] = mapped_column(Text, nullable=False)
    project_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    skills: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
