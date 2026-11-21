from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Permission, Role, RoleBinding, RolePermission, TenantMembership


async def has_permission(
    db: AsyncSession,
    *,
    user_id: UUID,
    permission_key: str,
    tenant_id: UUID | None,
    scope_type: str = "tenant",
    scope_id: UUID | None = None,
) -> bool:
    now = datetime.now(UTC)
    time_valid = (
        (RoleBinding.starts_at.is_(None) | (RoleBinding.starts_at <= now))
        & (RoleBinding.ends_at.is_(None) | (RoleBinding.ends_at > now))
    )
    if tenant_id is None:
        scope_valid = RoleBinding.scope_type == "platform"
    else:
        scope_valid = or_(
            RoleBinding.scope_type == "platform",
            (
                (RoleBinding.tenant_id == tenant_id)
                & or_(
                    (
                        (RoleBinding.scope_type == "tenant")
                        & (RoleBinding.scope_id == tenant_id)
                    ),
                    (
                        (RoleBinding.scope_type == scope_type)
                        & (RoleBinding.scope_id == scope_id)
                    ),
                )
            ),
        )

    query = (
        select(RolePermission.effect, RoleBinding.scope_type)
        .join(RoleBinding, RoleBinding.role_id == RolePermission.role_id)
        .join(Role, Role.id == RoleBinding.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            RoleBinding.user_id == user_id,
            Permission.key == permission_key,
            time_valid,
            scope_valid,
            or_(Role.tenant_id.is_(None), Role.tenant_id == tenant_id),
        )
    )
    grants = (await db.execute(query)).all()
    if not grants:
        return False
    if any(effect == "deny" for effect, _ in grants):
        return False

    has_platform_grant = any(scope == "platform" for _, scope in grants)
    if has_platform_grant or tenant_id is None:
        return any(effect == "allow" for effect, _ in grants)

    membership_query = select(TenantMembership.id).where(
        TenantMembership.user_id == user_id,
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.status == "active",
        or_(TenantMembership.starts_at.is_(None), TenantMembership.starts_at <= now),
        or_(TenantMembership.ends_at.is_(None), TenantMembership.ends_at > now),
    )
    has_membership = (await db.execute(membership_query)).scalar_one_or_none() is not None
    return has_membership and any(effect == "allow" for effect, _ in grants)


async def require_permission(
    db: AsyncSession,
    *,
    user_id: UUID,
    permission_key: str,
    tenant_id: UUID | None,
    scope_type: str = "tenant",
    scope_id: UUID | None = None,
) -> None:
    from fastapi import HTTPException, status

    allowed = await has_permission(
        db,
        user_id=user_id,
        permission_key=permission_key,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
