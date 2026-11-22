from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    InvalidApplicationInput,
    ResourceConflict,
    ResourceNotFound,
)
from app.db.models import (
    ClassGroup,
    ClassGroupLinkHandshake,
    ClassSubject,
    ClassTelegramGroup,
    School,
    Subject,
    TelegramAccount,
    Tenant,
    UsageEvent,
    UsageIdentity,
)
from app.modules.access.policy import has_permission
from app.modules.audit.service import record_event

_SAFE_EVENT_ATTRIBUTE_KEYS = {
    "feature",
    "action",
    "result",
    "duration_ms",
    "class_code",
    "subject_code",
    "source_screen",
}


async def _require(
    db: AsyncSession,
    *,
    actor_id: UUID,
    permission_key: str,
    tenant_id: UUID | None,
    scope_type: str = "platform",
    scope_id: UUID | None = None,
) -> None:
    allowed = await has_permission(
        db,
        user_id=actor_id,
        permission_key=permission_key,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    if not allowed:
        raise AccessDenied


async def create_class_group_handshake(
    db: AsyncSession,
    *,
    actor_id: UUID,
    class_group_id: UUID,
) -> tuple[str, ClassGroup, datetime]:
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == class_group_id))
    ).scalar_one_or_none()
    if class_group is None or class_group.status == "archived":
        raise ResourceNotFound("کلاس فعال موردنظر پیدا نشد.")

    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:group:manage",
        tenant_id=class_group.tenant_id,
        scope_type="class",
        scope_id=class_group.id,
    )
    account = (
        await db.execute(
            select(TelegramAccount).where(
                TelegramAccount.user_id == actor_id,
                TelegramAccount.status == "active",
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise AccessDenied

    now = datetime.now(UTC)
    await db.execute(
        update(ClassGroupLinkHandshake)
        .where(
            ClassGroupLinkHandshake.class_group_id == class_group_id,
            ClassGroupLinkHandshake.consumed_at.is_(None),
            ClassGroupLinkHandshake.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    raw_token = secrets.token_urlsafe(24)
    expires_at = now + timedelta(minutes=10)
    db.add(
        ClassGroupLinkHandshake(
            tenant_id=class_group.tenant_id,
            class_group_id=class_group.id,
            created_by_id=actor_id,
            creator_telegram_user_id=account.telegram_user_id,
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
        )
    )
    await db.flush()
    await record_event(
        db,
        event_type="class.telegram_group.handshake_created",
        actor_id=actor_id,
        tenant_id=class_group.tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={"expires_at": expires_at.isoformat()},
    )
    await db.commit()
    return raw_token, class_group, expires_at


async def get_class_telegram_group(
    db: AsyncSession,
    *,
    actor_id: UUID,
    class_group_id: UUID,
) -> ClassTelegramGroup | None:
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == class_group_id))
    ).scalar_one_or_none()
    if class_group is None or class_group.status == "archived":
        raise ResourceNotFound("کلاس فعال موردنظر پیدا نشد.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=class_group.tenant_id,
        scope_type="class",
        scope_id=class_group.id,
    )
    connection = (
        await db.execute(
            select(ClassTelegramGroup).where(
                ClassTelegramGroup.class_group_id == class_group_id,
                ClassTelegramGroup.status == "active",
            )
        )
    ).scalar_one_or_none()
    await record_event(
        db,
        event_type="class.telegram_group.directory.read",
        actor_id=actor_id,
        tenant_id=class_group.tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
    )
    await db.commit()
    return connection


async def revoke_class_telegram_group(
    db: AsyncSession,
    *,
    actor_id: UUID,
    class_group_id: UUID,
) -> bool:
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == class_group_id))
    ).scalar_one_or_none()
    if class_group is None or class_group.status == "archived":
        raise ResourceNotFound("کلاس فعال موردنظر پیدا نشد.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:group:manage",
        tenant_id=class_group.tenant_id,
        scope_type="class",
        scope_id=class_group.id,
    )
    connection = (
        await db.execute(
            select(ClassTelegramGroup).where(
                ClassTelegramGroup.class_group_id == class_group_id,
                ClassTelegramGroup.status == "active",
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        return False

    connection.status = "revoked"
    connection.updated_at = datetime.now(UTC)
    await record_event(
        db,
        event_type="class.telegram_group.revoked",
        actor_id=actor_id,
        tenant_id=class_group.tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={"telegram_chat_id": connection.telegram_chat_id},
    )
    await db.commit()
    return True


async def complete_class_group_handshake(
    db: AsyncSession,
    *,
    token: str,
    telegram_chat_id: int,
    chat_title: str,
    chat_username: str | None,
    chat_type: str,
    actor_telegram_user_id: int,
    integration_bot_id: int,
    bot_is_admin: bool,
) -> ClassTelegramGroup:
    if not token or len(token) > 128:
        raise InvalidApplicationInput("کد اتصال معتبر نیست.")
    if chat_type not in {"group", "supergroup"}:
        raise InvalidApplicationInput("فقط گروه تلگرام قابل اتصال است.")
    if not bot_is_admin:
        raise AccessDenied
    if actor_telegram_user_id <= 0 or integration_bot_id <= 0:
        raise InvalidApplicationInput("شناسه تلگرام معتبر نیست.")

    normalized_title = chat_title.strip()
    if not normalized_title or len(normalized_title) > 255:
        raise InvalidApplicationInput("نام گروه معتبر نیست.")
    normalized_username = (chat_username or "").strip().lstrip("@") or None
    if normalized_username and len(normalized_username) > 64:
        raise InvalidApplicationInput("شناسه عمومی گروه معتبر نیست.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    handshake = (
        await db.execute(
            select(ClassGroupLinkHandshake)
            .where(ClassGroupLinkHandshake.token_hash == token_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if handshake is None:
        raise ResourceNotFound("کد اتصال پیدا نشد یا قبلاً مصرف شده است.")

    class_group = (
        await db.execute(
            select(ClassGroup).where(
                ClassGroup.id == handshake.class_group_id,
                ClassGroup.tenant_id == handshake.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if class_group is None or class_group.status == "archived":
        raise ResourceNotFound("کلاس متصل به این کد دیگر فعال نیست.")

    existing = (
        await db.execute(
            select(ClassTelegramGroup).where(
                ClassTelegramGroup.class_group_id == class_group.id
            )
        )
    ).scalar_one_or_none()

    if handshake.consumed_at is not None:
        if (
            existing is not None
            and existing.telegram_chat_id == telegram_chat_id
            and existing.status == "active"
        ):
            return existing
        raise ResourceConflict("کد اتصال قبلاً مصرف شده است.")

    expires_at = handshake.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if handshake.revoked_at is not None or expires_at <= now:
        raise ResourceNotFound("کد اتصال منقضی یا لغو شده است.")
    if handshake.creator_telegram_user_id != actor_telegram_user_id:
        raise AccessDenied

    chat_owner = (
        await db.execute(
            select(ClassTelegramGroup).where(
                ClassTelegramGroup.telegram_chat_id == telegram_chat_id
            )
        )
    ).scalar_one_or_none()
    if chat_owner is not None and chat_owner.class_group_id != class_group.id:
        raise ResourceConflict("این گروه قبلاً به کلاس دیگری اختصاص یافته است.")

    if existing is None:
        existing = ClassTelegramGroup(
            tenant_id=class_group.tenant_id,
            class_group_id=class_group.id,
            telegram_chat_id=telegram_chat_id,
            chat_title=normalized_title,
            chat_username=normalized_username,
            chat_type=chat_type,
            status="active",
            connected_by_id=handshake.created_by_id,
            integration_bot_id=integration_bot_id,
            connected_at=now,
            last_verified_at=now,
        )
        db.add(existing)
    else:
        existing.telegram_chat_id = telegram_chat_id
        existing.chat_title = normalized_title
        existing.chat_username = normalized_username
        existing.chat_type = chat_type
        existing.status = "active"
        existing.connected_by_id = handshake.created_by_id
        existing.integration_bot_id = integration_bot_id
        existing.connected_at = now
        existing.last_verified_at = now
        existing.updated_at = now

    handshake.consumed_at = now
    await record_event(
        db,
        event_type="class.telegram_group.connected",
        actor_id=handshake.created_by_id,
        tenant_id=class_group.tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={
            "telegram_chat_id": telegram_chat_id,
            "chat_type": chat_type,
            "integration_bot_id": integration_bot_id,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ResourceConflict("این گروه یا کلاس به اتصال دیگری اختصاص یافته است.") from exc
    await db.refresh(existing)
    return existing


async def ingest_usage_event(
    db: AsyncSession,
    *,
    external_event_id: UUID,
    source_service: str,
    event_name: str,
    actor_telegram_user_id: int | None,
    actor_key_hash: str | None,
    tenant_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID | None,
    attributes: dict[str, Any],
    occurred_at: datetime,
) -> bool:
    prior = (
        await db.execute(
            select(UsageEvent.id).where(UsageEvent.external_event_id == external_event_id)
        )
    ).scalar_one_or_none()
    if prior is not None:
        return False

    actor_id = None
    if actor_telegram_user_id is not None:
        account = (
            await db.execute(
                select(TelegramAccount).where(
                    TelegramAccount.telegram_user_id == actor_telegram_user_id,
                    TelegramAccount.status == "active",
                )
            )
        ).scalar_one_or_none()
        if account is not None:
            actor_id = account.user_id

    if tenant_id is not None:
        tenant_exists = (
            await db.execute(
                select(Tenant.id).where(Tenant.id == tenant_id, Tenant.status == "active")
            )
        ).scalar_one_or_none()
        if tenant_exists is None:
            raise ResourceNotFound("مجموعهٔ رویداد پیدا نشد.")

    safe_attributes = {
        key: value
        for key, value in attributes.items()
        if key in _SAFE_EVENT_ATTRIBUTE_KEYS
        and isinstance(value, (str, int, float, bool))
        and len(str(value)) <= 160
    }
    if actor_key_hash is not None:
        if len(actor_key_hash) != 64:
            raise InvalidApplicationInput("شناسه ناشناس رویداد معتبر نیست.")
        identity = (
            await db.execute(
                select(UsageIdentity)
                .where(UsageIdentity.actor_key_hash == actor_key_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        occurred = occurred_at if occurred_at.tzinfo is not None else occurred_at.replace(tzinfo=UTC)
        status_updates = {
            "user.blocked": "blocked",
            "user.deactivated": "inactive",
            "user.activated": "active",
            "user.reactivated": "active",
            "user.registered": "active",
        }
        new_status = status_updates.get(event_name)
        if identity is None:
            identity = UsageIdentity(
                actor_key_hash=actor_key_hash,
                status=new_status or "active",
                first_source_service=source_service[:80],
                last_source_service=source_service[:80],
                first_seen_at=occurred,
                last_seen_at=occurred,
            )
            db.add(identity)
        else:
            last_seen = identity.last_seen_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            if occurred > last_seen:
                identity.last_seen_at = occurred
            identity.last_source_service = source_service[:80]
            identity.updated_at = datetime.now(UTC)
            if new_status is not None:
                identity.status = new_status
    db.add(
        UsageEvent(
            external_event_id=external_event_id,
            source_service=source_service[:80],
            event_name=event_name,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_key_hash=actor_key_hash,
            aggregate_type=aggregate_type[:80],
            aggregate_id=aggregate_id,
            attributes=safe_attributes,
            occurred_at=occurred_at,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        duplicate = (
            await db.execute(
                select(UsageEvent.id).where(UsageEvent.external_event_id == external_event_id)
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            return False
        raise
    return True


async def get_platform_report(
    db: AsyncSession,
    *,
    actor_id: UUID,
) -> dict[str, int | datetime]:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="platform:reports:read",
        tenant_id=None,
    )
    now = datetime.now(UTC)

    async def count(statement) -> int:
        return int((await db.execute(statement)).scalar_one() or 0)

    total_users = await count(select(func.count()).select_from(UsageIdentity))
    active_users = await count(
        select(func.count()).select_from(UsageIdentity).where(UsageIdentity.status == "active")
    )

    async def new_users_since(delta: timedelta) -> int:
        return await count(
            select(func.count()).select_from(UsageIdentity).where(
                UsageIdentity.first_seen_at >= now - delta
            )
        )

    async def active_users_since(delta: timedelta) -> int:
        return await count(
            select(func.count(func.distinct(UsageEvent.actor_key_hash)))
            .select_from(UsageEvent)
            .join(
                UsageIdentity,
                UsageIdentity.actor_key_hash == UsageEvent.actor_key_hash,
            )
            .where(
                UsageEvent.actor_key_hash.is_not(None),
                UsageIdentity.status == "active",
                UsageEvent.occurred_at >= now - delta,
            )
        )

    report = {
        "total_users": total_users,
        "active_users": active_users,
        "new_users_1d": await new_users_since(timedelta(days=1)),
        "new_users_7d": await new_users_since(timedelta(days=7)),
        "new_users_30d": await new_users_since(timedelta(days=30)),
        "active_users_1h": await active_users_since(timedelta(hours=1)),
        "active_users_4h": await active_users_since(timedelta(hours=4)),
        "active_users_12h": await active_users_since(timedelta(hours=12)),
        "schools": await count(select(func.count()).select_from(School)),
        "classes": await count(select(func.count()).select_from(ClassGroup)),
        "subjects": await count(select(func.count()).select_from(Subject)),
        "connected_groups": await count(
            select(func.count()).select_from(ClassTelegramGroup).where(
                ClassTelegramGroup.status == "active"
            )
        ),
        "events_24h": await count(
            select(func.count()).select_from(UsageEvent).where(
                UsageEvent.occurred_at >= now - timedelta(hours=24)
            )
        ),
        "generated_at": now,
    }
    await record_event(
        db,
        event_type="platform.report.viewed",
        actor_id=actor_id,
        aggregate_type="platform_report",
    )
    await db.commit()
    return report

async def list_school_class_group_directory(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    limit: int = 8,
    offset: int = 0,
) -> dict[str, Any]:
    if not 1 <= limit <= 25 or offset < 0:
        raise InvalidApplicationInput("بازهٔ صفحه‌بندی فهرست گروه‌ها معتبر نیست.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    school = (
        await db.execute(
            select(School).where(
                School.id == school_id,
                School.tenant_id == tenant_id,
                School.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if school is None:
        raise ResourceNotFound("مدرسه در مجموعه انتخاب‌شده پیدا نشد.")

    class_filter = (
        ClassGroup.tenant_id == tenant_id,
        ClassGroup.school_id == school_id,
        ClassGroup.status != "archived",
    )
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(ClassGroup).where(*class_filter)
            )
        ).scalar_one()
        or 0
    )
    active_link = and_(
        ClassTelegramGroup.class_group_id == ClassGroup.id,
        ClassTelegramGroup.status == "active",
    )
    connected = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ClassGroup)
                .join(ClassTelegramGroup, active_link)
                .where(*class_filter)
            )
        ).scalar_one()
        or 0
    )
    result = await db.execute(
        select(ClassGroup, ClassTelegramGroup)
        .outerjoin(ClassTelegramGroup, active_link)
        .where(*class_filter)
        .order_by(ClassGroup.name, ClassGroup.grade, ClassGroup.code)
        .offset(offset)
        .limit(limit)
    )
    items = [
        {"class_group": class_group, "connection": connection}
        for class_group, connection in result.all()
    ]
    pages = (total + limit - 1) // limit
    page = offset // limit + 1
    await record_event(
        db,
        event_type="class.telegram_group.directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="class_group_directory",
        aggregate_id=school_id,
        event_data={
            "school_id": str(school_id),
            "offset": offset,
            "limit": limit,
            "returned_count": len(items),
            "total_classes": total,
        },
    )
    await db.commit()
    return {
        "school_name": school.name,
        "total_classes": total,
        "connected_classes": connected,
        "disconnected_classes": max(0, total - connected),
        "page": page,
        "pages": pages,
        "items": items,
    }

async def get_class_subject_catalog(
    db: AsyncSession,
    *,
    actor_id: UUID,
    class_group_id: UUID,
) -> dict[str, Any]:
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == class_group_id))
    ).scalar_one_or_none()
    if class_group is None or class_group.status == "archived":
        raise ResourceNotFound("کلاس فعال موردنظر پیدا نشد.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=class_group.tenant_id,
        scope_type="class",
        scope_id=class_group.id,
    )
    subjects_result = await db.execute(
        select(Subject)
        .where(
            Subject.tenant_id == class_group.tenant_id,
            Subject.school_id == class_group.school_id,
            Subject.status == "active",
        )
        .order_by(Subject.name, Subject.code)
    )
    subjects = list(subjects_result.scalars().all())
    assignment_result = await db.execute(
        select(ClassSubject.subject_id).where(
            ClassSubject.tenant_id == class_group.tenant_id,
            ClassSubject.school_id == class_group.school_id,
            ClassSubject.class_group_id == class_group.id,
            ClassSubject.status == "active",
        )
    )
    assigned_ids = set(assignment_result.scalars().all())
    await record_event(
        db,
        event_type="class.subject_catalog.read",
        actor_id=actor_id,
        tenant_id=class_group.tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={
            "subject_count": len(subjects),
            "assigned_count": len(assigned_ids),
        },
    )
    await db.commit()
    return {
        "class_group": class_group,
        "subject_count": len(subjects),
        "assigned_count": len(assigned_ids),
        "items": [
            {"subject": subject, "assigned": subject.id in assigned_ids}
            for subject in subjects
        ],
    }


async def set_class_subject_assignment(
    db: AsyncSession,
    *,
    actor_id: UUID,
    class_group_id: UUID,
    subject_id: UUID,
    assigned: bool,
) -> bool:
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == class_group_id))
    ).scalar_one_or_none()
    if class_group is None or class_group.status == "archived":
        raise ResourceNotFound("کلاس فعال موردنظر پیدا نشد.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:subjects:manage",
        tenant_id=class_group.tenant_id,
        scope_type="class",
        scope_id=class_group.id,
    )
    subject = (
        await db.execute(
            select(Subject).where(
                Subject.id == subject_id,
                Subject.tenant_id == class_group.tenant_id,
                Subject.school_id == class_group.school_id,
                Subject.status == "active",
            )
        )
    ).scalar_one_or_none()
    if subject is None:
        raise ResourceNotFound("این درس در مدرسهٔ متعلق به کلاس پیدا نشد.")

    link = (
        await db.execute(
            select(ClassSubject).where(
                ClassSubject.class_group_id == class_group.id,
                ClassSubject.subject_id == subject.id,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)

    if assigned:
        if link is not None and link.status == "active":
            return False
        if link is None:
            link = ClassSubject(
                tenant_id=class_group.tenant_id,
                school_id=class_group.school_id,
                class_group_id=class_group.id,
                subject_id=subject.id,
                assigned_by_id=actor_id,
                status="active",
                assigned_at=now,
                updated_at=now,
            )
            db.add(link)
        else:
            link.status = "active"
            link.assigned_by_id = actor_id
            link.assigned_at = now
            link.updated_at = now
        event_type = "class.subject.assigned"
    else:
        if link is None or link.status != "active":
            return False
        link.status = "inactive"
        link.updated_at = now
        event_type = "class.subject.unassigned"

    await record_event(
        db,
        event_type=event_type,
        actor_id=actor_id,
        tenant_id=class_group.tenant_id,
        aggregate_type="class_subject",
        aggregate_id=link.id,
        event_data={
            "class_group_id": str(class_group.id),
            "subject_id": str(subject.id),
        },
    )
    await db.commit()
    return True

