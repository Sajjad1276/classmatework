from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    AcademicYear,
    AuditEvent,
    ClassGroup,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    School,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.policy import has_permission


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



def test_audit_events_are_immutable_through_orm() -> None:
    with pytest.raises(RuntimeError, match="append-only"):
        from app.db.models import _reject_audit_mutation

        _reject_audit_mutation(None, None, AuditEvent(id=uuid4()))



@pytest.mark.asyncio
async def test_tenant_role_inherits_permission_to_school_descendant() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        tenant = Tenant(slug="inherit-tenant", name="Inheritance Tenant")
        user = User(
            email="tenant-admin@example.com",
            display_name="Tenant Admin",
        )
        db.add_all([tenant, user])
        await db.flush()
        school = School(
            tenant_id=tenant.id, name="School One", code="SCH-1", status="active"
        )
        role = Role(tenant_id=tenant.id, key="tenant_admin", name="Tenant Admin")
        permission = Permission(key="academic_year:create", description="Create academic year")
        db.add_all([school, role, permission])
        await db.flush()
        db.add_all([
            RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"),
            RoleBinding(
                user_id=user.id,
                role_id=role.id,
                tenant_id=tenant.id,
                scope_type="tenant",
                scope_id=tenant.id,
            ),
            TenantMembership(tenant_id=tenant.id, user_id=user.id, status="active"),
        ])
        await db.commit()

        assert await has_permission(
            db,
            user_id=user.id,
            permission_key="academic_year:create",
            tenant_id=tenant.id,
            scope_type="school",
            scope_id=school.id,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_school_role_inherits_to_its_class_but_not_sibling_school() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        tenant = Tenant(slug="class-scope-tenant", name="Class Scope Tenant")
        user = User(
            email="school-admin@example.com",
            display_name="School Admin",
        )
        db.add_all([tenant, user])
        await db.flush()

        school_one = School(
            tenant_id=tenant.id, name="School One", code="S1", status="active"
        )
        school_two = School(
            tenant_id=tenant.id, name="School Two", code="S2", status="active"
        )
        db.add_all([school_one, school_two])
        await db.flush()
        year_one = AcademicYear(
            tenant_id=tenant.id, school_id=school_one.id, name="1405",
            starts_on=__import__("datetime").date(2026, 3, 21),
            ends_on=__import__("datetime").date(2027, 3, 20), status="active",
        )
        year_two = AcademicYear(
            tenant_id=tenant.id, school_id=school_two.id, name="1405",
            starts_on=__import__("datetime").date(2026, 3, 21),
            ends_on=__import__("datetime").date(2027, 3, 20), status="active",
        )
        db.add_all([year_one, year_two])
        await db.flush()
        class_one = ClassGroup(
            tenant_id=tenant.id, school_id=school_one.id, academic_year_id=year_one.id,
            name="Class A", code="A", grade="10", status="active",
        )
        class_two = ClassGroup(
            tenant_id=tenant.id, school_id=school_two.id, academic_year_id=year_two.id,
            name="Class B", code="B", grade="10", status="active",
        )
        role = Role(tenant_id=tenant.id, key="school_admin", name="School Admin")
        permission = Permission(key="class:create", description="Create class")
        db.add_all([class_one, class_two, role, permission])
        await db.flush()
        db.add_all([
            RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"),
            RoleBinding(
                user_id=user.id, role_id=role.id, tenant_id=tenant.id,
                scope_type="school", scope_id=school_one.id,
            ),
            TenantMembership(tenant_id=tenant.id, user_id=user.id, status="active"),
        ])
        await db.commit()

        assert await has_permission(
            db, user_id=user.id, permission_key="class:create",
            tenant_id=tenant.id, scope_type="class", scope_id=class_one.id,
        )
        assert not await has_permission(
            db, user_id=user.id, permission_key="class:create",
            tenant_id=tenant.id, scope_type="class", scope_id=class_two.id,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_deny_on_descendant_overrides_inherited_parent_allow() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        tenant = Tenant(slug="deny-child-tenant", name="Deny Child Tenant")
        user = User(
            email="deny-child@example.com",
            display_name="Deny Child",
        )
        db.add_all([tenant, user])
        await db.flush()
        school = School(
            tenant_id=tenant.id, name="Restricted School", code="RS", status="active"
        )
        allow_role = Role(tenant_id=tenant.id, key="tenant_allow", name="Tenant Allow")
        deny_role = Role(tenant_id=tenant.id, key="school_deny", name="School Deny")
        permission = Permission(key="school:read", description="Read schools")
        db.add_all([school, allow_role, deny_role, permission])
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
                scope_type="school", scope_id=school.id,
            ),
            TenantMembership(tenant_id=tenant.id, user_id=user.id, status="active"),
        ])
        await db.commit()

        assert not await has_permission(
            db, user_id=user.id, permission_key="school:read",
            tenant_id=tenant.id, scope_type="school", scope_id=school.id,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_scope_or_cross_tenant_scope_is_denied() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        tenant_one = Tenant(slug="scope-one", name="Scope One")
        tenant_two = Tenant(slug="scope-two", name="Scope Two")
        user = User(
            email="cross-scope@example.com",
            display_name="Cross Scope",
            password_hash=hash_password("a-long-test-password"),
        )
        db.add_all([tenant_one, tenant_two, user])
        await db.flush()
        school = School(
            tenant_id=tenant_two.id, name="Other Tenant School", code="OTS", status="active"
        )
        role = Role(tenant_id=tenant_one.id, key="scope_admin", name="Scope Admin")
        permission = Permission(key="school:read", description="Read school")
        db.add_all([school, role, permission])
        await db.flush()
        db.add_all([
            RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"),
            RoleBinding(
                user_id=user.id, role_id=role.id, tenant_id=tenant_one.id,
                scope_type="tenant", scope_id=tenant_one.id,
            ),
            TenantMembership(tenant_id=tenant_one.id, user_id=user.id, status="active"),
        ])
        await db.commit()

        assert not await has_permission(
            db, user_id=user.id, permission_key="school:read",
            tenant_id=tenant_one.id, scope_type="school", scope_id=school.id,
        )
        assert not await has_permission(
            db, user_id=user.id, permission_key="school:read",
            tenant_id=tenant_one.id, scope_type="unknown", scope_id=school.id,
        )

    await engine.dispose()
