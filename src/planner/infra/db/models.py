"""SQLAlchemy ORM models for all planner domain tables."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from planner.infra.db.base import Base


class Person(Base):
    """A team member or external collaborator."""

    __tablename__ = "people"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_user_id = Column(Integer, unique=True, nullable=True)
    name = Column(Text, nullable=False)
    role_label = Column(Text, nullable=True)
    capacity_h = Column(Integer, nullable=False, default=8)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    is_external = Column(Boolean, nullable=False, default=False)


class Template(Base):
    """A reusable project template (e.g. 'standard', 'lite')."""

    __tablename__ = "templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)

    tasks = relationship("TemplateTask", back_populates="template")


class TemplateTask(Base):
    """A task definition within a template."""

    __tablename__ = "template_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates.id"), nullable=False)
    ord = Column(Integer, nullable=False)
    name = Column(Text, nullable=False)
    duration_hours = Column(Integer, nullable=False)
    duration_is_window = Column(Boolean, nullable=False, default=False)
    is_splittable = Column(Boolean, nullable=False, default=False)
    allow_two_assignees = Column(Boolean, nullable=False, default=False)
    optional_in_lite = Column(Boolean, nullable=False, default=False)

    template = relationship("Template", back_populates="tasks")


class TemplateTaskAssignee(Base):
    """Default assignee for a template task, with a strictness level."""

    __tablename__ = "template_task_assignees"

    template_task_id = Column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), primary_key=True
    )
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True)
    strictness = Column(String(1), nullable=False)

    __table_args__ = (
        CheckConstraint("strictness IN ('A','B','C')", name="ck_strictness"),
    )


class TemplateDependency(Base):
    """Dependency between two template tasks."""

    __tablename__ = "template_dependencies"

    template_task_id = Column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), primary_key=True
    )
    depends_on_id = Column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), primary_key=True
    )
    link_type = Column(String(2), nullable=False)

    __table_args__ = (
        CheckConstraint("link_type IN ('FS','SS')", name="ck_link_type_template"),
    )


class Project(Base):
    """A presales project instance."""

    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(Text, nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates.id"), nullable=True)
    brief_return_date = Column(Date, nullable=True)
    deadline = Column(Date, nullable=True)
    status = Column(Text, nullable=False, default="planning")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)

    tasks = relationship("Task", back_populates="project")
    plan_versions = relationship("PlanVersion", back_populates="project")


class Task(Base):
    """A concrete task within a project."""

    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    template_task_id = Column(
        UUID(as_uuid=True), ForeignKey("template_tasks.id"), nullable=True
    )
    name = Column(Text, nullable=False)
    duration_hours = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    status = Column(Text, nullable=False, default="not_done")
    is_preliminary = Column(Boolean, nullable=False, default=False)
    is_splittable = Column(Boolean, nullable=False, default=False)
    allow_two_assignees = Column(Boolean, nullable=False, default=False)

    project = relationship("Project", back_populates="tasks")


class Assignment(Base):
    """Assignment of a person to a task with an hour allocation."""

    __tablename__ = "assignments"

    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True)
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True)
    hours = Column(Integer, nullable=False)


class Dependency(Base):
    """Dependency between two concrete tasks in a project."""

    __tablename__ = "dependencies"

    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True)
    depends_on_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True)
    link_type = Column(String(2), nullable=False)

    __table_args__ = (
        CheckConstraint("link_type IN ('FS','SS')", name="ck_link_type_dep"),
    )


class DayOverride(Base):
    """Per-person per-day capacity override (e.g. holiday, partial day)."""

    __tablename__ = "day_overrides"

    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), primary_key=True)
    day = Column(Date, primary_key=True)
    capacity_h = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)


class PlanVersion(Base):
    """A snapshot of a project schedule (proposed or committed)."""

    __tablename__ = "plan_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    payload = Column(JSONB, nullable=False)

    project = relationship("Project", back_populates="plan_versions")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    action = Column(Text, nullable=False)        # e.g. "confirm_plan", "add_project"
    entity_type = Column(Text, nullable=False)   # e.g. "plan_version", "project"
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    payload = Column(JSONB, nullable=True)        # before/after snapshot
