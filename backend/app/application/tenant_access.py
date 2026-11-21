import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    InvalidApplicationInput,
    ResourceConflict,
)
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
from app.modules.audit.service import record_event


@dataclass(frozen=True, slots=True)
class TenantMemberView:
    user_id: UUID
    display_name: str
    telegram_user_id: int | None
    role_keys: tuple[str, ...]
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class RoleDirectoryEntry:
    role_id: UUID
    key: str
    name: str
    tenant_id: UUID | None
    tenant_name: str | None
    permissions: tuple[tuple[str, str], ...]


async def _require_tenant_permission(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    permission_key: str,
) -> None:
    allowed = await has_permission(
        db,
        user_id=actor_id,
        permission_key=permission_key,
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    if not allowed:
        raise AccessDenied


async def _build_role_directory(
    db: AsyncSession,
    *,
    rows: list[tuple[Role, str | None]],
) -> list[RoleDirectoryEntry]:
    role_ids = [role.id for role, _ in rows]
    permission_map: dict[UUID, list[tuple[str, str]]] = {
        role_id: [] for role_id in role_ids
    }
    if role_ids:
        permission_rows = (
            await db.execute(
                select(
                    RolePermission.role_id,
                    Permission.key,
                    RolePermission.effect,
                )
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(RolePermission.role_id.in_(role_ids))
                .order_by(Permission.key)
            )
        ).all()
        for role_id, permission_key, effect in permission_rows:
            permission_map[role_id].append((permission_key, effect))

    return [
        RoleDirectoryEntry(
            role_id=role.id,
            key=role.key,
            name=role.name,
            tenant_id=role.tenant_id,
            tenant_name=tenant_name,
            permissions=tuple(permission_map[role.id]),
        )
        for role, tenant_name in rows
    ]


async def list_tenant_roles(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    limit: int = 100,
) -> list[RoleDirectoryEntry]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Role page size must be between 1 and 100.")
    await _require_tenant_permission(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        permission_key="role:manage",
    )
    rows = (
        await db.execute(
            select(Role, Tenant.name)
            .outerjoin(Tenant, Tenant.id == Role.tenant_id)
            .where(Role.tenant_id == tenant_id)
            .order_by(Role.key)
            .limit(limit)
        )
    ).all()
    entries = await _build_role_directory(db, rows=list(rows))
    await record_event(
        db,
        event_type="role.tenant_directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="role_directory",
        event_data={"returned_count": len(entries), "limit": limit},
    )
    await db.commit()
    return entries


async def list_platform_roles(
    db: AsyncSession,
    *,
    actor_id: UUID,
    limit: int = 100,
) -> list[RoleDirectoryEntry]:
    if not 1 <= limit <= 200:
        raise InvalidApplicationInput("Platform role page size must be between 1 and 200.")
    allowed = await has_permission(
        db,
        user_id=actor_id,
        permission_key="platform:roles:assign",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    if not allowed:
        raise AccessDenied
    rows = (
        await db.execute(
            select(Role, Tenant.name)
            .outerjoin(Tenant, Tenant.id == Role.tenant_id)
            .order_by(Role.tenant_id, Role.key)
            .limit(limit)
        )
    ).all()
    entries = await _build_role_directory(db, rows=list(rows))
    await record_event(
        db,
        event_type="role.platform_directory.read",
        actor_id=actor_id,
        aggregate_type="role_directory",
        event_data={"returned_count": len(entries), "limit": limit},
    )
    await db.commit()
    return entries


async def create_tenant_role(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    key: str,
    name: str,
    permission_keys: tuple[str, ...],
) -> Role:
    await _require_tenant_permission(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        permission_key="role:manage",
    )

    normalized_key = key.strip().lower()
    normalized_name = name.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", normalized_key):
        raise InvalidApplicationInput(
            "Role key must start with a lowercase letter and contain 2-64 letters, digits or underscores."
        )
    if not 2 <= len(normalized_name) <= 120:
        raise InvalidApplicationInput("Role name must contain 2 to 120 characters.")
    normalized_permissions = tuple(dict.fromkeys(item.strip() for item in permission_keys))
    if not normalized_permissions or len(normalized_permissions) > 20:
        raise InvalidApplicationInput("Choose between 1 and 20 permissions.")
    if any(not item or len(item) > 128 for item in normalized_permissions):
        raise InvalidApplicationInput("One or more permission keys are invalid.")

    existing = (
        await db.execute(
            select(Role.id).where(
                Role.tenant_id == tenant_id,
                Role.key == normalized_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ResourceConflict("این شناسه نقش در همین مجموعه قبلاً استفاده شده است.")

    permissions = (
        await db.execute(
            select(Permission).where(Permission.key.in_(normalized_permissions))
        )
    ).scalars().all()
    permission_by_key = {permission.key: permission for permission in permissions}
    missing = sorted(set(normalized_permissions) - set(permission_by_key))
    if missing:
        raise InvalidApplicationInput("Unknown permission keys: " + ", ".join(missing))

    for permission_key in normalized_permissions:
        allowed = await has_permission(
            db,
            user_id=actor_id,
            permission_key=permission_key,
            tenant_id=tenant_id,
            scope_type="tenant",
            scope_id=tenant_id,
        )
        if not allowed:
            raise AccessDenied

    role = Role(
        tenant_id=tenant_id,
        key=normalized_key,
        name=normalized_name,
        is_system=False,
    )
    db.add(role)
    await db.flush()
    db.add_all(
        [
            RolePermission(
                role_id=role.id,
                permission_id=permission_by_key[key].id,
                effect="allow",
            )
            for key in normalized_permissions
        ]
    )
    await db.flush()

    await record_event(
        db,
        event_type="role.tenant_created",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="role",
        aggregate_id=role.id,
        event_data={
            "key": normalized_key,
            "permission_keys": list(normalized_permissions),
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ResourceConflict("Role key already exists for this tenant.") from exc
    await db.refresh(role)
    return role



@dataclass(frozen=True, slots=True)
class PendingInvitationView:
    invitation_id: UUID
    invited_user_id: UUID
    display_name: str
    target_telegram_user_id: int
    role_key: str
    scope_type: str
    scope_id: UUID | None
    created_at: datetime
    expires_at: datetime
    status: str


async def list_admin_invitations(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID | None = None,
    limit: int = 50,
) -> list[PendingInvitationView]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Invitation page size must be between 1 and 100.")

    if tenant_id is None:
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
                raise AccessDenied
    else:
        await _require_tenant_permission(
            db,
            actor_id=actor_id,
            tenant_id=tenant_id,
            permission_key="membership:invite",
        )
        await _require_tenant_permission(
            db,
            actor_id=actor_id,
            tenant_id=tenant_id,
            permission_key="role:manage",
        )

    statement = (
        select(AdminPairingToken, User.display_name, Role.key)
        .join(User, User.id == AdminPairingToken.user_id)
        .join(Role, Role.id == AdminPairingToken.role_id)
        .where(
            AdminPairingToken.consumed_at.is_(None),
            AdminPairingToken.revoked_at.is_(None),
            User.status == "pending",
        )
    )
    if tenant_id is not None:
        statement = statement.where(
            AdminPairingToken.tenant_id == tenant_id,
            AdminPairingToken.scope_type == "tenant",
        )
    rows = (
        await db.execute(
            statement
            .order_by(AdminPairingToken.created_at.desc(), AdminPairingToken.id)
            .limit(limit)
        )
    ).all()

    now = datetime.now(UTC)
    invitations = [
        PendingInvitationView(
            invitation_id=pairing.id,
            invited_user_id=pairing.user_id,
            display_name=display_name,
            target_telegram_user_id=pairing.target_telegram_user_id,
            role_key=role_key,
            scope_type=pairing.scope_type,
            scope_id=pairing.scope_id,
            created_at=pairing.created_at,
            expires_at=pairing.expires_at,
            status=(
                "expired"
                if (
                    pairing.expires_at.replace(tzinfo=UTC)
                    if pairing.expires_at.tzinfo is None
                    else pairing.expires_at.astimezone(UTC)
                )
                <= now
                else "pending"
            ),
        )
        for pairing, display_name, role_key in rows
    ]
    await record_event(
        db,
        event_type=(
            "identity.telegram_pairing.tenant_directory.read"
            if tenant_id is not None
            else "identity.telegram_pairing.platform_directory.read"
        ),
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="pairing_directory",
        event_data={"returned_count": len(invitations), "limit": limit},
    )
    await db.commit()
    return invitations


async def list_tenant_members(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    limit: int = 50,
) -> list[TenantMemberView]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Membership page size must be between 1 and 100.")
    await _require_tenant_permission(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        permission_key="membership:read",
    )

    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(TenantMembership, User, TelegramAccount.telegram_user_id)
            .join(User, User.id == TenantMembership.user_id)
            .outerjoin(
                TelegramAccount,
                and_(
                    TelegramAccount.user_id == User.id,
                    TelegramAccount.status == "active",
                ),
            )
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
                User.status == "active",
                or_(
                    TenantMembership.starts_at.is_(None),
                    TenantMembership.starts_at <= now,
                ),
                or_(
                    TenantMembership.ends_at.is_(None),
                    TenantMembership.ends_at > now,
                ),
            )
            .order_by(User.display_name, User.id)
            .limit(limit)
        )
    ).all()

    user_ids = [user.id for _, user, _ in rows]
    role_map: dict[UUID, list[str]] = {user_id: [] for user_id in user_ids}
    if user_ids:
        binding_rows = (
            await db.execute(
                select(RoleBinding.user_id, Role.key)
                .join(Role, Role.id == RoleBinding.role_id)
                .where(
                    RoleBinding.user_id.in_(user_ids),
                    RoleBinding.tenant_id == tenant_id,
                    RoleBinding.scope_type == "tenant",
                    or_(RoleBinding.starts_at.is_(None), RoleBinding.starts_at <= now),
                    or_(RoleBinding.ends_at.is_(None), RoleBinding.ends_at > now),
                )
                .order_by(Role.key)
            )
        ).all()
        for user_id, role_key in binding_rows:
            role_map[user_id].append(role_key)

    members = [
        TenantMemberView(
            user_id=user.id,
            display_name=user.display_name,
            telegram_user_id=telegram_user_id,
            role_keys=tuple(role_map.get(user.id, [])),
            joined_at=membership.starts_at or membership.created_at,
        )
        for membership, user, telegram_user_id in rows
    ]
    await record_event(
        db,
        event_type="membership.tenant_directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="membership_directory",
        event_data={"returned_count": len(members), "limit": limit},
    )
    await db.commit()
    return members



@dataclass(frozen=True, slots=True)
class TenantMemberSuspensionPreview:
    user_id: UUID
    display_name: str
    is_tenant_admin: bool


@dataclass(frozen=True, slots=True)
class TenantMemberSuspensionResult:
    user_id: UUID
    display_name: str
    tenant_id: UUID
    suspended_at: datetime
    ended_role_bindings: int
    reason: str


async def _active_tenant_admin_ids(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    now: datetime,
) -> set[UUID]:
    candidate_ids = (
        await db.execute(
            select(TenantMembership.user_id)
            .join(User, User.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
                User.status == "active",
                or_(
                    TenantMembership.starts_at.is_(None),
                    TenantMembership.starts_at <= now,
                ),
                or_(
                    TenantMembership.ends_at.is_(None),
                    TenantMembership.ends_at > now,
                ),
            )
        )
    ).scalars().all()

    admins: set[UUID] = set()
    for user_id in set(candidate_ids):
        if await has_permission(
            db,
            user_id=user_id,
            permission_key="role:manage",
            tenant_id=tenant_id,
            scope_type="tenant",
            scope_id=tenant_id,
        ):
            admins.add(user_id)
    return admins


async def _load_suspendable_tenant_member(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    member_user_id: UUID,
    lock: bool,
) -> tuple[TenantMembership, User, bool]:
    tenant_statement = select(Tenant).where(
        Tenant.id == tenant_id,
        Tenant.status == "active",
    )
    if lock:
        tenant_statement = tenant_statement.with_for_update()
    tenant = (await db.execute(tenant_statement)).scalar_one_or_none()
    if tenant is None:
        raise ResourceNotFound("مجموعه فعال پیدا نشد.")

    membership_statement = select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.user_id == member_user_id,
        TenantMembership.status == "active",
    )
    if lock:
        membership_statement = membership_statement.with_for_update()
    membership = (
        await db.execute(membership_statement)
    ).scalar_one_or_none()
    if membership is None:
        raise ResourceNotFound("عضویت فعال این کاربر در مجموعه پیدا نشد.")

    user = await db.get(User, member_user_id)
    if user is None or user.status != "active":
        raise ResourceNotFound("حساب کاربر فعال نیست.")

    now = datetime.now(UTC)
    starts_at = membership.starts_at
    ends_at = membership.ends_at
    if (
        starts_at is not None
        and (
            starts_at.replace(tzinfo=UTC)
            if starts_at.tzinfo is None
            else starts_at.astimezone(UTC)
        ) > now
    ):
        raise ResourceConflict("عضویت هنوز شروع نشده است.")
    if (
        ends_at is not None
        and (
            ends_at.replace(tzinfo=UTC)
            if ends_at.tzinfo is None
            else ends_at.astimezone(UTC)
        ) <= now
    ):
        raise ResourceConflict("عضویت در حال حاضر فعال نیست.")

    if actor_id == member_user_id:
        raise InvalidApplicationInput("مدیر نمی‌تواند عضویت خودش را تعلیق کند.")

    is_tenant_admin = await has_permission(
        db,
        user_id=member_user_id,
        permission_key="role:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    if is_tenant_admin:
        admins = await _active_tenant_admin_ids(
            db,
            tenant_id=tenant_id,
            now=now,
        )
        if member_user_id in admins and len(admins) <= 1:
            raise InvalidApplicationInput(
                "آخرین مدیر فعال مجموعه قابل تعلیق نیست؛ ابتدا مدیر جایگزین اضافه کنید."
            )
    return membership, user, is_tenant_admin


async def preview_tenant_member_suspension(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    member_user_id: UUID,
) -> TenantMemberSuspensionPreview:
    await _require_tenant_permission(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        permission_key="membership:manage",
    )
    _, user, is_admin = await _load_suspendable_tenant_member(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        member_user_id=member_user_id,
        lock=False,
    )
    return TenantMemberSuspensionPreview(
        user_id=user.id,
        display_name=user.display_name,
        is_tenant_admin=is_admin,
    )


async def suspend_tenant_member(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    member_user_id: UUID,
    reason: str,
) -> TenantMemberSuspensionResult:
    normalized_reason = reason.strip()
    if not 10 <= len(normalized_reason) <= 500:
        raise InvalidApplicationInput("دلیل تعلیق باید بین ۱۰ تا ۵۰۰ نویسه باشد.")

    # Lock the tenant row to serialize concurrent last-admin suspension attempts.
    await db.execute(
        select(Tenant.id)
        .where(Tenant.id == tenant_id)
        .with_for_update()
    )
    await _require_tenant_permission(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        permission_key="membership:manage",
    )
    membership, user, is_admin = await _load_suspendable_tenant_member(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        member_user_id=member_user_id,
        lock=True,
    )

    now = datetime.now(UTC)
    bindings = (
        await db.execute(
            select(RoleBinding)
            .where(
                RoleBinding.user_id == member_user_id,
                RoleBinding.tenant_id == tenant_id,
                or_(RoleBinding.starts_at.is_(None), RoleBinding.starts_at <= now),
                or_(RoleBinding.ends_at.is_(None), RoleBinding.ends_at > now),
            )
            .with_for_update()
        )
    ).scalars().all()

    membership.status = "suspended"
    membership.ends_at = now
    for binding in bindings:
        binding.ends_at = now

    await record_event(
        db,
        event_type="membership.tenant_member_suspended",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="membership",
        aggregate_id=membership.id,
        event_data={
            "member_user_id": str(member_user_id),
            "member_was_tenant_admin": is_admin,
            "ended_role_bindings": len(bindings),
            "reason": normalized_reason,
        },
    )
    await db.commit()
    return TenantMemberSuspensionResult(
        user_id=user.id,
        display_name=user.display_name,
        tenant_id=tenant_id,
        suspended_at=now,
        ended_role_bindings=len(bindings),
        reason=normalized_reason,
    )
