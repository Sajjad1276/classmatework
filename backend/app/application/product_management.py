from __future__ import annotations

import unicodedata
from datetime import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import InvalidApplicationInput, ResourceConflict, ResourceNotFound


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



async def get_product_class(
    db: AsyncSession,
    *,
    class_id: int,
) -> dict | None:
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT
                c.id,
                c.name,
                c.grade,
                c.section,
                c.is_active,
                ay.school_id,
                s.name AS school_name
            FROM public.classes AS c
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.id = :class_id
            """
        ),
        {"class_id": class_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def list_class_subjects(
    db: AsyncSession,
    *,
    class_id: int,
) -> list[dict]:
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT s.id, s.name
            FROM public.class_subjects AS cs
            JOIN public.subjects AS s ON s.id = cs.subject_id
            WHERE cs.class_id = :class_id
            ORDER BY s.name ASC, s.id ASC
            """
        ),
        {"class_id": class_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def create_and_assign_product_subject(
    db: AsyncSession,
    *,
    class_id: int,
    name: str,
) -> dict:
    normalized_name = " ".join(name.split())
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    if not 2 <= len(normalized_name) <= 100:
        raise InvalidApplicationInput("نام درس باید بین ۲ تا ۱۰۰ نویسه باشد.")

    class_result = await get_product_class(db, class_id=class_id)
    if class_result is None or not class_result["is_active"]:
        raise ResourceNotFound("کلاس فعال پیدا نشد.")

    normalized_key = unicodedata.normalize("NFKC", normalized_name).casefold()
    subject_result = await db.execute(
        text(
            """
            INSERT INTO public.subjects (school_id, name, normalized_name)
            VALUES (:school_id, :name, :normalized_name)
            ON CONFLICT (school_id, normalized_name) DO NOTHING
            RETURNING id, name
            """
        ),
        {
            "school_id": class_result["school_id"],
            "name": normalized_name,
            "normalized_name": normalized_key,
        },
    )
    subject = subject_result.mappings().one_or_none()
    if subject is None:
        existing = await db.execute(
            text(
                """
                SELECT id, name
                FROM public.subjects
                WHERE school_id = :school_id AND normalized_name = :normalized_name
                """
            ),
            {
                "school_id": class_result["school_id"],
                "normalized_name": normalized_key,
            },
        )
        subject = existing.mappings().one()

    assignment = await db.execute(
        text(
            """
            INSERT INTO public.class_subjects (class_id, subject_id)
            VALUES (:class_id, :subject_id)
            ON CONFLICT (class_id, subject_id) DO NOTHING
            RETURNING id
            """
        ),
        {"class_id": class_id, "subject_id": subject["id"]},
    )
    return {
        "id": subject["id"],
        "name": subject["name"],
        "created": subject_result.rowcount == 1,
        "assigned": assignment.scalar_one_or_none() is not None,
        "school_id": class_result["school_id"],
    }


async def list_class_timetable(
    db: AsyncSession,
    *,
    class_id: int,
) -> list[dict]:
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT
                t.id,
                t.weekday,
                t.period,
                t.starts_at,
                t.ends_at,
                t.room,
                s.id AS subject_id,
                s.name AS subject_name
            FROM public.timetable_entries AS t
            JOIN public.subjects AS s ON s.id = t.subject_id
            WHERE t.class_id = :class_id
            ORDER BY t.weekday ASC, t.period ASC
            """
        ),
        {"class_id": class_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def create_product_timetable_entry(
    db: AsyncSession,
    *,
    class_id: int,
    subject_id: int,
    weekday: int,
    period: int,
    starts_at: str | None = None,
    ends_at: str | None = None,
    room: str | None = None,
) -> dict:
    if class_id <= 0 or subject_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس یا درس معتبر نیست.")
    if weekday not in range(7):
        raise InvalidApplicationInput("روز هفته معتبر نیست.")
    if period not in range(1, 17):
        raise InvalidApplicationInput("شماره زنگ باید بین ۱ تا ۱۶ باشد.")

    start_value = None
    end_value = None
    if bool(starts_at) != bool(ends_at):
        raise InvalidApplicationInput("زمان شروع و پایان باید هر دو وارد شوند یا هر دو خالی باشند.")
    if starts_at and ends_at:
        try:
            start_value = time.fromisoformat(starts_at)
            end_value = time.fromisoformat(ends_at)
        except ValueError as exc:
            raise InvalidApplicationInput("زمان باید با قالب HH:MM وارد شود.") from exc
        if end_value <= start_value:
            raise InvalidApplicationInput("زمان پایان باید بعد از زمان شروع باشد.")

    normalized_room = " ".join(room.split()) if room else None
    if normalized_room is not None and len(normalized_room) > 80:
        raise InvalidApplicationInput("نام کلاس/اتاق حداکثر ۸۰ نویسه است.")

    class_result = await get_product_class(db, class_id=class_id)
    if class_result is None or not class_result["is_active"]:
        raise ResourceNotFound("کلاس فعال پیدا نشد.")

    subject_result = await db.execute(
        text(
            """
            SELECT s.id
            FROM public.subjects AS s
            JOIN public.class_subjects AS cs ON cs.subject_id = s.id
            WHERE s.id = :subject_id
              AND s.school_id = :school_id
              AND cs.class_id = :class_id
            """
        ),
        {
            "subject_id": subject_id,
            "school_id": class_result["school_id"],
            "class_id": class_id,
        },
    )
    if subject_result.scalar_one_or_none() is None:
        raise ResourceNotFound("این درس به کلاس تخصیص داده نشده است.")

    result = await db.execute(
        text(
            """
            INSERT INTO public.timetable_entries
                (class_id, subject_id, weekday, period, starts_at, ends_at, room)
            VALUES
                (:class_id, :subject_id, :weekday, :period, :starts_at, :ends_at, :room)
            ON CONFLICT (class_id, weekday, period) DO NOTHING
            RETURNING id, weekday, period
            """
        ),
        {
            "class_id": class_id,
            "subject_id": subject_id,
            "weekday": weekday,
            "period": period,
            "starts_at": start_value,
            "ends_at": end_value,
            "room": normalized_room,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceConflict("برای این کلاس، در این روز و زنگ قبلاً برنامه ثبت شده است.")
    return dict(row)
