from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import request_id_context
from app.db.models import AuditEvent


async def record_event(
    db: AsyncSession,
    *,
    event_type: str,
    actor_id: UUID | None,
    tenant_id: UUID | None = None,
    aggregate_type: str = "",
    aggregate_id: UUID | None = None,
    event_data: dict[str, Any] | None = None,
) -> AuditEvent:
    record = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=request_id_context.get(),
        event_data=event_data or {},
    )
    db.add(record)
    await db.flush()
    return record
