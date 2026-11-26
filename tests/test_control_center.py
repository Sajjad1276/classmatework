from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.control_center import (
    build_usage_actor_hash,
    complete_class_group_handshake,
    create_class_group_handshake,
    get_class_subject_catalog,
    get_platform_report,
    ingest_usage_event,
    list_school_class_group_directory,
    set_class_subject_assignment,
)
from app.application.errors import AccessDenied
from app.bot.control_router import (
    _decode_subject_callback_id,
    _encode_subject_callback_id,
)
from app.db.base import Base
from app.db.models import (
    AcademicYear,
    ClassGroup,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    School,
    Subject,
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
        Permission(key="class:subjects:manage", description="Manage class subjects"),
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
async def test_class_subject_assignment_is_scoped_to_the_same_school() -> None:
    from app.application.errors import ResourceNotFound

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        user, tenant, class_group = await _setup_group_admin(db)
        subject = Subject(
            tenant_id=tenant.id,
            school_id=class_group.school_id,
            name="Mathematics",
            code=f"M-{uuid4().hex[:6]}",
            status="active",
        )
        other_school = School(
            tenant_id=tenant.id,
            name="Other School",
            code=f"OS-{uuid4().hex[:6]}",
            status="active",
        )
        db.add_all([subject, other_school])
        await db.flush()
        other_subject = Subject(
            tenant_id=tenant.id,
            school_id=other_school.id,
            name="Physics",
            code=f"P-{uuid4().hex[:6]}",
            status="active",
        )
        db.add(other_subject)
        await db.commit()

        initial = await get_class_subject_catalog(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
        )
        assert initial["subject_count"] == 1
        assert initial["assigned_count"] == 0
        assert initial["items"][0]["assigned"] is False

        assert await set_class_subject_assignment(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
            subject_id=subject.id,
            assigned=True,
        )
        assigned = await get_class_subject_catalog(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
        )
        assert assigned["assigned_count"] == 1
        assert assigned["items"][0]["assigned"] is True

        assert await set_class_subject_assignment(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
            subject_id=subject.id,
            assigned=False,
        )
        removed = await get_class_subject_catalog(
            db,
            actor_id=user.id,
            class_group_id=class_group.id,
        )
        assert removed["assigned_count"] == 0
        assert removed["items"][0]["assigned"] is False

        with pytest.raises(ResourceNotFound):
            await set_class_subject_assignment(
                db,
                actor_id=user.id,
                class_group_id=class_group.id,
                subject_id=other_subject.id,
                assigned=True,
            )

    await engine.dispose()

def test_subject_callback_id_is_compact_and_round_trips_without_fsm_state() -> None:
    class_id = uuid4()
    subject_id = uuid4()
    subject_ref = _encode_subject_callback_id(subject_id)

    assert len(subject_ref) == 22
    assert _decode_subject_callback_id(subject_ref) == subject_id
    assert len(f"cs:a:{class_id.hex}:{subject_ref}".encode("utf-8")) <= 64
    assert len(f"cs:r:{class_id.hex}:{subject_ref}".encode("utf-8")) <= 64



def test_usage_actor_hash_is_stable_and_does_not_expose_telegram_id() -> None:
    first = build_usage_actor_hash(telegram_user_id=123456789, hash_key="stable-test-key")
    repeated = build_usage_actor_hash(telegram_user_id=123456789, hash_key="stable-test-key")
    different_actor = build_usage_actor_hash(telegram_user_id=123456780, hash_key="stable-test-key")
    rotated_key = build_usage_actor_hash(telegram_user_id=123456789, hash_key="rotated-test-key")

    assert first == repeated
    assert first != different_actor
    assert first != rotated_key
    assert len(first) == 64
    assert "123456789" not in first


@pytest.mark.asyncio
async def test_platform_report_reads_private_starts_and_activity_from_live_classmate_db() -> None:
    work_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    product_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with work_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    work_factory = async_sessionmaker(work_engine, expire_on_commit=False)
    product_factory = async_sessionmaker(product_engine, expire_on_commit=False)

    async with product_engine.begin() as connection:
        await connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
        await connection.execute(text(
            "CREATE TABLE public.users (id INTEGER PRIMARY KEY, private_chat_started BOOLEAN NOT NULL, "
            "is_active BOOLEAN NOT NULL, created_at DATETIME NOT NULL, last_seen_at DATETIME)"
        ))
        await connection.execute(text(
            "CREATE TABLE public.schools (id INTEGER PRIMARY KEY, is_active BOOLEAN NOT NULL)"
        ))
        await connection.execute(text(
            "CREATE TABLE public.classes (id INTEGER PRIMARY KEY, is_active BOOLEAN NOT NULL, group_chat_id BIGINT)"
        ))
        await connection.execute(text(
            "CREATE TABLE public.subjects (id INTEGER PRIMARY KEY)"
        ))
        await connection.execute(text(
            "CREATE TABLE public.class_memberships (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "is_active BOOLEAN NOT NULL, role VARCHAR(32) NOT NULL)"
        ))

    now = datetime.now(UTC)
    async with work_factory() as db, product_factory() as product_db:
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
        db.add_all([
            RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"),
            RoleBinding(
                user_id=user.id,
                role_id=role.id,
                tenant_id=None,
                scope_type="platform",
                scope_id=None,
                created_by_id=user.id,
            ),
        ])
        await db.commit()

        await product_db.execute(text(
            """
            INSERT INTO users (id, private_chat_started, is_active, created_at, last_seen_at)
            VALUES
                (1, TRUE, TRUE, :recent, :half_hour),
                (2, TRUE, FALSE, :older, :two_hours),
                (3, FALSE, TRUE, :older, NULL)
            """
        ), {
            "recent": now - timedelta(hours=12),
            "older": now - timedelta(days=10),
            "half_hour": now - timedelta(minutes=30),
            "two_hours": now - timedelta(hours=2),
        })
        await product_db.execute(text(
            "INSERT INTO schools (id, is_active) VALUES (1, TRUE), (2, FALSE)"
        ))
        await product_db.execute(text(
            "INSERT INTO classes (id, is_active, group_chat_id) VALUES "
            "(1, TRUE, -100123456), (2, FALSE, NULL)"
        ))
        await product_db.execute(text(
            "INSERT INTO subjects (id) VALUES (1), (2), (3)"
        ))
        await product_db.execute(text(
            "INSERT INTO public.class_memberships (id, user_id, is_active, role) VALUES "
            "(1, 1, TRUE, 'STUDENT'), (2, 1, TRUE, 'CLASS_MANAGER'), "
            "(3, 2, TRUE, 'STUDENT'), (4, 1, TRUE, 'STUDENT')"
        ))
        await product_db.commit()

        report = await get_platform_report(
            db,
            actor_id=user.id,
            product_db=product_db,
        )
        assert report["source_is_product_db"] == 1
        assert report["total_users"] == 3
        assert report["started_users"] == 2
        assert report["active_users"] == 2
        assert report["active_started_users"] == 1
        assert report["active_students"] == 1
        assert report["active_memberships"] == 4
        assert report["new_users_1d"] == 1
        assert report["new_users_7d"] == 1
        assert report["new_users_30d"] == 3
        assert report["active_users_1h"] == 1
        assert report["active_users_4h"] == 1
        assert report["active_users_12h"] == 1
        assert report["active_users_24h"] == 1
        assert report["schools"] == 2
        assert report["active_schools"] == 1
        assert report["classes"] == 2
        assert report["active_classes"] == 1
        assert report["subjects"] == 3
        assert report["connected_groups"] == 1

    await product_engine.dispose()
    await work_engine.dispose()
