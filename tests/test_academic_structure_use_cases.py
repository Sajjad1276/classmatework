from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.tenants import (
    create_academic_year,
    create_class,
    create_school,
    create_subject,
    list_academic_years,
    list_classes,
    list_schools,
    list_subjects,
)
from app.db.base import Base
from app.db.models import (
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    Tenant,
    User,
)


async def _setup_structure_access(db):
    user = User(display_name="Structure Owner", status="active")
    tenant = Tenant(
        slug=f"structure-{uuid4().hex[:8]}",
        name="Structure Tenant",
        status="active",
    )
    role = Role(
        tenant_id=None,
        key=f"structure_owner_{uuid4().hex[:8]}",
        name="Structure Owner",
        is_system=True,
    )
    keys = [
        "school:read",
        "school:create",
        "academic_year:create",
        "class:create",
        "subject:create",
    ]
    permissions = [
        Permission(key=key, description=key)
        for key in keys
    ]
    db.add_all([user, tenant, role, *permissions])
    await db.flush()
    db.add_all(
        [
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                effect="allow",
            )
            for permission in permissions
        ]
        + [
            RoleBinding(
                user_id=user.id,
                role_id=role.id,
                tenant_id=None,
                scope_type="platform",
                scope_id=None,
                created_by_id=user.id,
            )
        ]
    )
    await db.commit()
    return user, tenant


@pytest.mark.asyncio
async def test_school_academic_class_and_subject_use_cases() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        owner, tenant = await _setup_structure_access(db)
        school = await create_school(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            name="دبیرستان نمونه",
            code="SAMPLE-01",
        )
        assert school.status == "setup"

        schools = await list_schools(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
        )
        assert [item.id for item in schools] == [school.id]

        year = await create_academic_year(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            school_id=school.id,
            name="۱۴۰۵-۱۴۰۶",
            starts_on=date(2026, 3, 21),
            ends_on=date(2027, 3, 20),
        )
        years = await list_academic_years(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            school_id=school.id,
        )
        assert [item.id for item in years] == [year.id]

        class_group = await create_class(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            school_id=school.id,
            academic_year_id=year.id,
            name="کلاس دهم الف",
            code="10-A",
            grade="دهم",
        )
        classes = await list_classes(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            school_id=school.id,
            academic_year_id=year.id,
        )
        assert [item.id for item in classes] == [class_group.id]

        subject = await create_subject(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            school_id=school.id,
            name="ریاضی",
            code="MATH",
        )
        subjects = await list_subjects(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            school_id=school.id,
        )
        assert [item.id for item in subjects] == [subject.id]

    await engine.dispose()


@pytest.mark.asyncio
async def test_academic_year_use_case_rejects_invalid_date_range() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        owner, tenant = await _setup_structure_access(db)
        school = await create_school(
            db,
            actor_id=owner.id,
            tenant_id=tenant.id,
            name="Invalid Date School",
            code="IDS",
        )
        from app.application.errors import InvalidApplicationInput

        with pytest.raises(InvalidApplicationInput):
            await create_academic_year(
                db,
                actor_id=owner.id,
                tenant_id=tenant.id,
                school_id=school.id,
                name="Invalid",
                starts_on=date(2027, 3, 20),
                ends_on=date(2027, 3, 20),
            )

    await engine.dispose()
