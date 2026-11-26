from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import and_, func, literal, select, union_all, update
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
    ClassGroupLinkHandshake,
    ClassSubject,
    ClassTelegramGroup,
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
    offset: int = 0,
) -> list[Tenant]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("Tenant page size or offset is invalid.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="platform:tenants:read",
        tenant_id=None,
    )
    result = await db.execute(
        select(Tenant).order_by(Tenant.created_at.desc(), Tenant.id).limit(limit).offset(offset)
    )
    tenants = list(result.scalars().all())
    await record_event(
        db,
        event_type="tenant.directory.read",
        actor_id=actor_id,
        aggregate_type="tenant_directory",
        event_data={"returned_count": len(tenants), "limit": limit, "offset": offset},
    )
    await db.commit()
    return tenants


_TENANT_ROLE_TEMPLATES = {
    "tenant_admin": (
        "مدیر مجموعه",
        (
            "school:read",
            "school:create",
            "school:manage",
            "academic_year:create",
            "academic_year:manage",
            "class:create",
            "class:manage",
            "subject:create",
            "subject:manage",
            "class:group:manage",
            "class:subjects:manage",
            "membership:read",
            "membership:invite",
            "membership:manage",
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
    offset: int = 0,
) -> list[School]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("School page size or offset is invalid.")
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
        .where(School.tenant_id == tenant_id, School.status != "archived")
        .order_by(School.created_at.desc(), School.id)
        .limit(limit)
        .offset(offset)
    )
    schools = list(result.scalars().all())
    await record_event(
        db,
        event_type="school.directory.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school_directory",
        event_data={"returned_count": len(schools), "limit": limit, "offset": offset},
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
                AcademicYear.status != "archived",
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
    offset: int = 0,
) -> list[AcademicYear]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("Academic year page size or offset is invalid.")
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
            AcademicYear.status != "archived",
        )
        .order_by(AcademicYear.starts_on.desc(), AcademicYear.name)
        .limit(limit)
        .offset(offset)
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
            "offset": offset,
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
    offset: int = 0,
) -> list[ClassGroup]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("Class page size or offset is invalid.")
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
        ClassGroup.status != "archived",
    )
    if academic_year_id is not None:
        statement = statement.where(ClassGroup.academic_year_id == academic_year_id)
    result = await db.execute(
        statement.order_by(ClassGroup.name, ClassGroup.code).limit(limit).offset(offset)
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
            "offset": offset,
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
    offset: int = 0,
) -> list[Subject]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("Subject page size or offset is invalid.")
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
            Subject.status != "archived",
        )
        .order_by(Subject.name, Subject.code)
        .limit(limit)
        .offset(offset)
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
            "offset": offset,
        },
    )
    await db.commit()
    return subjects


