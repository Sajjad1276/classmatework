from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    AuditEvent,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    TelegramAccount,
    User,
)
from app.db.runtime import create_database_engine, create_session_factory

PLATFORM_PERMISSIONS = {
    "platform:tenants:read": "Read platform tenant directory",
    "platform:tenants:create": "Create a tenant",
    "platform:users:read": "Read platform user directory",
    "platform:users:manage": "Manage platform users and role assignments",
    "school:read": "Read schools in an authorized tenant or school scope",
    "school:create": "Create schools within an authorized tenant",
    "academic_year:create": "Create academic years within a school",
    "class:create": "Create classes within a school",
    "subject:create": "Create subjects within a school",
    "membership:read": "Read tenant memberships",
    "membership:invite": "Invite users into a tenant",
    "role:manage": "Create and manage tenant-scoped roles",
    "audit:read": "Read audit events within an authorized tenant",
}


async def _create_platform_owner(telegram_user_id: int, display_name: str) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as db:
            existing_owner = (
                await db.execute(
                    select(RoleBinding.id)
                    .join(Role, Role.id == RoleBinding.role_id)
                    .where(Role.key == "platform_owner", RoleBinding.scope_type == "platform")
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing_owner is not None:
                raise RuntimeError(
                    "A platform owner is already configured; refusing to create another."
                )

            existing_account = (
                await db.execute(
                    select(TelegramAccount.id).where(
                        TelegramAccount.telegram_user_id == telegram_user_id
                    )
                )
            ).scalar_one_or_none()
            if existing_account is not None:
                raise RuntimeError("This Telegram account is already linked to a user.")

            user = User(
                email=None,
                display_name=display_name,
                password_hash=None,
                status="active",
            )
            db.add(user)
            await db.flush()

            account = TelegramAccount(
                user_id=user.id,
                telegram_user_id=telegram_user_id,
                status="active",
            )
            db.add(account)
            await db.flush()

            role = (
                await db.execute(
                    select(Role).where(Role.key == "platform_owner", Role.tenant_id.is_(None))
                )
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    tenant_id=None,
                    key="platform_owner",
                    name="مالک پلتفرم",
                    is_system=True,
                )
                db.add(role)
                await db.flush()

            for permission_key, description in PLATFORM_PERMISSIONS.items():
                permission = (
                    await db.execute(select(Permission).where(Permission.key == permission_key))
                ).scalar_one_or_none()
                if permission is None:
                    permission = Permission(key=permission_key, description=description)
                    db.add(permission)
                    await db.flush()
                assigned = (
                    await db.execute(
                        select(RolePermission.id).where(
                            RolePermission.role_id == role.id,
                            RolePermission.permission_id == permission.id,
                        )
                    )
                ).scalar_one_or_none()
                if assigned is None:
                    db.add(
                        RolePermission(
                            role_id=role.id,
                            permission_id=permission.id,
                            effect="allow",
                        )
                    )

            db.add(
                RoleBinding(
                    user_id=user.id,
                    role_id=role.id,
                    tenant_id=None,
                    scope_type="platform",
                    scope_id=None,
                    created_by_id=user.id,
                )
            )
            db.add(
                AuditEvent(
                    tenant_id=None,
                    actor_id=user.id,
                    event_type="identity.platform_owner.telegram_bootstrap",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    event_data={
                        "provisioning": "interactive_cli",
                        "telegram_account_id": str(account.id),
                    },
                )
            )
            await db.commit()
            print("Platform owner created and linked to the supplied Telegram account.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="classmatework")
    parser.add_argument("command", choices=["create-platform-owner"])
    args = parser.parse_args()

    if args.command == "create-platform-owner":
        raw_telegram_id = input("Owner Telegram numeric ID: ").strip()
        display_name = input("Owner display name: ").strip()
        if not raw_telegram_id.isdigit():
            raise SystemExit("Telegram User ID must contain digits only.")
        telegram_user_id = int(raw_telegram_id)
        if telegram_user_id <= 0 or telegram_user_id > 9_223_372_036_854_775_807:
            raise SystemExit("Telegram User ID is outside the supported positive 64-bit range.")
        if not display_name or len(display_name) > 160:
            raise SystemExit("Display name is required and must be at most 160 characters.")
        asyncio.run(_create_platform_owner(telegram_user_id, display_name))


if __name__ == "__main__":
    main()
