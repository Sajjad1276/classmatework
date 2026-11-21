from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.errors import AccessDenied, ResourceConflict
from app.application.tenants import create_tenant, list_tenants
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    Tenant,
    User,
)


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _platform_owner(db):
    user = User(display_name="Platform Owner", status="active")
    role = Role(
        tenant_id=None,
        key=f"owner_{uuid4().hex[:8]}",
        name="Platform Owner",
        is_system=True,
    )
    read_permission = Permission(
        key="platform:tenants:read",
        description="List tenants",
    )
    create_permission = Permission(
        key="platform:tenants:create",
        description="Create tenant",
    )
    db.add_all([user, role, read_permission, create_permission])
    await db.flush()
    db.add_all([
        RolePermission(role_id=role.id, permission_id=read_permission.id, effect="allow"),
        RolePermission(role_id=role.id, permission_id=create_permission.id, effect="allow"),
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
    return user


@pytest.mark.asyncio
async def test_create_and_list_tenant_use_cases_write_audit() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner = await _platform_owner(db)
        tenant = await create_tenant(
            db,
            actor_id=owner.id,
            name="مجموعه آزمایشی",
            slug="sample-tenant",
            timezone="Asia/Tehran",
            locale="fa-IR",
        )
        assert tenant.slug == "sample-tenant"
        assert tenant.status == "active"

        tenants = await list_tenants(db, actor_id=owner.id)
        assert [item.id for item in tenants] == [tenant.id]

        audit_types = (
            await db.execute(select(AuditEvent.event_type).order_by(AuditEvent.created_at))
        ).scalars().all()
        assert "tenant.created" in audit_types
        assert "tenant.directory.read" in audit_types

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_tenant_rejects_duplicate_slug_without_duplicate_row() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner = await _platform_owner(db)
        await create_tenant(
            db,
            actor_id=owner.id,
            name="First Tenant",
            slug="duplicate-tenant",
        )
        with pytest.raises(ResourceConflict):
            await create_tenant(
                db,
                actor_id=owner.id,
                name="Second Tenant",
                slug="duplicate-tenant",
            )
        rows = (
            await db.execute(
                select(Tenant).where(Tenant.slug == "duplicate-tenant")
            )
        ).scalars().all()
        assert len(rows) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_use_case_rejects_actor_without_platform_permission() -> None:
    engine, factory = await _database()
    async with factory() as db:
        user = User(display_name="Read Only", status="active")
        db.add(user)
        await db.commit()

        with pytest.raises(AccessDenied):
            await create_tenant(
                db,
                actor_id=user.id,
                name="Blocked Tenant",
                slug="blocked-tenant",
            )

        count = (await db.execute(select(Tenant.id))).scalars().all()
        assert count == []

    await engine.dispose()
