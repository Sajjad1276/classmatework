from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    AdminPairingToken,
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
from app.modules.identity.pairing import (
    PairingNotAllowed,
    PairingRejected,
    consume_admin_pairing,
    create_admin_pairing,
)
from app.modules.identity.tokens import digest_secret


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create_platform_actor(db, *, permission_keys: tuple[str, ...] = ()):
    actor = User(display_name="Platform Owner", status="active")
    owner_role = Role(
        tenant_id=None,
        key=f"platform_owner_{uuid4().hex[:8]}",
        name="Platform Owner",
        is_system=True,
    )
    keys = tuple(dict.fromkeys((
        "platform:users:manage",
        "platform:roles:assign",
        *permission_keys,
    )))
    permissions = [Permission(key=key, description=key) for key in keys]
    db.add_all([actor, owner_role, *permissions])
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=owner_role.id, permission_id=p.id, effect="allow")
            for p in permissions
        ]
        + [
            RoleBinding(
                user_id=actor.id,
                role_id=owner_role.id,
                tenant_id=None,
                scope_type="platform",
                scope_id=None,
                created_by_id=actor.id,
            )
        ]
    )
    await db.commit()
    return actor, owner_role


async def _create_role(
    db,
    *,
    key: str,
    tenant_id,
    permission_keys: tuple[str, ...],
) -> Role:
    role = Role(
        tenant_id=tenant_id,
        key=key,
        name=key.replace("_", " ").title(),
        is_system=False,
    )
    db.add(role)
    permissions = []
    for permission_key in permission_keys:
        permission = (
            await db.execute(select(Permission).where(Permission.key == permission_key))
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(key=permission_key, description=permission_key)
            db.add(permission)
            await db.flush()
        permissions.append(permission)
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=role.id, permission_id=p.id, effect="allow")
            for p in permissions
        ]
    )
    await db.commit()
    return role


async def _invite(
    db,
    *,
    actor: User,
    telegram_user_id: int,
    role_key: str,
    scope_type: str = "platform",
    scope_id=None,
):
    return await create_admin_pairing(
        db,
        actor_id=actor.id,
        target_telegram_user_id=telegram_user_id,
        display_name="Invited Administrator",
        role_key=role_key,
        scope_type=scope_type,
        scope_id=scope_id,
    )