async def list_accessible_tenants(
    db: AsyncSession,
    *,
    actor_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[Tenant]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("Tenant page size or offset is invalid.")
    result = await db.execute(
        select(Tenant)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == actor_id,
            TenantMembership.status == "active",
            Tenant.status == "active",
        )
        .order_by(Tenant.created_at.desc(), Tenant.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


# --- Structure lifecycle operations ---


async def update_school(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    name: str,
) -> School:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    normalized_name = name.strip()
    if not 2 <= len(normalized_name) <= 180:
        raise InvalidApplicationInput("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.")
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
        raise ResourceNotFound("مدرسهٔ فعال در این مجموعه پیدا نشد.")
    if school.name == normalized_name:
        return school
    previous_name = school.name
    school.name = normalized_name
    await record_event(
        db,
        event_type="school.updated",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school",
        aggregate_id=school.id,
        event_data={"previous_name": previous_name, "name": normalized_name},
    )
    await _commit_or_conflict(db, "ویرایش مدرسه انجام نشد.")
    await db.refresh(school)
    return school


async def archive_school(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    school = (
        await db.execute(
            select(School).where(
                School.id == school_id,
                School.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if school is None:
        raise ResourceNotFound("مدرسه در این مجموعه پیدا نشد.")
    if school.status == "archived":
        return False

    active_counts = []
    for model in (AcademicYear, ClassGroup, Subject):
        count = int(
            (
                await db.execute(
                    select(func.count()).select_from(model).where(
                        model.tenant_id == tenant_id,
                        model.school_id == school_id,
                        model.status != "archived",
                    )
                )
            ).scalar_one()
            or 0
        )
        active_counts.append(count)
    if any(active_counts):
        raise ResourceConflict(
            "ابتدا سال‌های تحصیلی، کلاس‌ها و درس‌های فعال این مدرسه را بایگانی کنید."
        )

    school.status = "archived"
    await record_event(
        db,
        event_type="school.archived",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school",
        aggregate_id=school.id,
        event_data={"code": school.code},
    )
    await db.commit()
    return True


async def update_academic_year(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    academic_year_id: UUID,
    name: str | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
) -> AcademicYear:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="academic_year:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    year = (
        await db.execute(
            select(AcademicYear).where(
                AcademicYear.id == academic_year_id,
                AcademicYear.tenant_id == tenant_id,
                AcademicYear.school_id == school_id,
                AcademicYear.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if year is None:
        raise ResourceNotFound("سال تحصیلی فعال در این مدرسه پیدا نشد.")

    normalized_name = year.name if name is None else name.strip()
    new_start = year.starts_on if starts_on is None else starts_on
    new_end = year.ends_on if ends_on is None else ends_on
    if not 1 <= len(normalized_name) <= 64:
        raise InvalidApplicationInput("نام سال تحصیلی باید بین ۱ تا ۶۴ نویسه باشد.")
    if new_end <= new_start:
        raise InvalidApplicationInput("تاریخ پایان باید بعد از تاریخ شروع باشد.")

    overlapping = (
        await db.execute(
            select(AcademicYear.id).where(
                AcademicYear.tenant_id == tenant_id,
                AcademicYear.school_id == school_id,
                AcademicYear.id != year.id,
                AcademicYear.status != "archived",
                AcademicYear.starts_on <= new_end,
                AcademicYear.ends_on >= new_start,
            )
        )
    ).scalar_one_or_none()
    if overlapping is not None:
        raise ResourceConflict("بازهٔ این سال تحصیلی با سال دیگری تداخل دارد.")

    previous = {
        "name": year.name,
        "starts_on": year.starts_on.isoformat(),
        "ends_on": year.ends_on.isoformat(),
    }
    updated = {
        "name": normalized_name,
        "starts_on": new_start.isoformat(),
        "ends_on": new_end.isoformat(),
    }
    if previous == updated:
        return year
    year.name = normalized_name
    year.starts_on = new_start
    year.ends_on = new_end
    await record_event(
        db,
        event_type="academic_year.updated",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="academic_year",
        aggregate_id=year.id,
        event_data={"previous": previous, "current": updated},
    )
    await _commit_or_conflict(db, "نام سال تحصیلی در این مدرسه قبلاً ثبت شده است.")
    await db.refresh(year)
    return year


async def archive_academic_year(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    academic_year_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="academic_year:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    year = (
        await db.execute(
            select(AcademicYear).where(
                AcademicYear.id == academic_year_id,
                AcademicYear.tenant_id == tenant_id,
                AcademicYear.school_id == school_id,
            )
        )
    ).scalar_one_or_none()
    if year is None:
        raise ResourceNotFound("سال تحصیلی در این مدرسه پیدا نشد.")
    if year.status == "archived":
        return False
    active_classes = int(
        (
            await db.execute(
                select(func.count()).select_from(ClassGroup).where(
                    ClassGroup.tenant_id == tenant_id,
                    ClassGroup.school_id == school_id,
                    ClassGroup.academic_year_id == academic_year_id,
                    ClassGroup.status != "archived",
                )
            )
        ).scalar_one()
        or 0
    )
    if active_classes:
        raise ResourceConflict("ابتدا کلاس‌های فعال این سال را بایگانی کنید.")
    year.status = "archived"
    await record_event(
        db,
        event_type="academic_year.archived",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="academic_year",
        aggregate_id=year.id,
        event_data={"name": year.name},
    )
    await db.commit()
    return True


async def update_class(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    class_group_id: UUID,
    name: str | None = None,
    grade: str | None = None,
) -> ClassGroup:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    class_group = (
        await db.execute(
            select(ClassGroup).where(
                ClassGroup.id == class_group_id,
                ClassGroup.tenant_id == tenant_id,
                ClassGroup.school_id == school_id,
                ClassGroup.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if class_group is None:
        raise ResourceNotFound("کلاس فعال در این مدرسه پیدا نشد.")
    new_name = class_group.name if name is None else name.strip()
    new_grade = class_group.grade if grade is None else grade.strip()
    if not 1 <= len(new_name) <= 120:
        raise InvalidApplicationInput("نام کلاس باید بین ۱ تا ۱۲۰ نویسه باشد.")
    if not 1 <= len(new_grade) <= 48:
        raise InvalidApplicationInput("پایه/مقطع باید بین ۱ تا ۴۸ نویسه باشد.")
    if new_name == class_group.name and new_grade == class_group.grade:
        return class_group
    previous = {"name": class_group.name, "grade": class_group.grade}
    class_group.name = new_name
    class_group.grade = new_grade
    await record_event(
        db,
        event_type="class.updated",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={
            "previous": previous,
            "current": {"name": new_name, "grade": new_grade},
        },
    )
    await db.commit()
    await db.refresh(class_group)
    return class_group


async def archive_class(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    class_group_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    class_group = (
        await db.execute(
            select(ClassGroup).where(
                ClassGroup.id == class_group_id,
                ClassGroup.tenant_id == tenant_id,
                ClassGroup.school_id == school_id,
            )
        )
    ).scalar_one_or_none()
    if class_group is None:
        raise ResourceNotFound("کلاس در این مدرسه پیدا نشد.")
    if class_group.status == "archived":
        return False

    now = datetime.now(UTC)
    connection = (
        await db.execute(
            select(ClassTelegramGroup).where(
                ClassTelegramGroup.class_group_id == class_group_id,
                ClassTelegramGroup.status == "active",
            )
        )
    ).scalar_one_or_none()
    if connection is not None:
        connection.status = "revoked"
        connection.updated_at = now

    await db.execute(
        update(ClassGroupLinkHandshake)
        .where(
            ClassGroupLinkHandshake.class_group_id == class_group_id,
            ClassGroupLinkHandshake.consumed_at.is_(None),
            ClassGroupLinkHandshake.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.execute(
        update(ClassSubject)
        .where(
            ClassSubject.class_group_id == class_group_id,
            ClassSubject.status == "active",
        )
        .values(status="suspended", updated_at=now)
    )
    class_group.status = "archived"
    await record_event(
        db,
        event_type="class.archived",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={
            "code": class_group.code,
            "group_connection_revoked": connection is not None,
        },
    )
    await db.commit()
    return True


async def update_subject(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    subject_id: UUID,
    name: str,
) -> Subject:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="subject:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    subject = (
        await db.execute(
            select(Subject).where(
                Subject.id == subject_id,
                Subject.tenant_id == tenant_id,
                Subject.school_id == school_id,
                Subject.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if subject is None:
        raise ResourceNotFound("درس فعال در این مدرسه پیدا نشد.")
    normalized_name = name.strip()
    if not 1 <= len(normalized_name) <= 120:
        raise InvalidApplicationInput("نام درس باید بین ۱ تا ۱۲۰ نویسه باشد.")
    if subject.name == normalized_name:
        return subject
    previous_name = subject.name
    subject.name = normalized_name
    await record_event(
        db,
        event_type="subject.updated",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="subject",
        aggregate_id=subject.id,
        event_data={"previous_name": previous_name, "name": normalized_name},
    )
    await db.commit()
    await db.refresh(subject)
    return subject


async def archive_subject(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    subject_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="subject:manage",
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )
    subject = (
        await db.execute(
            select(Subject).where(
                Subject.id == subject_id,
                Subject.tenant_id == tenant_id,
                Subject.school_id == school_id,
            )
        )
    ).scalar_one_or_none()
    if subject is None:
        raise ResourceNotFound("درس در این مدرسه پیدا نشد.")
    if subject.status == "archived":
        return False
    now = datetime.now(UTC)
    await db.execute(
        update(ClassSubject)
        .where(
            ClassSubject.subject_id == subject_id,
            ClassSubject.status == "active",
        )
        .values(status="suspended", updated_at=now)
    )
    subject.status = "archived"
    await record_event(
        db,
        event_type="subject.archived",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="subject",
        aggregate_id=subject.id,
        event_data={"code": subject.code},
    )
    await db.commit()
    return True

# --- Archive bin and safe restore operations ---


async def list_archived_schools(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    limit: int = 8,
    offset: int = 0,
) -> dict:
    if not 1 <= limit <= 25 or offset < 0:
        raise InvalidApplicationInput("بازهٔ صفحه‌بندی صندوق بایگانی معتبر نیست.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(School).where(
                    School.tenant_id == tenant_id,
                    School.status == "archived",
                )
            )
        ).scalar_one()
        or 0
    )
    result = await db.execute(
        select(School)
        .where(School.tenant_id == tenant_id, School.status == "archived")
        .order_by(School.name, School.id)
        .offset(offset)
        .limit(limit)
    )
    items = list(result.scalars().all())
    pages = (total + limit - 1) // limit
    page = offset // limit + 1
    await record_event(
        db,
        event_type="school.archive_bin.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school_archive_bin",
        event_data={"returned_count": len(items), "total": total, "page": page},
    )
    await db.commit()
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
    }


async def list_archived_school_structure(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    limit: int = 8,
    offset: int = 0,
) -> dict:
    if not 1 <= limit <= 25 or offset < 0:
        raise InvalidApplicationInput("بازهٔ صفحه‌بندی صندوق بایگانی معتبر نیست.")
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:manage",
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
        raise ResourceNotFound("مدرسهٔ فعال در این مجموعه پیدا نشد.")

    archived_items = union_all(
        select(
            literal("year").label("kind"),
            AcademicYear.id.label("id"),
            AcademicYear.name.label("name"),
            AcademicYear.created_at.label("created_at"),
        ).where(
            AcademicYear.tenant_id == tenant_id,
            AcademicYear.school_id == school_id,
            AcademicYear.status == "archived",
        ),
        select(
            literal("class").label("kind"),
            ClassGroup.id.label("id"),
            ClassGroup.name.label("name"),
            ClassGroup.created_at.label("created_at"),
        ).where(
            ClassGroup.tenant_id == tenant_id,
            ClassGroup.school_id == school_id,
            ClassGroup.status == "archived",
        ),
        select(
            literal("subject").label("kind"),
            Subject.id.label("id"),
            Subject.name.label("name"),
            Subject.created_at.label("created_at"),
        ).where(
            Subject.tenant_id == tenant_id,
            Subject.school_id == school_id,
            Subject.status == "archived",
        ),
    ).subquery()

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(archived_items)
            )
        ).scalar_one()
        or 0
    )
    result = await db.execute(
        select(
            archived_items.c.kind,
            archived_items.c.id,
            archived_items.c.name,
        )
        .order_by(archived_items.c.kind, archived_items.c.name, archived_items.c.id)
        .offset(offset)
        .limit(limit)
    )
    items = [
        {"kind": kind, "id": item_id, "name": name}
        for kind, item_id, name in result.all()
    ]
    pages = (total + limit - 1) // limit
    page = offset // limit + 1
    await record_event(
        db,
        event_type="school.structure_archive_bin.read",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school_structure_archive_bin",
        aggregate_id=school_id,
        event_data={"returned_count": len(items), "total": total, "page": page},
    )
    await db.commit()
    return {
        "school_name": school.name,
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
    }


async def restore_school(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="school:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    school = (
        await db.execute(
            select(School).where(
                School.id == school_id,
                School.tenant_id == tenant_id,
                School.status == "archived",
            )
        )
    ).scalar_one_or_none()
    if school is None:
        return False
    previous_status = school.status
    school.status = "setup"
    await record_event(
        db,
        event_type="school.restored",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="school",
        aggregate_id=school.id,
        event_data={
            "code": school.code,
            "previous_status": previous_status,
            "restored_status": school.status,
        },
    )
    await db.commit()
    return True


async def restore_academic_year(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    academic_year_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="academic_year:manage",
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
        raise ResourceConflict("ابتدا مدرسه را از صندوق بایگانی بازیابی کنید.")
    year = (
        await db.execute(
            select(AcademicYear).where(
                AcademicYear.id == academic_year_id,
                AcademicYear.tenant_id == tenant_id,
                AcademicYear.school_id == school_id,
                AcademicYear.status == "archived",
            )
        )
    ).scalar_one_or_none()
    if year is None:
        return False
    overlapping = (
        await db.execute(
            select(AcademicYear.id).where(
                AcademicYear.tenant_id == tenant_id,
                AcademicYear.school_id == school_id,
                AcademicYear.id != year.id,
                AcademicYear.status != "archived",
                AcademicYear.starts_on <= year.ends_on,
                AcademicYear.ends_on >= year.starts_on,
            )
        )
    ).scalar_one_or_none()
    if overlapping is not None:
        raise ResourceConflict("بازهٔ این سال با یک سال تحصیلی فعال تداخل دارد.")
    year.status = "planned"
    await record_event(
        db,
        event_type="academic_year.restored",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="academic_year",
        aggregate_id=year.id,
        event_data={"restored_status": "planned"},
    )
    await db.commit()
    return True


async def restore_class(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    class_group_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="class:manage",
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
        raise ResourceConflict("ابتدا مدرسه را از صندوق بایگانی بازیابی کنید.")
    class_group = (
        await db.execute(
            select(ClassGroup).where(
                ClassGroup.id == class_group_id,
                ClassGroup.tenant_id == tenant_id,
                ClassGroup.school_id == school_id,
                ClassGroup.status == "archived",
            )
        )
    ).scalar_one_or_none()
    if class_group is None:
        return False
    year = (
        await db.execute(
            select(AcademicYear).where(
                AcademicYear.id == class_group.academic_year_id,
                AcademicYear.tenant_id == tenant_id,
                AcademicYear.school_id == school_id,
                AcademicYear.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if year is None:
        raise ResourceConflict("ابتدا سال تحصیلی این کلاس را بازیابی کنید.")

    class_group.status = "active"
    links = (
        await db.execute(
            select(ClassSubject)
            .join(
                Subject,
                and_(
                    Subject.id == ClassSubject.subject_id,
                    Subject.tenant_id == ClassSubject.tenant_id,
                    Subject.school_id == ClassSubject.school_id,
                ),
            )
            .where(
                ClassSubject.class_group_id == class_group.id,
                ClassSubject.status == "suspended",
                Subject.status == "active",
            )
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for link in links:
        link.status = "active"
        link.updated_at = now
    await record_event(
        db,
        event_type="class.restored",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="class",
        aggregate_id=class_group.id,
        event_data={
            "restored_status": "active",
            "restored_subject_assignments": len(links),
            "telegram_group_requires_reconnect": True,
        },
    )
    await db.commit()
    return True


async def restore_subject(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    subject_id: UUID,
) -> bool:
    await _require(
        db,
        actor_id=actor_id,
        permission_key="subject:manage",
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
        raise ResourceConflict("ابتدا مدرسه را از صندوق بایگانی بازیابی کنید.")
    subject = (
        await db.execute(
            select(Subject).where(
                Subject.id == subject_id,
                Subject.tenant_id == tenant_id,
                Subject.school_id == school_id,
                Subject.status == "archived",
            )
        )
    ).scalar_one_or_none()
    if subject is None:
        return False
    subject.status = "active"
    links = (
        await db.execute(
            select(ClassSubject)
            .join(
                ClassGroup,
                and_(
                    ClassGroup.id == ClassSubject.class_group_id,
                    ClassGroup.tenant_id == ClassSubject.tenant_id,
                    ClassGroup.school_id == ClassSubject.school_id,
                ),
            )
            .where(
                ClassSubject.subject_id == subject.id,
                ClassSubject.status == "suspended",
                ClassGroup.status == "active",
            )
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for link in links:
        link.status = "active"
        link.updated_at = now
    await record_event(
        db,
        event_type="subject.restored",
        actor_id=actor_id,
        tenant_id=tenant_id,
        aggregate_type="subject",
        aggregate_id=subject.id,
        event_data={
            "restored_status": "active",
            "restored_class_assignments": len(links),
        },
    )
    await db.commit()
    return True

