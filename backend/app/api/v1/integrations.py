from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.control_center import complete_class_group_handshake, ingest_usage_event
from app.core.config import Settings, get_settings
from app.db.dependencies import get_db

router = APIRouter()
_EVENT_NAME = re.compile(r"^[a-z0-9_.-]{2,120}$")


class GroupLinkCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=10, max_length=128)
    telegram_chat_id: int
    chat_title: str = Field(min_length=1, max_length=255)
    chat_username: str | None = Field(default=None, max_length=64)
    chat_type: Literal["group", "supergroup"]
    actor_telegram_user_id: int = Field(gt=0)
    integration_bot_id: int = Field(gt=0)
    bot_is_admin: bool


class UsageEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    source_service: str = Field(min_length=2, max_length=80)
    event_name: str = Field(min_length=2, max_length=120)
    actor_telegram_user_id: int | None = Field(default=None, gt=0)
    tenant_id: UUID | None = None
    aggregate_type: str = Field(default="", max_length=80)
    aggregate_id: UUID | None = None
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=20)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("event_name")
    @classmethod
    def validate_event_name(cls, value: str) -> str:
        if not _EVENT_NAME.fullmatch(value):
            raise ValueError("event_name must be a lowercase dotted event key")
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)


async def _require_integration_key(
    x_classmate_integration_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.classmate_integration_token
    if expected is None or not expected.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ClassMate integration is not configured.",
        )
    if not x_classmate_integration_key or not hmac.compare_digest(
        x_classmate_integration_key,
        expected.get_secret_value(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid integration credential.",
        )


@router.post("/telegram/group-links/complete", status_code=status.HTTP_200_OK)
async def complete_group_link(
    payload: GroupLinkCompleteIn,
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_integration_key),
) -> dict[str, object]:
    connection = await complete_class_group_handshake(
        db,
        token=payload.code,
        telegram_chat_id=payload.telegram_chat_id,
        chat_title=payload.chat_title,
        chat_username=payload.chat_username,
        chat_type=payload.chat_type,
        actor_telegram_user_id=payload.actor_telegram_user_id,
        integration_bot_id=payload.integration_bot_id,
        bot_is_admin=payload.bot_is_admin,
    )
    return {
        "connected": True,
        "class_group_id": str(connection.class_group_id),
        "telegram_chat_id": connection.telegram_chat_id,
        "chat_title": connection.chat_title,
        "status": connection.status,
    }


@router.post("/usage-events", status_code=status.HTTP_202_ACCEPTED)
async def receive_usage_event(
    payload: UsageEventIn,
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_integration_key),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    actor_key_hash = None
    if payload.actor_telegram_user_id is not None:
        hash_secret = settings.classmate_telemetry_hash_key
        if hash_secret is None or not hash_secret.get_secret_value().strip():
            hash_secret = settings.classmate_integration_token
        if hash_secret is None or not hash_secret.get_secret_value().strip():
            raise HTTPException(status_code=503, detail="Telemetry fingerprint key is not configured.")
        actor_key_hash = hmac.new(
            hash_secret.get_secret_value().encode("utf-8"),
            f"telegram:{payload.actor_telegram_user_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    accepted = await ingest_usage_event(
        db,
        external_event_id=payload.event_id,
        source_service=payload.source_service,
        event_name=payload.event_name,
        actor_telegram_user_id=payload.actor_telegram_user_id,
        actor_key_hash=actor_key_hash,
        tenant_id=payload.tenant_id,
        aggregate_type=payload.aggregate_type,
        aggregate_id=payload.aggregate_id,
        attributes=payload.attributes,
        occurred_at=payload.occurred_at,
    )
    return {
        "accepted": accepted,
        "duplicate": not accepted,
        "event_id": str(payload.event_id),
    }
