from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    InvalidApplicationInput,
    ResourceConflict,
    ResourceNotFound,
)
from app.db.models import (
    AcademicYear,
    ClassGroup,
    Organization,
    Permission,
    Role,
    RolePermission,
    School,
    Subject,
    Tenant,
    TenantMembership,
)
from app.modules.access.policy import has_permission
from app.modules.audit.service import record_event


async def _require(
    db: AsyncSession,
    *,
    actor_id: UUID,
    permission_key: str,
    tenant_id: UUID | None,
    scope_type: str = "platform",
    scope_id: UUID | None = None,
) -> None:
    allowed = await has_permission(
        db,
        user_id=actor_id,
        permission_key=permission_key,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    if not allowed:
        raise AccessDenied


async def _flush_or_conflict(db: AsyncSession, message: str) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ResourceConflict(message) from exc


async def _commit_or_conflict(db: AsyncSession, message: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ResourceConflict(message) from exc


async def list_tenants(
    db: AsyncSession,
    *,
    actor_id: UUID,
    limit: int = 50,
) -> list[Tenant]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Tenant page size must be between 1 and 100.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="platform:tenants:read",
        tenant_id=None,
    )
    result = await db.execute(
        select(Tenant).order_by(Tenant.created_at.desc(), Tenant.id).limit(limit)
    )
    tenants = list(result.scalars().all())
    await record_event(
        db,
        event_type="tenant.directory.read",
        actor_id=actor_id,
        aggregate_type="tenant_directory",
        event_data={"returned_count": len(tenants), "limit": limit},
    )
    await db.commit()
    return tenants


_TENANT_ROLE_TEMPLATES = {
    "tenant_admin": (
        "مدیر مجموعه",
        (
            "school:read",
            "school:create",
            "academic_year:create",
            "class:create",
            "subject:create",
            "membership:read",
            "membership:invite",
            "role:manage",
        ),
    ),
    "tenant_viewer": (
        "ناظر مجموعه",
        ("school:read",),
    ),
}


async def _seed_tenant_roles(
    db: AsyncSession,
    *,
    tenant_id: UUID,
) -> None:
    """Create safe, tenant-local baseline roles for the first admin invitations."""
    for role_key, (role_name, permission_keys) in _TENANT_ROLE_TEMPLATES.items():
        role = (
            await db.execute(
                select(Role).where(
                    Role.tenant_id == tenant_id,
                    Role.key == role_key,
                )
            )
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                tenant_id=tenant_id,
                key=role_key,
                name=role_name,
                is_system=True,
            )
            db.add(role)
            await db.flush()

        for permission_key in permission_keys:
            permission = (
                await db.execute(
                    select(Permission).where(Permission.key == permission_key)
                )
            ).scalar_one_or_none()
            if permission is None:
                permission = Permission(
                    key=permission_key,
                    description=f"Baseline permission: {permission_key}",
                )
                db.add(permission)
                await db.flush()

            existing = (
                await db.execute(
                    select(RolePermission.id).where(
                        RolePermission.role_id == role.id,
                        RolePermission.permission_id == permission.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    RolePermission(
                        role_id=role.id,
                        permission_id=permission.id,
                        effect="allow",
                    )
                )
    await db.flush()


async def create_tenant(
    db: AsyncSession,
    *,
    actor_id: UUID,
    name: str,
    slug: str,
    timezone: str = "Asia/Tehran",
    locale: str = "fa-IR",
) -> Tenant:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="platform:tenants:create",
        tenant_id=None,
    )
    normalized_name = name.strip()
    normalized_slug = slug.strip().lower()
    normalized_timezone = timezone.strip()
    normalized_locale = locale.strip()

    if not 2 <= len(normalized_name) <= 180:
        raise InvalidApplicationInput("Tenant name must contain 2 to 180 characters.")
    if not 2 <= len(normalized_slug) <= 80:
        raise InvalidApplicationInput("Tenant slug must contain 2 to 80 characters.")
    if not normalized_slug[0].isalnum() or not normalized_slug[-1].isalnum():
        raise InvalidApplicationInput("Tenant slug must start and end with a letter or digit.")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in normalized_slug):
        raise InvalidApplicationInput("Tenant slug may contain lowercase letters, digits and hyphens only.")
    if not normalized_timezone or len(normalized_timezone) > 64:
        raise InvalidApplicationInput("Timezone is invalid.")
    if not 2 <= len(normalized_locale) <= 12:
        raise InvalidApplicationInput("Locale must contain 2 to 12 characters.")

    existing = (
        await db.execute(select(Tenant.id).where(Tenant.slug == normalized_slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise ResourceConflict("این شناسه قبلاً برای یک مجموعه ثبت شده است.")

    tenant = Tenant(
        name=normalized_name,
        slug=normalized_slug,
        timezone=normalized_timezone,
        locale=normalized_locale,
        status="active",
    )
    db.add(tenant)
    await _flush_or_conflict(db, "Tenant slug already exists.")
    await _seed_tenant_roles(db, tenant_id=tenant.id)
    await record_event(
        db,
        event_type="tenant.created",
        actor_id=actor_id,
        tenant_id=tenant.id,
        aggregate_type="tenant",
        aggregate_id=tenant.id,
        event_data={"slug": tenant.slug, "name": tenant.name},
    )
    await _commit_or_conflict(db, "Tenant slug already exists.")
    await db.refresh(tenant)
    return tenant


async def list_schools(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    limit: int = 50,
) -> list[School]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("School page size must be between 1 and 100.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    result = await db.execute(
        select(School)
        .where(School.tenant_id == tenant_id)
        .order_by(School.created_at.desc(), School.id)
        .limit(limit)
    )
    schools = list(result.scalars().all())
    await record_event(
        db,
        event_type="school.directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school_directory",
        event_data={"returned_count": len(schools), "limit": limit},
    )
    await db.commit()
    return schools


async def create_school(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    name: str,
    code: str,
    organization_id: UUID | None = None,
) -> School:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:create",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    normalized_name = name.strip()
    normalized_code = code.strip().upper()
    if not 2 <= len(normalized_name) <= 180:
        raise InvalidApplicationInput("School name must contain 2 to 180 characters.")
    if not 1 <= len(normalized_code) <= 48:
        raise InvalidApplicationInput("School code must contain 1 to 48 characters.")

    if organization_id is not None:
        organization = (
            await db.execute(
                select(Organization).where(
                    Organization.id == organization_id,
                    Organization.tenant_id == tenant_id,
                    Organization.status == "active",
                )
            )
        ).scalar_one_or_none()
        if organization is None:
            raise ResourceNotFound("سازمان انتخاب‌شده در این مجموعه پیدا نشد.")

    existing = (
        await db.execute(
            select(School.id).where(
                School.tenant_id == tenant_id,
                School.code == normalized_code,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ResourceConflict("کد مدرسه در این مجموعه قبلاً استفاده شده است.")

    school = School(
        tenant_id=tenant_id,
        organization_id=organization_id,
        name=normalized_name,
        code=normalized_code,
        status="setup",
    )
    db.add(school)
    await _flush_or_conflict(db, "School code already exists in this tenant.")
    await record_event(
        db,
        event_type="school.created",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school",
        aggregate_id=school.id,
        event_data={"code": school.code, "name": school.name},
    )
    await _commit_or_conflict(db, "School code already exists in this tenant.")
    await db.refresh(school)
    return school


async def create_academic_year(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    name: str,
    starts_on: date,
    ends_on: date,
) -> AcademicYear:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="academic_year:create",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    if ends_on <= starts_on:
        raise InvalidApplicationInput("تاریخ پایان باید بعد از تاریخ شروع باشد.")
    school = (
        await db.execute(
            select(School).where(
                School.id == school_id,
                School.tenant_id == tenant_id,
                School.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if school is None:
        raise ResourceNotFound("مدرسه در محدوده انتخاب‌شده پیدا نشد.")

    academic_year = AcademicYear(
        tenant_id=tenant_id,
        school_id=school_id,
        name=name.strip(),
        starts_on=starts_on,
        ends_on=ends_on,
        status="planned",
    )
    if not academic_year.name or len(academic_year.name) > 64:
        raise InvalidApplicationInput("نام سال تحصیلی نامعتبر است.")
    db.add(academic_year)
    await _flush_or_conflict(db, "Academic year already exists for this school.")
    await record_event(
        db,
        event_type="academic_year.created",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="academic_year",
        aggregate_id=academic_year.id,
        event_data={"school_id": str(school_id), "name": academic_year.name},
    )
    await _commit_or_conflict(db, "Academic year already exists for this school.")
    await db.refresh(academic_year)
    return academic_year


async def create_class(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    academic_year_id: UUID,
    name: str,
    code: str,
    grade: str,
) -> ClassGroup:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:create",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    academic_year = (
        await db.execute(
            select(AcademicYear).where(
                AcademicYear.id == academic_year_id,
                AcademicYear.school_id == school_id,
                AcademicYear.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if academic_year is None:
        raise ResourceNotFound("سال تحصیلی در مدرسه انتخاب‌شده پیدا نشد.")

    normalized_name = name.strip()
    normalized_code = code.strip().upper()
    normalized_grade = grade.strip()
    if not normalized_name or len(normalized_name) > 120:
        raise InvalidApplicationInput("Class name is invalid.")
    if not normalized_code or len(normalized_code) > 48:
        raise InvalidApplicationInput("Class code is invalid.")
    if not normalized_grade or len(normalized_grade) > 48:
        raise InvalidApplicationInput("Grade is invalid.")

    class_group = ClassGroup(
        tenant_id=tenant_id,
        school_id=school_id,
        academic_year_id=academic_year_id,
        name=normalized_name,
        code=normalized_code,
        grade=normalized_grade,
        status="active",
    )
    db.add(class_group)
    await _flush_or_conflict(db, "Class code already exists for this academic year.")
    await record_event(
        db,
        event_type="class.created",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={
            "school_id": str(school_id),
            "academic_year_id": str(academic_year_id),
            "code": normalized_code,
        },
    )
    await _commit_or_conflict(db, "Class code already exists for this academic year.")
    await db.refresh(class_group)
    return class_group


async def create_subject(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    name: str,
    code: str,
) -> Subject:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="subject:create",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    school = (
        await db.execute(
            select(School).where(
                School.id == school_id,
                School.tenant_id == tenant_id,
                School.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if school is None:
        raise ResourceNotFound("مدرسه در محدوده انتخاب‌شده پیدا نشد.")

    normalized_name = name.strip()
    normalized_code = code.strip().upper()
    if not normalized_name or len(normalized_name) > 120:
        raise InvalidApplicationInput("Subject name is invalid.")
    if not normalized_code or len(normalized_code) > 48:
        raise InvalidApplicationInput("Subject code is invalid.")

    subject = Subject(
        tenant_id=tenant_id,
        school_id=school_id,
        name=normalized_name,
        code=normalized_code,
        status="active",
    )
    db.add(subject)
    await _flush_or_conflict(db, "Subject code already exists for this school.")
    await record_event(
        db,
        event_type="subject.created",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="subject",
        aggregate_id=subject.id,
        event_data={"school_id": str(school_id), "code": normalized_code},
    )
    await _commit_or_conflict(db, "Subject code already exists for this school.")
    await db.refresh(subject)
    return subject



async def list_academic_years(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    limit: int = 50,
) -> list[AcademicYear]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Academic year page size must be between 1 and 100.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    result = await db.execute(
        select(AcademicYear)
        .where(
            AcademicYear.tenant_id == tenant_id,
            AcademicYear.school_id == school_id,
        )
        .order_by(AcademicYear.starts_on.desc(), AcademicYear.name)
        .limit(limit)
    )
    years = list(result.scalars().all())
    await record_event(
        db,
        event_type="academic_year.directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="academic_year_directory",
        event_data={
            "school_id": str(school_id),
            "returned_count": len(years),
            "limit": limit,
        },
    )
    await db.commit()
    return years


async def list_classes(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    academic_year_id: UUID | None = None,
    limit: int = 50,
) -> list[ClassGroup]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Class page size must be between 1 and 100.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    statement = select(ClassGroup).where(
        ClassGroup.tenant_id == tenant_id,
        ClassGroup.school_id == school_id,
    )
    if academic_year_id is not None:
        statement = statement.where(ClassGroup.academic_year_id == academic_year_id)
    result = await db.execute(
        statement.order_by(ClassGroup.name, ClassGroup.code).limit(limit)
    )
    classes = list(result.scalars().all())
    await record_event(
        db,
        event_type="class.directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="class_directory",
        event_data={
            "school_id": str(school_id),
            "academic_year_id": str(academic_year_id) if academic_year_id else None,
            "returned_count": len(classes),
            "limit": limit,
        },
    )
    await db.commit()
    return classes


async def list_subjects(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    limit: int = 50,
) -> list[Subject]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Subject page size must be between 1 and 100.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:read",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    result = await db.execute(
        select(Subject)
        .where(
            Subject.tenant_id == tenant_id,
            Subject.school_id == school_id,
        )
        .order_by(Subject.name, Subject.code)
        .limit(limit)
    )
    subjects = list(result.scalars().all())
    await record_event(
        db,
        event_type="subject.directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="subject_directory",
        event_data={
            "school_id": str(school_id),
            "returned_count": len(subjects),
            "limit": limit,
        },
    )
    await db.commit()
    return subjects



async def list_accessible_tenants(
    db: AsyncSession,
    *,
    actor_id: UUID,
    limit: int = 50,
) -> list[Tenant]:
    """List only active tenants where the actor has a current membership and scope grant."""
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("Tenant page size must be between 1 and 100.")
    now = datetime.now(UTC)
    result = await db.execute(
        select(Tenant)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == actor_id,
            TenantMembership.status == "active",
            Tenant.status == "active",
            or_(TenantMembership.starts_at.is_(None), TenantMembership.starts_at <= now),
            or_(TenantMembership.ends_at.is_(None), TenantMembership.ends_at > now),
        )
        .order_by(Tenant.name, Tenant.id)
        .limit(limit)
    )
    candidates = list(result.scalars().all())
    accessible: list[Tenant] = []
    for tenant in candidates:
        for permission_key in (
            "school:read",
            "school:create",
            "academic_year:create",
            "class:create",
            "subject:create",
        ):
            if await has_permission(
                db,
                user_id=actor_id,
                permission_key=permission_key,
                tenant_id=tenant.id,
                scope_type="tenant",
                scope_id=tenant.id,
            ):
                accessible.append(tenant)
                break

    await record_event(
        db,
        event_type="tenant.accessible_directory.read",
        actor_id=actor_id,
        aggregate_type="tenant_directory",
        event_data={"returned_count": len(accessible), "limit": limit},
    )
    await db.commit()
    return accessible
