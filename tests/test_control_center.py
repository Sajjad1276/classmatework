from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.control_center import (
    complete_class_group_handshake,
    create_class_group_handshake,
    get_platform_report,
    ingest_usage_event,
    list_school_class_group_directory,
)
from app.api.v1.integrations import _require_integration_key
from app.application.errors import AccessDenied
from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AcademicYear,
    ClassGroup,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    School,
    TelegramAccount,
    Tenant,
    TenantMembership,
    UsageEvent,
    User,
)


async def _setup_group_admin(db):
    tenant = Tenant(slug=f"group-{uuid4().hex[:10]}", name="Group Tenant")
    user = User(display_name="Group Manager", status="active")
    role = Role(tenant_id=None, key=f"group_admin_{uuid4().hex[:8]}", name="Group Admin")
    permissions = [
        Permission(key="class:group:manage", description="Manage class group"),
        Permission(key="school:read", description="Read class data"),
    ]
    db.add_all([tenant, user, role, *permissions])
    await db.flush()
    school = School(tenant_id=tenant.id, name="School", code=f"S{uuid4().hex[:6]}")
    db.add(school)
    await db.flush()
    year = AcademicYear(
        tenant_id=tenant.id,
        school_id=school.id,
        name="1405",
        starts_on=date(2026, 3, 21),
        ends_on=date(2027, 3, 20),
        status="active",
    )
    db.add(year)
    await db.flush()
    class_group = ClassGroup(
        tenant_id=tenant.id,
        school_id=school.id,
        academic_year_id=year.id,
        name="Class A",
        code=f"A-{uuid4().hex[:6]}",
        grade="10",
        status="active",
    )
    db.add_all([
        class_group,
        TelegramAccount(user_id=user.id, telegram_user_id=987654321, status="active"),
        TenantMembership(tenant_id=tenant.id, user_id=user.id, status="active"),
    ])
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=role.id, permission_id=item.id, effect="allow")
            for item in permissions
        ]
        + [
            RoleBinding(
                user_id=user.id,
                role_id=role.id,
                tenant_id=tenant.id,
                scope_type="tenant",
                scope_id=tenant.id,
                created_by_id=user.id,
            )
        ]
    )
    await db.commit()
    return user, tenant, class_group


