from __future__ import annotations

import argparse
import asyncio
from getpass import getpass

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AuditEvent, Permission, Role, RoleBinding, RolePermission, User
from app.db.runtime import create_database_engine, create_session_factory
from app.modules.identity.passwords import hash_password

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


async def _create_platform_owner(email: str, display_name: str, password: str) -> None:
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
                raise RuntimeError("A platform owner is already configured; refusing to create another.")

            existing_user = (
                await db.execute(select(User.id).where(User.email == email))
            ).scalar_one_or_none()
            if existing_user is not None:
                raise RuntimeError("This email already belongs to a user.")

            user = User(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                status="active",
            )
            db.add(user)
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
                    db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"))

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
                    event_type="identity.platform_owner.bootstrap_created",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    event_data={"provisioning": "interactive_cli"},
                )
            )
            await db.commit()
            print(f"Platform owner created: {email}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="classmatework")
    parser.add_argument("command", choices=["create-platform-owner"])
    args = parser.parse_args()

    if args.command == "create-platform-owner":
        email = input("Owner email: ").strip().lower()
        display_name = input("Owner display name: ").strip()
        password = getpass("Password (minimum 12 characters): ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
        if len(password) < 12:
            raise SystemExit("Password must contain at least 12 characters.")
        if not email or not display_name:
            raise SystemExit("Email and display name are required.")
        asyncio.run(_create_platform_owner(email, display_name, password))


if __name__ == "__main__":
    main()
