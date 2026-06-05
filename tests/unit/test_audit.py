"""Unit tests for AuditLog model and write_audit() helper.

No database connection required — all tests run purely against the ORM
mapper and a mocked AsyncSession.
"""
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import inspect

from planner.infra.db.audit import write_audit
from planner.infra.db.base import Base
from planner.infra.db.models import AuditLog

# ---------------------------------------------------------------------------
# Model structure
# ---------------------------------------------------------------------------


def test_audit_log_tablename():
    assert AuditLog.__tablename__ == "audit_log"


def test_audit_log_has_all_columns():
    expected = {"id", "created_at", "actor_id", "action", "entity_type", "entity_id", "payload"}
    actual = {c.key for c in inspect(AuditLog).mapper.column_attrs}
    assert expected <= actual


# ---------------------------------------------------------------------------
# write_audit() behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_audit_creates_entry_with_correct_fields():
    session = MagicMock()
    actor = uuid.uuid4()
    entity = uuid.uuid4()

    entry = await write_audit(
        session=session,
        action="confirm_plan",
        entity_type="plan_version",
        entity_id=entity,
        actor_id=actor,
        payload={"before": None, "after": "confirmed"},
    )

    assert isinstance(entry, AuditLog)
    assert entry.action == "confirm_plan"
    assert entry.entity_type == "plan_version"
    assert entry.entity_id == entity
    assert entry.actor_id == actor
    assert entry.payload == {"before": None, "after": "confirmed"}
    session.add.assert_called_once_with(entry)


@pytest.mark.asyncio
async def test_write_audit_required_args_only_defaults_to_none():
    session = MagicMock()

    entry = await write_audit(
        session=session,
        action="add_project",
        entity_type="project",
    )

    assert isinstance(entry, AuditLog)
    assert entry.action == "add_project"
    assert entry.entity_type == "project"
    assert entry.entity_id is None
    assert entry.actor_id is None
    assert entry.payload is None
    session.add.assert_called_once_with(entry)


# ---------------------------------------------------------------------------
# Metadata registration
# ---------------------------------------------------------------------------


def test_base_metadata_contains_audit_log():
    assert "audit_log" in Base.metadata.tables
