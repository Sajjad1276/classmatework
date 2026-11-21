from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.db.models import (
    AcademicYear,
    AuditEvent,
    ClassGroup,
    Organization,
    School,
    Subject,
    Tenant,
)
from app.modules.access.policy import require_permission
from app.modules.audit.schemas import AuditEventRead
from app.modules.audit.service import record_event
from app.modules.identity.dependencies import Principal, get_current_principal, require_csrf
from app.modules.tenants.context import get_active_tenant
from app.modules.tenants.schemas import (
    AcademicYearCreate,
    AcademicYearRead,
    ClassCreate,
    ClassRead,
    SchoolCreate,
    SchoolRead,
    SubjectCreate,
    TenantCreate,
    TenantRead,
)

router = APIRouter()


@router.get("/tenants", response_model=list[TenantRead])
async def list_tenants(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=250),
) -> list[Tenant]:
    await require_permission(db, user_id=principal.user.id, permission_key="platform:tenants:read", tenant_id=None)
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()).limit(limit))
    return list(result.scalars().all())


@router.post("/tenants", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    await require_csrf(request, principal)
    await require_permission(db, user_id=principal.user.id, permission_key="platform:tenants:create", tenant_id=None)
    existing = (await db.execute(select(Tenant.id).where(Tenant.slug == payload.slug))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant slug already exists.")
    tenant = Tenant(
        name=payload.name.strip(),
        slug=payload.slug,
        timezone=payload.timezone,
        locale=payload.locale,
        status="active",
    )
    db.add(tenant)
    await db.flush()
    await record_event(
        db,
        event_type="tenant.created",
        actor_id=principal.user.id,
        tenant_id=tenant.id,
        aggregate_type="tenant",
        aggregate_id=tenant.id,
        event_data={"slug": tenant.slug, "name": tenant.name},
    )
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/schools", response_model=list[SchoolRead])
async def list_schools(
    tenant: Tenant = Depends(get_active_tenant),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=250),
) -> list[School]:
    await require_permission(
        db, user_id=principal.user.id, permission_key="school:read",
        tenant_id=tenant.id, scope_type="tenant", scope_id=tenant.id,
    )
    result = await db.execute(
        select(School).where(School.tenant_id == tenant.id)
        .order_by(School.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


@router.post("/schools", response_model=SchoolRead, status_code=status.HTTP_201_CREATED)
async def create_school(
    payload: SchoolCreate,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> School:
    await require_csrf(request, principal)
    await require_permission(
        db, user_id=principal.user.id, permission_key="school:create",
        tenant_id=tenant.id, scope_type="tenant", scope_id=tenant.id,
    )
    if payload.organization_id is not None:
        organization = (
            await db.execute(select(Organization).where(
                Organization.id == payload.organization_id,
                Organization.tenant_id == tenant.id,
                Organization.status == "active",
            ))
        ).scalar_one_or_none()
        if organization is None:
            raise HTTPException(status_code=404, detail="Organization not found.")
    school = School(
        tenant_id=tenant.id, organization_id=payload.organization_id,
        name=payload.name.strip(), code=payload.code.strip(), status="setup",
    )
    db.add(school)
    await db.flush()
    await record_event(
        db, event_type="school.created", actor_id=principal.user.id,
        tenant_id=tenant.id, aggregate_type="school", aggregate_id=school.id,
        event_data={"code": school.code, "name": school.name},
    )
    await db.commit()
    await db.refresh(school)
    return school


@router.post("/academic-years", response_model=AcademicYearRead, status_code=status.HTTP_201_CREATED)
async def create_academic_year(
    payload: AcademicYearCreate,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> AcademicYear:
    await require_csrf(request, principal)
    await require_permission(
        db, user_id=principal.user.id, permission_key="academic_year:create",
        tenant_id=tenant.id, scope_type="school", scope_id=payload.school_id,
    )
    school = (
        await db.execute(select(School).where(
            School.id == payload.school_id, School.tenant_id == tenant.id, School.status != "archived",
        ))
    ).scalar_one_or_none()
    if school is None:
        raise HTTPException(status_code=404, detail="School not found.")
    academic_year = AcademicYear(
        tenant_id=tenant.id, school_id=school.id, name=payload.name.strip(),
        starts_on=payload.starts_on, ends_on=payload.ends_on, status="planned",
    )
    db.add(academic_year)
    await db.flush()
    await record_event(
        db, event_type="academic_year.created", actor_id=principal.user.id,
        tenant_id=tenant.id, aggregate_type="academic_year", aggregate_id=academic_year.id,
        event_data={"school_id": str(school.id), "name": academic_year.name},
    )
    await db.commit()
    await db.refresh(academic_year)
    return academic_year


@router.post("/classes", response_model=ClassRead, status_code=status.HTTP_201_CREATED)
async def create_class(
    payload: ClassCreate,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ClassGroup:
    await require_csrf(request, principal)
    await require_permission(
        db, user_id=principal.user.id, permission_key="class:create",
        tenant_id=tenant.id, scope_type="school", scope_id=payload.school_id,
    )
    academic_year = (
        await db.execute(select(AcademicYear).where(
            AcademicYear.id == payload.academic_year_id,
            AcademicYear.school_id == payload.school_id,
            AcademicYear.tenant_id == tenant.id,
        ))
    ).scalar_one_or_none()
    if academic_year is None:
        raise HTTPException(status_code=404, detail="Academic year not found for this school.")
    class_group = ClassGroup(
        tenant_id=tenant.id, school_id=payload.school_id, academic_year_id=academic_year.id,
        name=payload.name.strip(), code=payload.code.strip(), grade=payload.grade.strip(), status="active",
    )
    db.add(class_group)
    await db.flush()
    await record_event(
        db, event_type="class.created", actor_id=principal.user.id,
        tenant_id=tenant.id, aggregate_type="class", aggregate_id=class_group.id,
        event_data={"school_id": str(payload.school_id), "academic_year_id": str(academic_year.id)},
    )
    await db.commit()
    await db.refresh(class_group)
    return class_group


@router.post("/subjects", status_code=status.HTTP_201_CREATED)
async def create_subject(
    payload: SubjectCreate,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await require_csrf(request, principal)
    await require_permission(
        db, user_id=principal.user.id, permission_key="subject:create",
        tenant_id=tenant.id, scope_type="school", scope_id=payload.school_id,
    )
    school = (
        await db.execute(select(School).where(
            School.id == payload.school_id, School.tenant_id == tenant.id,
        ))
    ).scalar_one_or_none()
    if school is None:
        raise HTTPException(status_code=404, detail="School not found.")
    subject = Subject(
        tenant_id=tenant.id, school_id=school.id, name=payload.name.strip(),
        code=payload.code.strip(), status="active",
    )
    db.add(subject)
    await db.flush()
    await record_event(
        db, event_type="subject.created", actor_id=principal.user.id,
        tenant_id=tenant.id, aggregate_type="subject", aggregate_id=subject.id,
        event_data={"school_id": str(school.id), "code": subject.code},
    )
    await db.commit()
    await db.refresh(subject)
    return {
        "id": subject.id, "tenant_id": subject.tenant_id, "school_id": subject.school_id,
        "name": subject.name, "code": subject.code, "status": subject.status,
        "created_at": subject.created_at,
    }


@router.get("/audit-events", response_model=list[AuditEventRead])
async def list_audit_events(
    tenant: Tenant = Depends(get_active_tenant),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=250),
) -> list[AuditEvent]:
    await require_permission(
        db, user_id=principal.user.id, permission_key="audit:read",
        tenant_id=tenant.id, scope_type="tenant", scope_id=tenant.id,
    )
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant.id)
        .order_by(AuditEvent.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())
