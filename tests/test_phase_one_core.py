from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.policy import has_permission
from app.modules.identity.passwords import hash_password, needs_rehash, verify_password


@pytest.mark.asyncio
async def test_tenant_permission_requires_membership_and_is_tenant_scoped() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        tenant_one = Tenant(slug="one-school", name="One School")
        tenant_two = Tenant(slug="two-school", name="Two School")
        user = User(
            email="manager@example.com",
            display_name="Tenant Manager",
            password_hash=hash_password("a-long-test-password"),
        )
        db.add_all([tenant_one, tenant_two, user])
        await db.flush()

        role = Role(tenant_id=tenant_one.id, key="school_admin", name="School Admin")
        permission = Permission(key="school:read", description="Read school records")
        db.add_all([role, permission])
        await db.flush()
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"))
        db.add(
            RoleBinding(
                user_id=user.id,
                role_id=role.id,
                tenant_id=tenant_one.id,
                scope_type="tenant",
                scope_id=tenant_one.id,
            )
        )
        await db.commit()

        assert not await has_permission(
            db,
            user_id=user.id,
            permission_key="school:read",
            tenant_id=tenant_one.id,
            scope_type="tenant",
            scope_id=tenant_one.id,
        )

        db.add(TenantMembership(tenant_id=tenant_one.id, user_id=user.id, status="active"))
        await db.commit()

        assert await has_permission(
            db,
            user_id=user.id,
            permission_key="school:read",
            tenant_id=tenant_one.id,
            scope_type="tenant",
            scope_id=tenant_one.id,
        )
        assert not await has_permission(
            db,
            user_id=user.id,
            permission_key="school:read",
            tenant_id=tenant_two.id,
            scope_type="tenant",
            scope_id=tenant_two.id,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_deny_overrides_allow() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        tenant = Tenant(slug="deny-test", name="Deny Test")
        user = User(
            email="reviewer@example.com",
            display_name="Reviewer",
            password_hash=hash_password("another-long-password"),
        )
        db.add_all([tenant, user])
        await db.flush()
        allow_role = Role(tenant_id=tenant.id, key="allow_role", name="Allow role")
        deny_role = Role(tenant_id=tenant.id, key="deny_role", name="Deny role")
        permission = Permission(key="audit:read", description="Read audit")
        db.add_all([allow_role, deny_role, permission])
        await db.flush()
        db.add_all([
            RolePermission(role_id=allow_role.id, permission_id=permission.id, effect="allow"),
            RolePermission(role_id=deny_role.id, permission_id=permission.id, effect="deny"),
            RoleBinding(
                user_id=user.id, role_id=allow_role.id, tenant_id=tenant.id,
                scope_type="tenant", scope_id=tenant.id,
            ),
            RoleBinding(
                user_id=user.id, role_id=deny_role.id, tenant_id=tenant.id,
                scope_type="tenant", scope_id=tenant.id,
            ),
            TenantMembership(tenant_id=tenant.id, user_id=user.id, status="active"),
        ])
        await db.commit()
        assert not await has_permission(
            db,
            user_id=user.id,
            permission_key="audit:read",
            tenant_id=tenant.id,
            scope_type="tenant",
            scope_id=tenant.id,
        )

    await engine.dispose()


def test_argon2_password_hash_is_not_the_password() -> None:
    password = "Correct-Horse-Battery-1"
    encoded = hash_password(password)

    assert encoded != password
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, password)
    assert not verify_password(encoded, "incorrect-password")
    assert not needs_rehash(encoded)


def test_audit_events_are_immutable_through_orm() -> None:
    with pytest.raises(RuntimeError, match="append-only"):
        from app.db.models import _reject_audit_mutation

        _reject_audit_mutation(None, None, AuditEvent(id=uuid4()))
