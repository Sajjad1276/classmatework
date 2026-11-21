from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdminPairingToken,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    TelegramAccount,
    TenantMembership,
    User,
)
from app.modules.access.policy import has_permission, resolve_scope_ancestors
from app.modules.audit.service import record_event
from app.modules.identity.tokens import digest_secret, generate_secret


class PairingRejected(Exception):
    """Raised when a Telegram pairing token cannot be consumed safely."""


class PairingNotAllowed(Exception):
    """Raised when the acting administrator lacks the pairing/assignment authority."""


@dataclass(frozen=True, slots=True)
class PairingInvitation:
    user_id: UUID
    telegram_user_id: int
    role_key: str
    scope_type: str
    scope_id: UUID | None
    tenant_id: UUID | None
    raw_token: str
    expires_at: datetime


async def _require_assignment_authority(
    db: AsyncSession,
    *,
    actor_id: UUID,
) -> None:
    for permission_key in ("platform:users:manage", "platform:roles:assign"):
        allowed = await has_permission(
            db,
            user_id=actor_id,
            permission_key=permission_key,
            tenant_id=None,
            scope_type="platform",
            scope_id=None,
        )
        if not allowed:
            raise PairingNotAllowed


async def _resolve_invitation_scope(
    db: AsyncSession,
    *,
    scope_type: str,
    scope_id: UUID | None,
) -> UUID | None:
    """Resolve only the assignment scopes exposed by the phase-one bot UI."""
    if scope_type == "platform":
        if scope_id is not None:
            raise ValueError("Platform scope must not have a scope ID.")
        return None
    if scope_type != "tenant" or scope_id is None:
        raise ValueError("Phase one invitations support only platform or tenant scope.")

    ancestors = await resolve_scope_ancestors(
        db,
        tenant_id=scope_id,
        scope_type="tenant",
        scope_id=scope_id,
    )
    if ancestors is None:
        raise ValueError("The requested tenant is not active.")
    return scope_id

