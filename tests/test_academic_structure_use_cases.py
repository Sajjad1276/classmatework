from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.tenants import (
    archive_academic_year,
    archive_class,
    archive_school,
    archive_subject,
    create_academic_year,
    create_class,
    create_school,
    create_subject,
    list_academic_years,
    list_archived_school_structure,
    list_archived_schools,
    list_classes,
    list_schools,
    list_subjects,
    restore_academic_year,
    restore_class,
    restore_school,
    restore_subject,
    update_academic_year,
    update_class,
    update_school,
    update_subject,
)
from app.db.base import Base
from app.db.models import (
    ClassSubject,
    ClassTelegramGroup,
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
        "school:manage",
        "academic_year:manage",
        "class:manage",
        "subject:manage",
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


@pytest.mark.asyncio
async def test_structure_edit_and_archive_preserve_history_and_dependencies() -> None:
    from app.application.errors import ResourceConflict

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        owner, tenant = await _setup_structure_access(db)
        school = await create_school(
            db, actor_id=owner.id, tenant_id=tenant.id,
            name="مدرسه قدیمی", code="OLD-SCHOOL",
        )
        school = await update_school(
            db, actor_id=owner.id, tenant_id=tenant.id,
            school_id=school.id, name="مدرسه مرکزی",
        )
        assert school.name == "مدرسه مرکزی"

        year = await create_academic_year(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            name="۱۴۰۵-۱۴۰۶", starts_on=date(2026, 3, 21), ends_on=date(2027, 3, 20),
        )
        year = await update_academic_year(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            academic_year_id=year.id, name="۱۴۰۵-۱۴۰۶ اصلی",
            starts_on=date(2026, 3, 22), ends_on=date(2027, 3, 20),
        )
        assert year.name == "۱۴۰۵-۱۴۰۶ اصلی"
        assert year.starts_on == date(2026, 3, 22)

        class_group = await create_class(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            academic_year_id=year.id, name="دهم الف", code="10-A", grade="دهم",
        )
        class_group = await update_class(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            class_group_id=class_group.id, name="دهم تجربی الف", grade="دهم تجربی",
        )
        assert class_group.name == "دهم تجربی الف"
        assert class_group.grade == "دهم تجربی"

        subject = await create_subject(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            name="ریاضی", code="MATH",
        )
        manually_removed_subject = await create_subject(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            name="فارسی", code="PERSIAN",
        )
        subject = await update_subject(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            subject_id=subject.id, name="ریاضی و آمار",
        )
        assert subject.name == "ریاضی و آمار"

        assignment = ClassSubject(
            tenant_id=tenant.id, school_id=school.id, class_group_id=class_group.id,
            subject_id=subject.id, assigned_by_id=owner.id, status="active",
        )
        group_connection = ClassTelegramGroup(
            tenant_id=tenant.id, class_group_id=class_group.id,
            telegram_chat_id=-10012345678, chat_title="گروه دهم تجربی الف",
            chat_type="supergroup", status="active", connected_by_id=owner.id,
            integration_bot_id=900000001,
        )
        manually_inactive_assignment = ClassSubject(
            tenant_id=tenant.id, school_id=school.id, class_group_id=class_group.id,
            subject_id=manually_removed_subject.id, assigned_by_id=owner.id,
            status="inactive",
        )
        db.add_all([assignment, group_connection, manually_inactive_assignment])
        await db.commit()

        with pytest.raises(ResourceConflict):
            await archive_school(
                db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            )
        with pytest.raises(ResourceConflict):
            await archive_academic_year(
                db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
                academic_year_id=year.id,
            )

        assert await archive_class(
            db, actor_id=owner.id, tenant_id=tenant.id,
            school_id=school.id, class_group_id=class_group.id,
        )
        await db.refresh(class_group)
        await db.refresh(group_connection)
        await db.refresh(assignment)
        assert class_group.status == "archived"
        assert group_connection.status == "revoked"
        assert assignment.status == "suspended"
        assert manually_inactive_assignment.status == "inactive"
        assert not await archive_class(
            db, actor_id=owner.id, tenant_id=tenant.id,
            school_id=school.id, class_group_id=class_group.id,
        )
        assert await list_classes(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id
        ) == []

        assert await archive_academic_year(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            academic_year_id=year.id,
        )
        assert await list_academic_years(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id
        ) == []

        assert await archive_subject(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
            subject_id=subject.id,
        )
        assert await list_subjects(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id
        ) == []

        assert await archive_school(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
        )
        await db.refresh(school)
        assert school.status == "archived"
        assert await list_schools(
            db, actor_id=owner.id, tenant_id=tenant.id
        ) == []

        school_bin = await list_archived_schools(
            db, actor_id=owner.id, tenant_id=tenant.id
        )
        assert [item.id for item in school_bin["items"]] == [school.id]
        assert await restore_school(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id,
        )
        await db.refresh(school)
        assert school.status == "setup"

        structure_bin = await list_archived_school_structure(
            db, actor_id=owner.id, tenant_id=tenant.id, school_id=school.id
        )
        assert {item["kind"] for item in structure_bin["items"]} == {
            "year", "class", "subject"
        }

        with pytest.raises(ResourceConflict):
            await restore_class(
                db, actor_id=owner.id, tenant_id=tenant.id,
                school_id=school.id, class_group_id=class_group.id,
            )
        assert await restore_academic_year(
            db, actor_id=owner.id, tenant_id=tenant.id,
            school_id=school.id, academic_year_id=year.id,
        )
        assert await restore_subject(
            db, actor_id=owner.id, tenant_id=tenant.id,
            school_id=school.id, subject_id=subject.id,
        )
        await db.refresh(assignment)
        assert assignment.status == "suspended"
        assert await restore_class(
            db, actor_id=owner.id, tenant_id=tenant.id,
            school_id=school.id, class_group_id=class_group.id,
        )
        await db.refresh(class_group)
        await db.refresh(assignment)
        await db.refresh(group_connection)
        await db.refresh(manually_inactive_assignment)
        assert class_group.status == "active"
        assert assignment.status == "active"
        assert manually_inactive_assignment.status == "inactive"
        # The bot was not silently re-added to the Telegram group.
        assert group_connection.status == "revoked"

    await engine.dispose()
