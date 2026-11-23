from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.errors import AccessDenied, InvalidApplicationInput, ResourceConflict
from app.application.tenant_access import (
    create_tenant_role,
    list_admin_invitations,
    list_assignable_tenant_permissions,
    list_tenant_members,
    list_tenant_members_page,
    list_tenant_roles,
    preview_tenant_member_reactivation,
    preview_tenant_member_suspension,
    reactivate_tenant_member,
    suspend_tenant_member,
)
from app.db.base import Base
from app.db.models import (
    AdminPairingToken,
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
from app.modules.access.policy import has_permission
from app.modules.identity.pairing import create_admin_pairing


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
        "membership:manage",
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
        "membership:manage",
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
async def test_assignable_permission_catalog_is_scoped_to_actor_and_tenant() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)
        permissions = await list_assignable_tenant_permissions(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
        )

        assert set(permissions) == {
            "role:manage",
            "membership:read",
            "membership:invite",
            "membership:manage",
            "school:read",
            "class:create",
        }
        assert not any(key.startswith("platform:") for key in permissions)
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



@pytest.mark.asyncio
async def test_tenant_invitation_directory_is_limited_to_the_managers_tenant() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)
        tenant_role = (
            await db.execute(
                select(Role).where(
                    Role.tenant_id == tenant.id,
                    Role.key.like("tenant_member_%"),
                )
            )
        ).scalar_one()
        own_invite = await create_admin_pairing(
            db,
            actor_id=manager.id,
            target_telegram_user_id=103_123_456,
            display_name="Own Tenant Invite",
            role_key=tenant_role.key,
            scope_type="tenant",
            scope_id=tenant.id,
        )

        other_tenant = Tenant(
            slug=f"another-{uuid4().hex[:8]}",
            name="Another Tenant",
            status="active",
        )
        other_user = User(
            display_name="Other Tenant Invite",
            status="pending",
            email=None,
            password_hash=None,
        )
        other_role = Role(
            tenant_id=other_tenant.id,
            key=f"other_invite_{uuid4().hex[:8]}",
            name="Other Invite Role",
            is_system=False,
        )
        db.add_all([other_tenant, other_user, other_role])
        await db.flush()
        other_pairing = AdminPairingToken(
            user_id=other_user.id,
            target_telegram_user_id=104_123_456,
            token_hash=uuid4().hex + uuid4().hex,
            role_id=other_role.id,
            tenant_id=other_tenant.id,
            scope_type="tenant",
            scope_id=other_tenant.id,
            created_by_id=manager.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db.add(other_pairing)
        await db.commit()

        expired_invite = await create_admin_pairing(
            db,
            actor_id=manager.id,
            target_telegram_user_id=103_123_457,
            display_name="Expired Tenant Invite",
            role_key=tenant_role.key,
            scope_type="tenant",
            scope_id=tenant.id,
        )
        expired_pairing = await db.get(AdminPairingToken, expired_invite.pairing_id)
        assert expired_pairing is not None
        now = datetime.now(UTC)
        expired_pairing.created_at = now - timedelta(minutes=2)
        expired_pairing.expires_at = now - timedelta(minutes=1)
        await db.commit()

        invitations = await list_admin_invitations(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
        )
        assert len(invitations) == 2
        by_id = {item.invitation_id: item for item in invitations}
        assert by_id[own_invite.pairing_id].target_telegram_user_id == 103_123_456
        assert by_id[own_invite.pairing_id].role_key == tenant_role.key
        assert by_id[own_invite.pairing_id].status == "pending"
        assert by_id[expired_invite.pairing_id].status == "expired"
        assert all(not hasattr(item, "raw_token") for item in invitations)

        with pytest.raises(AccessDenied):
            await list_admin_invitations(
                db,
                actor_id=manager.id,
                tenant_id=other_tenant.id,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_platform_invitation_directory_requires_global_assignment_authority() -> None:
    engine, factory = await _database()
    async with factory() as db:
        _, manager, _ = await _seed_manager_and_member(db)
        with pytest.raises(AccessDenied):
            await list_admin_invitations(
                db,
                actor_id=manager.id,
                tenant_id=None,
            )
    await engine.dispose()



@pytest.mark.asyncio
async def test_suspending_regular_member_ends_only_that_tenant_membership_and_roles() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, member = await _seed_manager_and_member(db)

        preview = await preview_tenant_member_suspension(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            member_user_id=member.id,
        )
        assert preview.user_id == member.id
        assert preview.display_name == "School Teacher"
        assert not preview.is_tenant_admin

        result = await suspend_tenant_member(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            member_user_id=member.id,
            reason="Repeated violation of tenant access policy",
        )
        assert result.user_id == member.id
        assert result.ended_role_bindings == 1

        membership = (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == member.id,
                )
            )
        ).scalar_one()
        assert membership.status == "suspended"
        assert membership.ends_at is not None

        binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.tenant_id == tenant.id,
                    RoleBinding.user_id == member.id,
                )
            )
        ).scalar_one()
        assert binding.ends_at is not None

        assert not await has_permission(
            db,
            user_id=member.id,
            permission_key="school:read",
            tenant_id=tenant.id,
            scope_type="tenant",
            scope_id=tenant.id,
        )
        remaining = await list_tenant_members(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
        )
        assert [row.user_id for row in remaining] == [manager.id]

        audit = (
            await db.execute(
                select(AuditEvent.event_type).where(
                    AuditEvent.event_type == "membership.tenant_member_suspended"
                )
            )
        ).scalars().all()
        assert audit == ["membership.tenant_member_suspended"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_admin_cannot_suspend_themselves() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)
        with pytest.raises(InvalidApplicationInput):
            await suspend_tenant_member(
                db,
                actor_id=manager.id,
                tenant_id=tenant.id,
                member_user_id=manager.id,
                reason="Self suspension should never be allowed",
            )

        membership = (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == manager.id,
                )
            )
        ).scalar_one()
        assert membership.status == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_operator_cannot_suspend_the_last_active_tenant_admin() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, last_admin, _ = await _seed_manager_and_member(db)
        membership_manage = (
            await db.execute(
                select(Permission).where(Permission.key == "membership:manage")
            )
        ).scalar_one()
        operator = User(display_name="Membership Operator", status="active")
        operator_role = Role(
            tenant_id=tenant.id,
            key=f"membership_operator_{uuid4().hex[:8]}",
            name="Membership Operator",
            is_system=False,
        )
        db.add_all([operator, operator_role])
        await db.flush()
        db.add_all(
            [
                RolePermission(
                    role_id=operator_role.id,
                    permission_id=membership_manage.id,
                    effect="allow",
                ),
                RoleBinding(
                    user_id=operator.id,
                    role_id=operator_role.id,
                    tenant_id=tenant.id,
                    scope_type="tenant",
                    scope_id=tenant.id,
                    created_by_id=last_admin.id,
                ),
                TenantMembership(
                    user_id=operator.id,
                    tenant_id=tenant.id,
                    status="active",
                ),
            ]
        )
        await db.commit()

        with pytest.raises(InvalidApplicationInput, match="آخرین مدیر فعال"):
            await suspend_tenant_member(
                db,
                actor_id=operator.id,
                tenant_id=tenant.id,
                member_user_id=last_admin.id,
                reason="Attempt to suspend the only tenant administrator",
            )

        still_active = (
            await db.execute(
                select(TenantMembership.status).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == last_admin.id,
                )
            )
        ).scalar_one()
        assert still_active == "active"
    await engine.dispose()