async def _resolve_assignable_role(
    db: AsyncSession,
    *,
    actor_id: UUID,
    role_key: str,
    tenant_id: UUID | None,
    scope_type: str,
    scope_id: UUID | None,
) -> Role:
    normalized_key = role_key.strip().lower()
    if not normalized_key or len(normalized_key) > 80:
        raise ValueError("Role key must contain 1 to 80 characters.")

    role_statement = select(Role).where(Role.key == normalized_key)
    if tenant_id is None:
        role_statement = role_statement.where(Role.tenant_id.is_(None))
    else:
        role_statement = role_statement.where(Role.tenant_id == tenant_id)
    role = (await db.execute(role_statement)).scalar_one_or_none()
    if role is None:
        raise ValueError("Role does not exist in the requested scope.")

    role_permissions = (
        await db.execute(
            select(Permission.key, RolePermission.effect)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
        )
    ).all()

    # The inviter may only grant permissions they currently possess at the exact
    # destination scope. Deny entries are safe to delegate and do not add authority.
    for permission_key, effect in role_permissions:
        if effect != "allow":
            continue
        actor_can_grant = await has_permission(
            db,
            user_id=actor_id,
            permission_key=permission_key,
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        if not actor_can_grant:
            raise PairingNotAllowed

    return role


async def _validate_pairing_grant(
    db: AsyncSession,
    *,
    pairing: AdminPairingToken,
    actor_id: UUID,
) -> Role:
    await _require_assignment_authority(db, actor_id=actor_id)
    tenant_id = await _resolve_invitation_scope(
        db,
        scope_type=pairing.scope_type,
        scope_id=pairing.scope_id,
    )
    if tenant_id != pairing.tenant_id:
        raise ValueError("The invitation scope no longer matches its tenant.")
    return await _resolve_assignable_role(
        db,
        actor_id=actor_id,
        role_key=(
            await db.execute(select(Role.key).where(Role.id == pairing.role_id))
        ).scalar_one_or_none()
        or "",
        tenant_id=tenant_id,
        scope_type=pairing.scope_type,
        scope_id=pairing.scope_id,
    )


async def create_admin_pairing(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_telegram_user_id: int,
    display_name: str,
    role_key: str,
    scope_type: str,
    scope_id: UUID | None,
    ttl: timedelta = timedelta(minutes=15),
) -> PairingInvitation:
    await _require_assignment_authority(db, actor_id=actor_id)

    if target_telegram_user_id <= 0:
        raise ValueError("Telegram User ID must be positive.")
    normalized_name = display_name.strip()
    if not normalized_name or len(normalized_name) > 160:
        raise ValueError("Display name must contain 1 to 160 characters.")
    if ttl <= timedelta(0) or ttl > timedelta(hours=1):
        raise ValueError("Pairing token TTL must be between 1 second and 1 hour.")

    now = datetime.now(UTC)
    existing_account = (
        await db.execute(
            select(TelegramAccount.id).where(
                TelegramAccount.telegram_user_id == target_telegram_user_id
            )
        )
    ).scalar_one_or_none()
    if existing_account is not None:
        raise ValueError("This Telegram account is already linked.")

    tenant_id = await _resolve_invitation_scope(
        db,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    role = await _resolve_assignable_role(
        db,
        actor_id=actor_id,
        role_key=role_key,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )

    existing_invitation = (
        await db.execute(
            select(AdminPairingToken)
            .where(
                AdminPairingToken.target_telegram_user_id == target_telegram_user_id,
                AdminPairingToken.consumed_at.is_(None),
                AdminPairingToken.revoked_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing_invitation is not None:
        if existing_invitation.expires_at > now:
            raise ValueError("An active invitation already exists for this Telegram account.")
        existing_invitation.revoked_at = now

    user = User(
        email=None,
        display_name=normalized_name,
        password_hash=None,
        status="pending",
    )
    db.add(user)
    await db.flush()

    raw_token = generate_secret()
    expires_at = now + ttl
    pairing = AdminPairingToken(
        user_id=user.id,
        target_telegram_user_id=target_telegram_user_id,
        token_hash=digest_secret(raw_token),
        role_id=role.id,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        created_by_id=actor_id,
        expires_at=expires_at,
        created_at=now,
    )
    db.add(pairing)
    await db.flush()

    await record_event(
        db,
        event_type="identity.telegram_pairing.created",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="user",
        aggregate_id=user.id,
        event_data={
            "target_telegram_user_id": target_telegram_user_id,
            "expires_at": expires_at.isoformat(),
            "pairing_token_id": str(pairing.id),
            "role_id": str(role.id),
            "role_key": role.key,
            "scope_type": scope_type,
            "scope_id": str(scope_id) if scope_id else None,
        },
    )
    await db.commit()
    return PairingInvitation(
        user_id=user.id,
        telegram_user_id=target_telegram_user_id,
        role_key=role.key,
        scope_type=scope_type,
        scope_id=scope_id,
        tenant_id=tenant_id,
        raw_token=raw_token,
        expires_at=expires_at,
    )


async def consume_admin_pairing(
    db: AsyncSession,
    *,
    raw_token: str,
    telegram_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> TelegramAccount:
    if not raw_token or len(raw_token) > 128 or telegram_user_id <= 0:
        raise PairingRejected

    now = datetime.now(UTC)
    pairing = (
        await db.execute(
            select(AdminPairingToken)
            .where(
                AdminPairingToken.token_hash == digest_secret(raw_token),
                AdminPairingToken.target_telegram_user_id == telegram_user_id,
                AdminPairingToken.consumed_at.is_(None),
                AdminPairingToken.revoked_at.is_(None),
                AdminPairingToken.expires_at > now,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pairing is None:
        raise PairingRejected

    existing_account = (
        await db.execute(
            select(TelegramAccount.id).where(
                TelegramAccount.telegram_user_id == telegram_user_id
            )
        )
    ).scalar_one_or_none()
    if existing_account is not None:
        raise PairingRejected

    user = (
        await db.execute(
            select(User).where(User.id == pairing.user_id, User.status == "pending")
        )
    ).scalar_one_or_none()
    if user is None:
        raise PairingRejected

    actor = await db.get(User, pairing.created_by_id)
    if actor is None or actor.status != "active":
        raise PairingRejected
    try:
        role = await _validate_pairing_grant(
            db,
            pairing=pairing,
            actor_id=actor.id,
        )
    except (ValueError, PairingNotAllowed):
        raise PairingRejected from None

    account = TelegramAccount(
        user_id=user.id,
        telegram_user_id=telegram_user_id,
        username=username[:64] if username else None,
        first_name=first_name[:128] if first_name else None,
        last_name=last_name[:128] if last_name else None,
        status="active",
        linked_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(account)

    if pairing.tenant_id is not None:
        db.add(
            TenantMembership(
                user_id=user.id,
                tenant_id=pairing.tenant_id,
                status="active",
                starts_at=now,
            )
        )

    db.add(
        RoleBinding(
            user_id=user.id,
            role_id=role.id,
            tenant_id=pairing.tenant_id,
            scope_type=pairing.scope_type,
            scope_id=pairing.scope_id,
            starts_at=now,
            created_by_id=pairing.created_by_id,
        )
    )
    user.status = "active"
    pairing.consumed_at = now
    await db.flush()

    await record_event(
        db,
        event_type="identity.telegram_pairing.consumed",
        actor_id=user.id,
        tenant_id=pairing.tenant_id,
        aggregate_type="user",
        aggregate_id=user.id,
        event_data={
            "telegram_account_id": str(account.id),
            "pairing_token_id": str(pairing.id),
            "role_id": str(role.id),
            "role_key": role.key,
            "scope_type": pairing.scope_type,
            "scope_id": str(pairing.scope_id) if pairing.scope_id else None,
            "assigned_by_id": str(pairing.created_by_id),
        },
    )
    await db.commit()
    return account
