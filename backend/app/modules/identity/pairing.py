from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminPairingToken, TelegramAccount, User
from app.modules.access.policy import has_permission
from app.modules.audit.service import record_event
from app.modules.identity.tokens import digest_secret, generate_secret


class PairingRejected(Exception):
    """Raised when a Telegram pairing token cannot be consumed safely."""


class PairingNotAllowed(Exception):
    """Raised when the acting administrator lacks the pairing permission."""


@dataclass(frozen=True, slots=True)
class PairingInvitation:
    user_id: UUID
    telegram_user_id: int
    raw_token: str
    expires_at: datetime


async def create_admin_pairing(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_telegram_user_id: int,
    display_name: str,
    ttl: timedelta = timedelta(minutes=15),
) -> PairingInvitation:
    allowed = await has_permission(
        db,
        user_id=actor_id,
        permission_key="platform:users:manage",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    if not allowed:
        raise PairingNotAllowed

    if target_telegram_user_id <= 0:
        raise ValueError("Telegram User ID must be positive.")
    normalized_name = display_name.strip()
    if not normalized_name or len(normalized_name) > 160:
        raise ValueError("Display name must contain 1 to 160 characters.")
    if ttl <= timedelta(0) or ttl > timedelta(hours=1):
        raise ValueError("Pairing token TTL must be between 1 second and 1 hour.")

    existing_account = (
        await db.execute(
            select(TelegramAccount.id).where(
                TelegramAccount.telegram_user_id == target_telegram_user_id
            )
        )
    ).scalar_one_or_none()
    if existing_account is not None:
        raise ValueError("This Telegram account is already linked.")

    now = datetime.now(UTC)
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
        aggregate_type="user",
        aggregate_id=user.id,
        event_data={
            "target_telegram_user_id": target_telegram_user_id,
            "expires_at": expires_at.isoformat(),
            "pairing_token_id": str(pairing.id),
        },
    )
    await db.commit()
    return PairingInvitation(
        user_id=user.id,
        telegram_user_id=target_telegram_user_id,
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
    user.status = "active"
    pairing.consumed_at = now
    await db.flush()

    await record_event(
        db,
        event_type="identity.telegram_pairing.consumed",
        actor_id=user.id,
        aggregate_type="user",
        aggregate_id=user.id,
        event_data={
            "telegram_account_id": str(account.id),
            "pairing_token_id": str(pairing.id),
        },
    )
    await db.commit()
    return account
