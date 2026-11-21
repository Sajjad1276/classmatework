from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.errors import AccessDenied
from app.application.tenant_access import (
    create_tenant_role,
    list_tenant_members,
    list_tenant_roles,
)
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    TelegramAccount,
    Tenant,
    TenantMembership,
    User,
)


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_manager_and_member(db):
    tenant = Tenant(
        slug=f"access-{uuid4().hex[:8]}",
        name="Access Tenant",
        status="active",
    )
    manager = User(display_name="Tenant Manager", status="active")
    member = User(display_name="School Teacher", status="active")
    manager_role = Role(
        tenant_id=None,
        key=f"tenant_manager_{uuid4().hex[:8]}",
        name="Tenant Manager",
        is_system=True,
    )
    member_role = Role(
        tenant_id=None,
        key=f"tenant_member_{uuid4().hex[:8]}",
        name="Tenant Member",
        is_system=True,
    )
    permission_keys = (
        "role:manage",
        "membership:read",
        "membership:invite",
        "school:read",
        "class:create",
        "platform:tenants:create",
    )
    permissions = [Permission(key=key, description=key) for key in permission_keys]
    db.add_all([tenant, manager, member, manager_role, member_role, *permissions])
    await db.flush()

    manager_role.tenant_id = tenant.id
    member_role.tenant_id = tenant.id
    permission_by_key = {permission.key: permission for permission in permissions}
    manager_grants = (
        "role:manage",
        "membership:read",
        "membership:invite",
        "school:read",
        "class:create",
    )
    db.add_all(
        [
            RolePermission(
                role_id=manager_role.id,
                permission_id=permission_by_key[key].id,
                effect="allow",
            )
            for key in manager_grants
        ]
        + [
            RolePermission(
                role_id=member_role.id,
                permission_id=permission_by_key["school:read"].id,
                effect="allow",
            )
        ]
        + [
            RoleBinding(
                user_id=manager.id,
                role_id=manager_role.id,
                tenant_id=tenant.id,
                scope_type="tenant",
                scope_id=tenant.id,
                created_by_id=manager.id,
            ),
            RoleBinding(
                user_id=member.id,
                role_id=member_role.id,
                tenant_id=tenant.id,
                scope_type="tenant",
                scope_id=tenant.id,
                created_by_id=manager.id,
            ),
            TenantMembership(
                user_id=manager.id,
                tenant_id=tenant.id,
                status="active",
            ),
            TenantMembership(
                user_id=member.id,
                tenant_id=tenant.id,
                status="active",
            ),
            TelegramAccount(
                user_id=manager.id,
                telegram_user_id=101_123_456,
                status="active",
            ),
            TelegramAccount(
                user_id=member.id,
                telegram_user_id=102_123_456,
                status="active",
            ),
        ]
    )
    await db.commit()
    return tenant, manager, member


@pytest.mark.asyncio
async def test_tenant_manager_can_create_role_from_permissions_they_hold() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)

        role = await create_tenant_role(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            key="teacher_assistant",
            name="دستیار معلم",
            permission_keys=("school:read", "class:create"),
        )
        assert role.key == "teacher_assistant"
        assert role.tenant_id == tenant.id
        assert not role.is_system

        rows = await list_tenant_roles(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
        )
        created = next(item for item in rows if item.key == role.key)
        assert {key for key, effect in created.permissions if effect == "allow"} == {
            "school:read",
            "class:create",
        }

        audit = (
            await db.execute(
                select(AuditEvent.event_type).where(
                    AuditEvent.event_type == "role.tenant_created"
                )
            )
        ).scalars().all()
        assert audit == ["role.tenant_created"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_manager_cannot_create_role_with_permission_they_do_not_hold() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)

        with pytest.raises(AccessDenied):
            await create_tenant_role(
                db,
                actor_id=manager.id,
                tenant_id=tenant.id,
                key="overpowered_role",
                name="Overpowered Role",
                permission_keys=("platform:tenants:create",),
            )

        assert (
            await db.execute(
                select(Role.id).where(
                    Role.tenant_id == tenant.id,
                    Role.key == "overpowered_role",
                )
            )
        ).scalar_one_or_none() is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_membership_directory_only_returns_active_members_in_authorized_tenant() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, member = await _seed_manager_and_member(db)
        rows = await list_tenant_members(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
        )
        assert {item.user_id for item in rows} == {manager.id, member.id}

        member_view = next(item for item in rows if item.user_id == member.id)
        assert member_view.display_name == "School Teacher"
        assert member_view.telegram_user_id == 102_123_456
        assert len(member_view.role_keys) == 1
        assert member_view.role_keys[0].startswith("tenant_member_")
    await engine.dispose()



@pytest.mark.asyncio
async def test_member_directory_hides_blocked_telegram_id_and_rejects_other_tenant() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, member = await _seed_manager_and_member(db)
        account = (
            await db.execute(
                select(TelegramAccount).where(TelegramAccount.user_id == member.id)
            )
        ).scalar_one()
        account.status = "blocked"
        other_tenant = Tenant(
            slug=f"outside-{uuid4().hex[:8]}",
            name="Outside Tenant",
            status="active",
        )
        db.add(other_tenant)
        await db.commit()

        rows = await list_tenant_members(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
        )
        member_view = next(item for item in rows if item.user_id == member.id)
        assert member_view.telegram_user_id is None

        with pytest.raises(AccessDenied):
            await list_tenant_members(
                db,
                actor_id=manager.id,
                tenant_id=other_tenant.id,
            )
    await engine.dispose()
