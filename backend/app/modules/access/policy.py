from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ClassGroup,
    Organization,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    School,
    Tenant,
    TenantMembership,
)

_SCOPE_TYPES = {"tenant", "organization", "school", "class"}


async def _resolve_scope_ancestors(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    scope_type: str,
    scope_id: UUID | None,
) -> list[tuple[str, UUID]] | None:
    """Return the target scope and its valid ancestors, or None for an invalid target."""
    if scope_id is None or scope_type not in _SCOPE_TYPES:
        return None

    tenant_exists = (
        await db.execute(
            select(Tenant.id).where(Tenant.id == tenant_id, Tenant.status == "active")
        )
    ).scalar_one_or_none()
    if tenant_exists is None:
        return None

    if scope_type == "tenant":
        return [("tenant", tenant_id)] if scope_id == tenant_id else None

    if scope_type == "organization":
        organization = (
            await db.execute(
                select(Organization).where(
                    Organization.id == scope_id,
                    Organization.tenant_id == tenant_id,
                    Organization.status == "active",
                )
            )
        ).scalar_one_or_none()
        if organization is None:
            return None
        return [("organization", organization.id), ("tenant", tenant_id)]

    if scope_type == "school":
        school = (
            await db.execute(
                select(School).where(
                    School.id == scope_id,
                    School.tenant_id == tenant_id,
                    School.status != "archived",
                )
            )
        ).scalar_one_or_none()
        if school is None:
            return None
        ancestors = [("school", school.id)]
        if school.organization_id is not None:
            organization_exists = (
                await db.execute(
                    select(Organization.id).where(
                        Organization.id == school.organization_id,
                        Organization.tenant_id == tenant_id,
                        Organization.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if organization_exists is None:
                return None
            ancestors.append(("organization", school.organization_id))
        ancestors.append(("tenant", tenant_id))
        return ancestors

    class_group = (
        await db.execute(
            select(ClassGroup).where(
                ClassGroup.id == scope_id,
                ClassGroup.tenant_id == tenant_id,
                ClassGroup.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if class_group is None:
        return None

    school = (
        await db.execute(
            select(School).where(
                School.id == class_group.school_id,
                School.tenant_id == tenant_id,
                School.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if school is None:
        return None

    ancestors = [("class", class_group.id), ("school", school.id)]
    if school.organization_id is not None:
        organization_exists = (
            await db.execute(
                select(Organization.id).where(
                    Organization.id == school.organization_id,
                    Organization.tenant_id == tenant_id,
                    Organization.status == "active",
                )
            )
        ).scalar_one_or_none()
        if organization_exists is None:
            return None
        ancestors.append(("organization", school.organization_id))
    ancestors.append(("tenant", tenant_id))
    return ancestors


async def has_permission(
    db: AsyncSession,
    *,
    user_id: UUID,
    permission_key: str,
    tenant_id: UUID | None,
    scope_type: str = "tenant",
    scope_id: UUID | None = None,
) -> bool:
    """Evaluate RBAC grants against a validated scope ancestry.

    Platform grants are global. Tenant/organization/school grants flow down to
    descendants only; they never flow to parents, sibling scopes, or other tenants.
    An explicit deny on any matching ancestor overrides every matching allow.
    """
    now = datetime.now(UTC)
    time_valid = and_(
        or_(RoleBinding.starts_at.is_(None), RoleBinding.starts_at <= now),
        or_(RoleBinding.ends_at.is_(None), RoleBinding.ends_at > now),
    )

    if tenant_id is None:
        if scope_type != "platform" or scope_id is not None:
            return False
        scope_predicate = RoleBinding.scope_type == "platform"
        role_tenant_predicate = Role.tenant_id.is_(None)
        ancestors: list[tuple[str, UUID]] = []
    else:
        ancestors = await _resolve_scope_ancestors(
            db,
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        if ancestors is None:
            return False
        ancestor_predicates = [
            and_(
                RoleBinding.scope_type == ancestor_type,
                RoleBinding.scope_id == ancestor_id,
                RoleBinding.tenant_id == tenant_id,
            )
            for ancestor_type, ancestor_id in ancestors
        ]
        scope_predicate = or_(
            RoleBinding.scope_type == "platform",
            *ancestor_predicates,
        )
        role_tenant_predicate = or_(
            Role.tenant_id.is_(None),
            Role.tenant_id == tenant_id,
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
            scope_predicate,
            role_tenant_predicate,
        )
    )
    grants = (await db.execute(query)).all()
    if not grants:
        return False
    if any(effect == "deny" for effect, _ in grants):
        return False

    has_platform_allow = any(
        effect == "allow" and binding_scope == "platform"
        for effect, binding_scope in grants
    )
    if tenant_id is None or has_platform_allow:
        return any(effect == "allow" for effect, _ in grants)

    membership_query = select(TenantMembership.id).where(
        TenantMembership.user_id == user_id,
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.status == "active",
        or_(TenantMembership.starts_at.is_(None), TenantMembership.starts_at <= now),
        or_(TenantMembership.ends_at.is_(None), TenantMembership.ends_at > now),
    )
    has_membership = (
        await db.execute(membership_query)
    ).scalar_one_or_none() is not None
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
