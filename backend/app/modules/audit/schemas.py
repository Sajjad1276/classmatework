from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    actor_id: UUID | None
    event_type: str
    aggregate_type: str
    aggregate_id: UUID | None
    correlation_id: str | None
    event_data: dict
    created_at: datetime