@pytest.mark.asyncio
async def test_pairing_binds_platform_role_and_is_target_bound_and_single_use() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner, _ = await _create_platform_actor(
            db,
            permission_keys=("platform:users:read",),
        )
        target_role = await _create_role(
            db,
            key="platform_auditor",
            tenant_id=None,
            permission_keys=("platform:users:read",),
        )
        invitation = await _invite(
            db,
            actor=owner,
            telegram_user_id=712345678,
            role_key=target_role.key,
        )
        pairing = (
            await db.execute(
                select(AdminPairingToken).where(
                    AdminPairingToken.user_id == invitation.user_id
                )
            )
        ).scalar_one()

        assert pairing.token_hash == digest_secret(invitation.raw_token)
        assert invitation.raw_token not in pairing.token_hash
        assert pairing.role_id == target_role.id
        assert pairing.scope_type == "platform"
        assert pairing.scope_id is None
        assert pairing.tenant_id is None

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invitation.raw_token,
                telegram_user_id=712345679,
                username="wrong_user",
                first_name="Wrong",
                last_name=None,
            )

        account = await consume_admin_pairing(
            db,
            raw_token=invitation.raw_token,
            telegram_user_id=712345678,
            username="auditor",
            first_name="Audit",
            last_name="User",
        )
        assert account.telegram_user_id == 712345678
        assert account.status == "active"

        invited_user = await db.get(User, invitation.user_id)
        assert invited_user is not None
        assert invited_user.status == "active"

        assigned_binding = (
            await db.execute(
                select(RoleBinding).where(RoleBinding.user_id == invitation.user_id)
            )
        ).scalar_one()
        assert assigned_binding.role_id == target_role.id
        assert assigned_binding.scope_type == "platform"
        assert assigned_binding.tenant_id is None

        membership = (
            await db.execute(
                select(TenantMembership.id).where(
                    TenantMembership.user_id == invitation.user_id
                )
            )
        ).scalar_one_or_none()
        assert membership is None

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invitation.raw_token,
                telegram_user_id=712345678,
                username="auditor",
                first_name="Audit",
                last_name="User",
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_pairing_creates_membership_and_scope_binding_atomically() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner, _ = await _create_platform_actor(
            db,
            permission_keys=("school:read",),
        )
        tenant = Tenant(
            slug=f"pairing-tenant-{uuid4().hex[:8]}",
            name="Pairing Tenant",
            status="active",
        )
        db.add(tenant)
        await db.flush()
        tenant_role = await _create_role(
            db,
            key="tenant_viewer",
            tenant_id=tenant.id,
            permission_keys=("school:read",),
        )
        invitation = await _invite(
            db,
            actor=owner,
            telegram_user_id=812345678,
            role_key=tenant_role.key,
            scope_type="tenant",
            scope_id=tenant.id,
        )

        account = await consume_admin_pairing(
            db,
            raw_token=invitation.raw_token,
            telegram_user_id=812345678,
            username=None,
            first_name="Tenant",
            last_name="Viewer",
        )
        assert account.user_id == invitation.user_id

        membership = (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.user_id == invitation.user_id,
                    TenantMembership.tenant_id == tenant.id,
                )
            )
        ).scalar_one()
        assert membership.status == "active"

        binding = (
            await db.execute(
                select(RoleBinding).where(RoleBinding.user_id == invitation.user_id)
            )
        ).scalar_one()
        assert binding.role_id == tenant_role.id
        assert binding.scope_type == "tenant"
        assert binding.scope_id == tenant.id
        assert binding.tenant_id == tenant.id

        assert await has_permission(
            db,
            user_id=invitation.user_id,
            permission_key="school:read",
            tenant_id=tenant.id,
            scope_type="tenant",
            scope_id=tenant.id,
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_pairing_requires_user_management_and_role_assignment_authority() -> None:
    engine, factory = await _database()
    async with factory() as db:
        actor = User(display_name="Read Only", status="active")
        db.add(actor)
        await db.commit()

        with pytest.raises(PairingNotAllowed):
            await create_admin_pairing(
                db,
                actor_id=actor.id,
                target_telegram_user_id=912345678,
                display_name="Unauthorized",
                role_key="platform_owner",
                scope_type="platform",
                scope_id=None,
            )

        assert (
            await db.execute(select(AdminPairingToken.id))
        ).scalar_one_or_none() is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_inviter_cannot_assign_permissions_they_do_not_hold() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner, _ = await _create_platform_actor(db)
        overpowered_role = await _create_role(
            db,
            key="overpowered",
            tenant_id=None,
            permission_keys=("platform:tenants:create",),
        )

        with pytest.raises(PairingNotAllowed):
            await _invite(
                db,
                actor=owner,
                telegram_user_id=923456789,
                role_key=overpowered_role.key,
            )

        assert (
            await db.execute(select(AdminPairingToken.id))
        ).scalar_one_or_none() is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_pairing_token_cannot_be_consumed() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner, _ = await _create_platform_actor(
            db,
            permission_keys=("platform:users:read",),
        )
        role = await _create_role(
            db,
            key="expired_auditor",
            tenant_id=None,
            permission_keys=("platform:users:read",),
        )
        invitation = await _invite(
            db,
            actor=owner,
            telegram_user_id=934567890,
            role_key=role.key,
        )
        pairing = (
            await db.execute(
                select(AdminPairingToken).where(
                    AdminPairingToken.user_id == invitation.user_id
                )
            )
        ).scalar_one()
        now = datetime.now(UTC)
        pairing.created_at = now - timedelta(minutes=2)
        pairing.expires_at = now - timedelta(minutes=1)
        await db.commit()

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invitation.raw_token,
                telegram_user_id=934567890,
                username=None,
                first_name="Expired",
                last_name=None,
            )

        linked = (
            await db.execute(
                select(TelegramAccount.id).where(
                    TelegramAccount.user_id == invitation.user_id
                )
            )
        ).scalar_one_or_none()
        assert linked is None
        binding = (
            await db.execute(
                select(RoleBinding.id).where(RoleBinding.user_id == invitation.user_id)
            )
        ).scalar_one_or_none()
        assert binding is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_invitation_is_rejected_when_inviter_loses_assignment_authority() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner, owner_role = await _create_platform_actor(
            db,
            permission_keys=("platform:users:read",),
        )
        target_role = await _create_role(
            db,
            key="revoked_auditor",
            tenant_id=None,
            permission_keys=("platform:users:read",),
        )
        invitation = await _invite(
            db,
            actor=owner,
            telegram_user_id=945678901,
            role_key=target_role.key,
        )

        assign_permission = (
            await db.execute(
                select(Permission).where(Permission.key == "platform:roles:assign")
            )
        ).scalar_one()
        owner_grant = (
            await db.execute(
                select(RolePermission).where(
                    RolePermission.role_id == owner_role.id,
                    RolePermission.permission_id == assign_permission.id,
                )
            )
        ).scalar_one()
        owner_grant.effect = "deny"
        await db.commit()

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invitation.raw_token,
                telegram_user_id=945678901,
                username=None,
                first_name="Late",
                last_name=None,
            )

        assert (
            await db.execute(
                select(TelegramAccount.id).where(
                    TelegramAccount.telegram_user_id == 945678901
                )
            )
        ).scalar_one_or_none() is None
        assert (
            await db.execute(
                select(RoleBinding.id).where(
                    RoleBinding.user_id == invitation.user_id
                )
            )
        ).scalar_one_or_none() is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_active_invitation_for_same_telegram_id_is_rejected() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner, _ = await _create_platform_actor(
            db,
            permission_keys=("platform:users:read",),
        )
        role = await _create_role(
            db,
            key="duplicate_test",
            tenant_id=None,
            permission_keys=("platform:users:read",),
        )
        await _invite(
            db,
            actor=owner,
            telegram_user_id=956789012,
            role_key=role.key,
        )
        with pytest.raises(ValueError, match="active invitation"):
            await _invite(
                db,
                actor=owner,
                telegram_user_id=956789012,
                role_key=role.key,
            )
    await engine.dispose()