@pytest.mark.asyncio
async def test_suspended_member_can_be_reactivated_with_a_fresh_scoped_role_binding() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, member = await _seed_manager_and_member(db)
        old_binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == member.id,
                    RoleBinding.tenant_id == tenant.id,
                )
            )
        ).scalar_one()
        old_binding_id = old_binding.id

        await suspend_tenant_member(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            member_user_id=member.id,
            reason="Temporary access suspension for investigation",
        )
        preview = await preview_tenant_member_reactivation(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            member_user_id=member.id,
            role_key=old_binding and (
                await db.execute(
                    select(Role.key)
                    .join(RoleBinding, RoleBinding.role_id == Role.id)
                    .where(RoleBinding.id == old_binding_id)
                )
            ).scalar_one(),
        )
        assert preview.user_id == member.id
        assert preview.permission_keys == ("school:read",)

        reactivated = await reactivate_tenant_member(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            member_user_id=member.id,
            role_key=preview.role_key,
            reason="Investigation completed and access approved",
        )
        assert reactivated.user_id == member.id
        assert reactivated.role_key == preview.role_key

        membership = (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == member.id,
                )
            )
        ).scalar_one()
        assert membership.status == "active"
        assert membership.ends_at is None

        bindings = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == member.id,
                    RoleBinding.tenant_id == tenant.id,
                )
            )
        ).scalars().all()
        assert len(bindings) == 2
        previous = next(binding for binding in bindings if binding.id == old_binding_id)
        current = next(binding for binding in bindings if binding.id != old_binding_id)
        assert previous.ends_at is not None
        assert current.ends_at is None
        assert current.role_id == old_binding.role_id

        assert await has_permission(
            db,
            user_id=member.id,
            permission_key="school:read",
            tenant_id=tenant.id,
            scope_type="tenant",
            scope_id=tenant.id,
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_reactivation_requires_active_telegram_link_and_role_permissions() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, member = await _seed_manager_and_member(db)
        await suspend_tenant_member(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            member_user_id=member.id,
            reason="Temporary member suspension for test",
        )
        account = (
            await db.execute(
                select(TelegramAccount).where(TelegramAccount.user_id == member.id)
            )
        ).scalar_one()
        account.status = "blocked"
        await db.commit()

        with pytest.raises(ResourceConflict, match="تلگرام"):
            await preview_tenant_member_reactivation(
                db,
                actor_id=manager.id,
                tenant_id=tenant.id,
                member_user_id=member.id,
                role_key=f"tenant_member_{uuid4().hex[:8]}",
            )

        with pytest.raises(ResourceConflict, match="تلگرام"):
            await reactivate_tenant_member(
                db,
                actor_id=manager.id,
                tenant_id=tenant.id,
                member_user_id=member.id,
                role_key="does_not_matter",
                reason="Attempt reactivation with a blocked Telegram link",
            )
    await engine.dispose()



@pytest.mark.asyncio
async def test_membership_directory_paginates_and_searches_by_name_or_telegram_id() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)
        for index in range(11):
            user = User(display_name=f"Teacher {index:02d}", status="active")
            db.add(user)
            await db.flush()
            db.add(
                TenantMembership(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    status="active",
                )
            )
        searched_user = User(display_name="Searchable Physics Teacher", status="active")
        db.add(searched_user)
        await db.flush()
        db.add_all(
            [
                TenantMembership(
                    user_id=searched_user.id,
                    tenant_id=tenant.id,
                    status="active",
                ),
                TelegramAccount(
                    user_id=searched_user.id,
                    telegram_user_id=555_001_222,
                    status="active",
                ),
            ]
        )
        await db.commit()

        first_page = await list_tenant_members_page(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            page=1,
            page_size=8,
        )
        second_page = await list_tenant_members_page(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            page=2,
            page_size=8,
        )
        assert first_page.total_items == 14
        assert first_page.total_pages == 2
        assert first_page.page == 1
        assert len(first_page.items) == 8
        assert second_page.page == 2
        assert len(second_page.items) == 6
        assert {item.user_id for item in first_page.items}.isdisjoint(
            {item.user_id for item in second_page.items}
        )

        by_name = await list_tenant_members_page(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            search_query="Physics",
        )
        assert by_name.total_items == 1
        assert by_name.items[0].user_id == searched_user.id

        by_telegram_id = await list_tenant_members_page(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            search_query="555001222",
        )
        assert by_telegram_id.total_items == 1
        assert by_telegram_id.items[0].user_id == searched_user.id

        clamped_page = await list_tenant_members_page(
            db,
            actor_id=manager.id,
            tenant_id=tenant.id,
            page=99,
            page_size=8,
        )
        assert clamped_page.page == 2
        assert len(clamped_page.items) == 6
    await engine.dispose()


@pytest.mark.asyncio
async def test_membership_directory_rejects_invalid_page_and_search_length() -> None:
    engine, factory = await _database()
    async with factory() as db:
        tenant, manager, _ = await _seed_manager_and_member(db)
        with pytest.raises(InvalidApplicationInput):
            await list_tenant_members_page(
                db,
                actor_id=manager.id,
                tenant_id=tenant.id,
                page=0,
            )
        with pytest.raises(InvalidApplicationInput):
            await list_tenant_members_page(
                db,
                actor_id=manager.id,
                tenant_id=tenant.id,
                search_query="x" * 81,
            )
    await engine.dispose()
