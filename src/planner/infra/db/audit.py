"""Manual audit log helper. Call write_audit() in each use-case that mutates state."""
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from planner.infra.db.models import AuditLog


async def write_audit(
    session: AsyncSession,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    actor_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Write one audit log row. Must be called inside an active transaction."""
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        payload=payload,
    )
    session.add(entry)
    return entry