@pytest.mark.asyncio
async def test_class_group_handshake_is_bound_to_issuer_and_chat() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        user, _tenant, class_group = await _setup_group_admin(db)
        code, _, expiry = await create_class_group_handshake(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
        )
        assert expiry > datetime.now(UTC)
        assert len(code) >= 24

        with pytest.raises(AccessDenied):
            await complete_class_group_handshake(
                db,
                token=code,
                telegram_chat_id=-100123456,
                chat_title="Class A",
                chat_username=None,
                chat_type="supergroup",
                actor_telegram_user_id=111111111,
                integration_bot_id=900000001,
                bot_is_admin=True,
            )

        connected = await complete_class_group_handshake(
            db,
            token=code,
            telegram_chat_id=-100123456,
            chat_title="Class A Group",
            chat_username="class_a",
            chat_type="supergroup",
            actor_telegram_user_id=987654321,
            integration_bot_id=900000001,
            bot_is_admin=True,
        )
        assert connected.class_group_id == class_group.id
        assert connected.telegram_chat_id == -100123456
        assert connected.status == "active"

        with pytest.raises(AccessDenied):
            await complete_class_group_handshake(
                db,
                token=code,
                telegram_chat_id=-100123456,
                chat_title="Class A Group",
                chat_username="class_a",
                chat_type="supergroup",
                actor_telegram_user_id=987654321,
                integration_bot_id=900000001,
                bot_is_admin=False,
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_usage_events_are_idempotent_and_report_real_activity() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        user = User(display_name="Report Owner", status="active")
        role = Role(
            tenant_id=None,
            key=f"platform_owner_{uuid4().hex[:8]}",
            name="Platform Owner",
            is_system=True,
        )
        permission = Permission(
            key="platform:reports:read",
            description="Read platform report",
        )
        db.add_all([user, role, permission])
        await db.flush()
        db.add_all(
            [
                RolePermission(
                    role_id=role.id,
                    permission_id=permission.id,
                    effect="allow",
                ),
                RoleBinding(
                    user_id=user.id,
                    role_id=role.id,
                    tenant_id=None,
                    scope_type="platform",
                    scope_id=None,
                    created_by_id=user.id,
                ),
                TelegramAccount(
                    user_id=user.id,
                    telegram_user_id=123456789,
                    status="active",
                ),
            ]
        )
        await db.commit()

        event_id = uuid4()
        now = datetime.now(UTC)
        assert await ingest_usage_event(
            db,
            external_event_id=event_id,
            source_service="main-bot",
            event_name="class.opened",
            actor_telegram_user_id=123456789,
            actor_key_hash="a" * 64,
            tenant_id=None,
            aggregate_type="class",
            aggregate_id=uuid4(),
            attributes={
                "feature": "class",
                "source_screen": "student_home",
                "message_text": "must_not_be_saved",
            },
            occurred_at=now,
        )
        assert not await ingest_usage_event(
            db,
            external_event_id=event_id,
            source_service="main-bot",
            event_name="class.opened",
            actor_telegram_user_id=123456789,
            actor_key_hash="a" * 64,
            tenant_id=None,
            aggregate_type="class",
            aggregate_id=None,
            attributes={},
            occurred_at=now,
        )

        report = await get_platform_report(db, actor_id=user.id)
        assert report["total_users"] == 1
        assert report["active_users_1h"] == 1
        assert report["events_24h"] == 1
        stored = (await db.execute(select(UsageEvent))).scalar_one()
        assert "message_text" not in stored.attributes
        assert stored.attributes["feature"] == "class"
        assert stored.actor_key_hash == "a" * 64

        assert await ingest_usage_event(
            db,
            external_event_id=uuid4(),
            source_service="main-bot",
            event_name="user.deactivated",
            actor_telegram_user_id=123456789,
            actor_key_hash="a" * 64,
            tenant_id=None,
            aggregate_type="user",
            aggregate_id=None,
            attributes={"action": "deactivated"},
            occurred_at=now,
        )
        inactive_report = await get_platform_report(db, actor_id=user.id)
        assert inactive_report["active_users"] == 0
        assert inactive_report["active_users_1h"] == 0

    await engine.dispose()

@pytest.mark.asyncio
async def test_school_group_directory_reports_connected_and_missing_classes_by_page() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        user, tenant, class_group = await _setup_group_admin(db)
        second_class = ClassGroup(
            tenant_id=tenant.id,
            school_id=class_group.school_id,
            academic_year_id=class_group.academic_year_id,
            name="Class B",
            code=f"B-{uuid4().hex[:6]}",
            grade="11",
            status="active",
        )
        db.add(second_class)
        await db.commit()

        code, _, _ = await create_class_group_handshake(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
        )
        await complete_class_group_handshake(
            db,
            token=code,
            telegram_chat_id=-100223344,
            chat_title="Class A Group",
            chat_username=None,
            chat_type="supergroup",
            actor_telegram_user_id=987654321,
            integration_bot_id=900000001,
            bot_is_admin=True,
        )

        first_page = await list_school_class_group_directory(
            db,
            actor_id=user.id,
            tenant_id=tenant.id,
            school_id=class_group.school_id,
            limit=1,
            offset=0,
        )
        second_page = await list_school_class_group_directory(
            db,
            actor_id=user.id,
            tenant_id=tenant.id,
            school_id=class_group.school_id,
            limit=1,
            offset=1,
        )

        assert first_page["total_classes"] == 2
        assert first_page["connected_classes"] == 1
        assert first_page["disconnected_classes"] == 1
        assert first_page["pages"] == 2
        assert first_page["items"][0]["connection"].chat_title == "Class A Group"
        assert second_page["items"][0]["class_group"].name == "Class B"
        assert second_page["items"][0]["connection"] is None

    await engine.dispose()

@pytest.mark.asyncio
async def test_integration_authentication_is_closed_by_default_and_compares_secret() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as missing:
        await _require_integration_key(
            x_classmate_integration_key=None,
            settings=Settings(),
        )
    assert missing.value.status_code == 503

    configured = Settings(classmate_integration_token="shared-secret")
    with pytest.raises(HTTPException) as invalid:
        await _require_integration_key(
            x_classmate_integration_key="wrong-secret",
            settings=configured,
        )
    assert invalid.value.status_code == 401

    await _require_integration_key(
        x_classmate_integration_key="shared-secret",
        settings=configured,
    )

