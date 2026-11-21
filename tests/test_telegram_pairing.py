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
    User,
)
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


@pytest.mark.asyncio
async def test_pairing_token_is_bound_to_target_and_consumed_once() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner = User(display_name="Platform Owner", status="active")
        role = Role(tenant_id=None, key="platform_owner", name="Platform Owner", is_system=True)
        permission = Permission(
            key="platform:users:manage",
            description="Invite platform administrators",
        )
        db.add_all([owner, role, permission])
        await db.flush()
        db.add_all([
            RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"),
            RoleBinding(
                user_id=owner.id,
                role_id=role.id,
                tenant_id=None,
                scope_type="platform",
                scope_id=None,
            ),
        ])
        await db.commit()

        invite = await create_admin_pairing(
            db,
            actor_id=owner.id,
            target_telegram_user_id=712345678,
            display_name="School Operator",
        )
        pairing = (
            await db.execute(
                select(AdminPairingToken).where(
                    AdminPairingToken.user_id == invite.user_id
                )
            )
        ).scalar_one()

        assert pairing.token_hash == digest_secret(invite.raw_token)
        assert invite.raw_token not in pairing.token_hash
        assert pairing.consumed_at is None

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invite.raw_token,
                telegram_user_id=712345679,
                username="wrong_user",
                first_name="Wrong",
                last_name=None,
            )

        account = await consume_admin_pairing(
            db,
            raw_token=invite.raw_token,
            telegram_user_id=712345678,
            username="school_operator",
            first_name="School",
            last_name="Operator",
        )
        assert account.telegram_user_id == 712345678
        assert account.username == "school_operator"
        assert account.status == "active"

        invited_user = await db.get(User, invite.user_id)
        assert invited_user is not None
        assert invited_user.status == "active"

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invite.raw_token,
                telegram_user_id=712345678,
                username="school_operator",
                first_name="School",
                last_name="Operator",
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_pairing_requires_platform_users_manage_permission() -> None:
    engine, factory = await _database()
    async with factory() as db:
        regular_user = User(display_name="Read Only", status="active")
        db.add(regular_user)
        await db.commit()

        with pytest.raises(PairingNotAllowed):
            await create_admin_pairing(
                db,
                actor_id=regular_user.id,
                target_telegram_user_id=812345678,
                display_name="Unauthorized",
            )

        count = (
            await db.execute(select(AdminPairingToken.id))
        ).scalars().all()
        assert count == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_pairing_token_cannot_be_consumed() -> None:
    engine, factory = await _database()
    async with factory() as db:
        owner = User(display_name="Owner", status="active")
        role = Role(tenant_id=None, key="platform_owner", name="Platform Owner", is_system=True)
        permission = Permission(
            key="platform:users:manage",
            description="Invite platform administrators",
        )
        db.add_all([owner, role, permission])
        await db.flush()
        db.add_all([
            RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"),
            RoleBinding(
                user_id=owner.id,
                role_id=role.id,
                tenant_id=None,
                scope_type="platform",
                scope_id=None,
            ),
        ])
        await db.commit()

        invite = await create_admin_pairing(
            db,
            actor_id=owner.id,
            target_telegram_user_id=912345678,
            display_name="Expired Invite",
        )
        pairing = (
            await db.execute(
                select(AdminPairingToken).where(
                    AdminPairingToken.user_id == invite.user_id
                )
            )
        ).scalar_one()
        pairing.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

        with pytest.raises(PairingRejected):
            await consume_admin_pairing(
                db,
                raw_token=invite.raw_token,
                telegram_user_id=912345678,
                username=None,
                first_name="Expired",
                last_name=None,
            )

        assert await db.get(TelegramAccount, invite.user_id) is None

    await engine.dispose()
