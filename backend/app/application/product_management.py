from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import InvalidApplicationInput, ResourceNotFound


async def list_product_schools(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict]:
    if not 1 <= limit <= 100:
        raise InvalidApplicationInput("تعداد مدارس درخواستی معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT
                s.id,
                s.name,
                s.is_active,
                COUNT(DISTINCT c.id) AS class_count
            FROM public.schools AS s
            LEFT JOIN public.academic_years AS ay ON ay.school_id = s.id
            LEFT JOIN public.classes AS c ON c.academic_year_id = ay.id
            GROUP BY s.id, s.name, s.is_active
            ORDER BY s.is_active DESC, s.name ASC, s.id ASC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [dict(row) for row in result.mappings().all()]


async def get_product_school(
    db: AsyncSession,
    *,
    school_id: int,
) -> dict | None:
    if school_id <= 0:
        raise InvalidApplicationInput("شناسه مدرسه معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT
                s.id,
                s.name,
                s.is_active,
                COUNT(DISTINCT c.id) AS class_count
            FROM public.schools AS s
            LEFT JOIN public.academic_years AS ay ON ay.school_id = s.id
            LEFT JOIN public.classes AS c ON c.academic_year_id = ay.id
            WHERE s.id = :school_id
            GROUP BY s.id, s.name, s.is_active
            """
        ),
        {"school_id": school_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def list_product_classes(
    db: AsyncSession,
    *,
    school_id: int,
    limit: int = 100,
) -> list[dict]:
    if school_id <= 0:
        raise InvalidApplicationInput("شناسه مدرسه معتبر نیست.")
    if not 1 <= limit <= 200:
        raise InvalidApplicationInput("تعداد کلاس‌های درخواستی معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT
                c.id,
                c.name,
                c.grade,
                c.section,
                c.is_active,
                c.group_chat_id,
                c.group_chat_title
            FROM public.classes AS c
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            WHERE ay.school_id = :school_id
            ORDER BY c.is_active DESC, c.grade ASC, c.name ASC, c.id ASC
            LIMIT :limit
            """
        ),
        {"school_id": school_id, "limit": limit},
    )
    return [dict(row) for row in result.mappings().all()]


async def create_product_school(
    db: AsyncSession,
    *,
    name: str,
) -> dict:
    normalized_name = " ".join(name.split())
    if not 2 <= len(normalized_name) <= 180:
        raise InvalidApplicationInput("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.")

    result = await db.execute(
        text(
            """
            INSERT INTO public.schools (name, timezone, is_active)
            VALUES (:name, 'Asia/Tehran', TRUE)
            RETURNING id, name, is_active
            """
        ),
        {"name": normalized_name},
    )
    row = result.mappings().one()
    return dict(row)


async def require_product_school(
    db: AsyncSession,
    *,
    school_id: int,
) -> dict:
    school = await get_product_school(db, school_id=school_id)
    if school is None:
        raise ResourceNotFound("مدرسه موردنظر پیدا نشد.")
    return school


async def create_product_class(
    db: AsyncSession,
    *,
    school_id: int,
    name: str,
    grade: str,
    section: str | None = None,
) -> dict:
    normalized_name = " ".join(name.split())
    normalized_grade = " ".join(grade.split())
    normalized_section = " ".join(section.split()) if section else None
    if school_id <= 0:
        raise InvalidApplicationInput("شناسه مدرسه معتبر نیست.")
    if not 2 <= len(normalized_name) <= 80:
        raise InvalidApplicationInput("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
    if not 1 <= len(normalized_grade) <= 32:
        raise InvalidApplicationInput("پایه باید بین ۱ تا ۳۲ نویسه باشد.")
    if normalized_section is not None and len(normalized_section) > 32:
        raise InvalidApplicationInput("عنوان بخش کلاس حداکثر ۳۲ نویسه است.")

    school_result = await db.execute(
        text(
            "SELECT id FROM public.schools "
            "WHERE id = :school_id AND is_active IS TRUE"
        ),
        {"school_id": school_id},
    )
    if school_result.scalar_one_or_none() is None:
        raise ResourceNotFound("مدرسه فعال پیدا نشد.")

    # ClassMate's current bot and worker still require academic_year_id.
    # It is an internal compatibility key: admins never select or manage it.
    year_result = await db.execute(
        text(
            """
            SELECT id
            FROM public.academic_years
            WHERE school_id = :school_id
            ORDER BY is_current DESC, id DESC
            LIMIT 1
            """
        ),
        {"school_id": school_id},
    )
    academic_year_id = year_result.scalar_one_or_none()
    if academic_year_id is None:
        internal_year = await db.execute(
            text(
                """
                INSERT INTO public.academic_years
                    (school_id, name, starts_on, ends_on, is_current)
                VALUES
                    (:school_id, 'پیش‌فرض داخلی', NULL, NULL, TRUE)
                ON CONFLICT (school_id, name)
                DO UPDATE SET is_current = TRUE
                RETURNING id
                """
            ),
            {"school_id": school_id},
        )
        academic_year_id = internal_year.scalar_one()

    result = await db.execute(
        text(
            """
            INSERT INTO public.classes
                (academic_year_id, name, grade, section, is_active)
            VALUES
                (:academic_year_id, :name, :grade, :section, TRUE)
            RETURNING id, name, grade, section, is_active
            """
        ),
        {
            "academic_year_id": academic_year_id,
            "name": normalized_name,
            "grade": normalized_grade,
            "section": normalized_section,
        },
    )
    return dict(result.mappings().one())
