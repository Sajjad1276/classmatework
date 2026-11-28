from __future__ import annotations

import hashlib
import html
import secrets
import unicodedata
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import InvalidApplicationInput, ResourceConflict, ResourceNotFound


async def list_product_schools(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("تعداد یا شماره صفحه مدارس معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT
                s.id,
                s.name,
                s.timezone,
                s.is_active,
                COUNT(DISTINCT c.id) AS class_count
            FROM public.schools AS s
            LEFT JOIN public.academic_years AS ay ON ay.school_id = s.id
            LEFT JOIN public.classes AS c ON c.academic_year_id = ay.id
            GROUP BY s.id, s.name, s.timezone, s.is_active
            ORDER BY s.is_active DESC, s.name ASC, s.id ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
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
                s.timezone,
                s.is_active,
                COUNT(DISTINCT c.id) AS class_count
            FROM public.schools AS s
            LEFT JOIN public.academic_years AS ay ON ay.school_id = s.id
            LEFT JOIN public.classes AS c ON c.academic_year_id = ay.id
            WHERE s.id = :school_id
            GROUP BY s.id, s.name, s.timezone, s.is_active
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
    offset: int = 0,
) -> list[dict]:
    if school_id <= 0:
        raise InvalidApplicationInput("شناسه مدرسه معتبر نیست.")
    if not 1 <= limit <= 200 or offset < 0:
        raise InvalidApplicationInput("تعداد یا شماره صفحه کلاس‌ها معتبر نیست.")

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
                c.group_chat_title,
                (c.member_join_code_hash IS NOT NULL) AS has_member_join_code
            FROM public.classes AS c
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            WHERE ay.school_id = :school_id
            ORDER BY c.is_active DESC, c.grade ASC, c.name ASC, c.id ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"school_id": school_id, "limit": limit, "offset": offset},
    )
    return [dict(row) for row in result.mappings().all()]


async def list_product_class_shortcut_candidates(db: AsyncSession) -> list[dict]:
    """Return active classes with their school labels for owner shortcuts."""
    result = await db.execute(
        text(
            """
            SELECT
                c.id,
                c.name,
                c.grade,
                c.section,
                s.name AS school_name,
                ay.name AS academic_year_name,
                ay.is_current AS is_current_academic_year
            FROM public.classes AS c
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.is_active IS TRUE
              AND s.is_active IS TRUE
            ORDER BY s.name ASC, c.grade ASC, c.name ASC, c.id ASC
            """
        )
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

    join_code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))
    join_code_hash = hashlib.sha256(join_code.encode("utf-8")).hexdigest()
    result = await db.execute(
        text(
            """
            INSERT INTO public.classes
                (
                    academic_year_id,
                    name,
                    grade,
                    section,
                    is_active,
                    member_join_code_hash,
                    member_join_code_expires_at,
                    member_join_code_token
                )
            VALUES
                (
                    :academic_year_id,
                    :name,
                    :grade,
                    :section,
                    TRUE,
                    :member_join_code_hash,
                    NULL,
                    :member_join_code_token
                )
            RETURNING id, name, grade, section, is_active
            """
        ),
        {
            "academic_year_id": academic_year_id,
            "name": normalized_name,
            "grade": normalized_grade,
            "section": normalized_section,
            "member_join_code_hash": join_code_hash,
            "member_join_code_token": join_code,
        },
    )
    school_class = dict(result.mappings().one())
    school_class["member_join_code"] = join_code
    school_class["member_join_code_token"] = join_code
    school_class["has_member_join_code"] = True
    school_class["member_join_code_expires_at"] = None
    return school_class



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
                s.name AS school_name,
                c.group_chat_id,
                c.group_chat_title,
                (c.member_join_code_hash IS NOT NULL) AS has_member_join_code,
                c.member_join_code_hash,
                c.member_join_code_expires_at,
                c.member_join_code_token,
                c.member_invite_reminder_next_at,
                c.member_invite_reminder_last_sent_at
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
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    if class_id <= 0 or not 1 <= limit <= 200 or offset < 0:
        raise InvalidApplicationInput("درخواست فهرست درس‌های کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT s.id, s.name
            FROM public.class_subjects AS cs
            JOIN public.subjects AS s ON s.id = cs.subject_id
            WHERE cs.class_id = :class_id
            ORDER BY s.name ASC, s.id ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"class_id": class_id, "limit": limit, "offset": offset},
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



async def list_product_class_members(
    db: AsyncSession,
    *,
    class_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    if class_id <= 0 or not 1 <= limit <= 200 or offset < 0:
        raise InvalidApplicationInput("درخواست فهرست اعضا معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT
                u.id AS user_id,
                u.telegram_id,
                u.full_name,
                u.username,
                u.is_active AS user_is_active,
                cm.is_active AS membership_is_active,
                cm.role AS membership_role
            FROM public.class_memberships AS cm
            JOIN public.users AS u ON u.id = cm.user_id
            WHERE cm.class_id = :class_id
            ORDER BY cm.is_active DESC, u.full_name ASC, u.id ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"class_id": class_id, "limit": limit, "offset": offset},
    )
    return [dict(row) for row in result.mappings().all()]


async def list_product_started_users(
    db: AsyncSession,
    *,
    limit: int = 9,
    offset: int = 0,
) -> list[dict]:
    """List every user who has started the student bot, regardless of class membership."""
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("درخواست فهرست کاربران ربات معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT
                u.id AS user_id,
                u.telegram_id,
                u.full_name,
                u.username,
                u.is_active AS user_is_active,
                u.private_chat_started,
                u.first_private_started_at
            FROM public.users AS u
            WHERE u.first_private_started_at IS NOT NULL
               OR u.private_chat_started IS TRUE
            ORDER BY u.first_private_started_at DESC NULLS LAST, u.id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    )
    return [dict(row) for row in result.mappings().all()]


async def add_product_class_member(
    db: AsyncSession,
    *,
    class_id: int,
    telegram_id: int,
    full_name: str,
) -> dict:
    normalized_name = " ".join(full_name.split())
    if class_id <= 0 or not 0 < telegram_id <= 9_223_372_036_854_775_807:
        raise InvalidApplicationInput("شناسه کلاس یا Telegram ID معتبر نیست.")
    if not 1 <= len(normalized_name) <= 160:
        raise InvalidApplicationInput("نام عضو باید بین ۱ تا ۱۶۰ نویسه باشد.")

    class_info = await get_product_class(db, class_id=class_id)
    if class_info is None or not class_info["is_active"]:
        raise ResourceNotFound("کلاس فعال پیدا نشد.")

    user_result = await db.execute(
        text(
            """
            INSERT INTO public.users
                (telegram_id, full_name, is_platform_admin, private_chat_started, is_active)
            VALUES
                (:telegram_id, :full_name, FALSE, FALSE, TRUE)
            ON CONFLICT (telegram_id) DO NOTHING
            RETURNING id, full_name, is_active
            """
        ),
        {"telegram_id": telegram_id, "full_name": normalized_name},
    )
    user = user_result.mappings().one_or_none()
    if user is None:
        existing = await db.execute(
            text(
                """
                SELECT id, full_name, is_active
                FROM public.users
                WHERE telegram_id = :telegram_id
                """
            ),
            {"telegram_id": telegram_id},
        )
        user = existing.mappings().one()
    if not user["is_active"]:
        raise ResourceConflict("حساب کاربری در ClassMate غیرفعال است و به کلاس اضافه نشد.")

    membership = await db.execute(
        text(
            """
            INSERT INTO public.class_memberships (class_id, user_id, role, is_active)
            VALUES (:class_id, :user_id, 'student', TRUE)
            ON CONFLICT (class_id, user_id)
            DO UPDATE SET is_active = TRUE
            RETURNING id, role
            """
        ),
        {"class_id": class_id, "user_id": user["id"]},
    )
    membership_row = membership.mappings().one()
    membership_role = str(membership_row["role"])
    if membership_role != "student":
        raise ResourceConflict(
            "این حساب در کلاس نقش مدیریتی دارد؛ کد دانش‌آموز برای آن صادر نمی‌شود."
        )
    return {
        "user_id": user["id"],
        "telegram_id": telegram_id,
        "full_name": user["full_name"],
        "membership_id": membership_row["id"],
        "membership_role": membership_role,
        "school_id": class_info["school_id"],
    }


async def set_product_class_member_active(
    db: AsyncSession,
    *,
    class_id: int,
    user_id: int,
    is_active: bool,
) -> bool:
    if class_id <= 0 or user_id <= 0:
        raise InvalidApplicationInput("شناسه عضو یا کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            UPDATE public.class_memberships
            SET is_active = :is_active
            WHERE class_id = :class_id AND user_id = :user_id
            RETURNING id
            """
        ),
        {"class_id": class_id, "user_id": user_id, "is_active": is_active},
    )
    changed = result.scalar_one_or_none() is not None
    if changed and not is_active:
        await db.execute(
            text(
                """
                UPDATE public.class_member_invite_codes
                SET used_at = COALESCE(used_at, NOW())
                WHERE class_id = :class_id AND user_id = :user_id
                """
            ),
            {"class_id": class_id, "user_id": user_id},
        )
    return changed


async def list_product_class_announcements(
    db: AsyncSession,
    *,
    class_id: int,
    limit: int = 30,
    offset: int = 0,
) -> list[dict]:
    if class_id <= 0 or not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("درخواست فهرست اطلاعیه‌ها معتبر نیست.")
    result = await db.execute(
        text(
            """
            SELECT id, title, body, published_at, expires_at, is_active
            FROM public.announcements
            WHERE class_id = :class_id
            ORDER BY published_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"class_id": class_id, "limit": limit, "offset": offset},
    )
    return [dict(row) for row in result.mappings().all()]


async def create_product_announcement(
    db: AsyncSession,
    *,
    class_id: int,
    telegram_id: int,
    author_name: str,
    title: str,
    body: str,
) -> dict:
    normalized_title = " ".join(title.split())
    normalized_body = body.strip()
    if class_id <= 0 or telegram_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس یا مدیر معتبر نیست.")
    if not 2 <= len(normalized_title) <= 180:
        raise InvalidApplicationInput("عنوان اطلاعیه باید بین ۲ تا ۱۸۰ نویسه باشد.")
    if not 2 <= len(normalized_body) <= 8000:
        raise InvalidApplicationInput("متن اطلاعیه باید بین ۲ تا ۸۰۰۰ نویسه باشد.")

    class_info = await get_product_class(db, class_id=class_id)
    if class_info is None or not class_info["is_active"]:
        raise ResourceNotFound("کلاس فعال پیدا نشد.")

    author = await db.execute(
        text(
            """
            INSERT INTO public.users
                (telegram_id, full_name, is_platform_admin, private_chat_started, is_active)
            VALUES
                (:telegram_id, :full_name, FALSE, FALSE, TRUE)
            ON CONFLICT (telegram_id) DO NOTHING
            RETURNING id, is_active
            """
        ),
        {"telegram_id": telegram_id, "full_name": author_name[:160] or "مدیر ClassMate"},
    )
    author_row = author.mappings().one_or_none()
    if author_row is None:
        existing = await db.execute(
            text("SELECT id, is_active FROM public.users WHERE telegram_id = :telegram_id"),
            {"telegram_id": telegram_id},
        )
        author_row = existing.mappings().one()
    if not author_row["is_active"]:
        raise ResourceConflict("حساب مدیر در دیتابیس ClassMate غیرفعال است.")

    result = await db.execute(
        text(
            """
            INSERT INTO public.announcements
                (class_id, title, body, created_by, is_active)
            VALUES
                (:class_id, :title, :body, :created_by, TRUE)
            RETURNING id, title, published_at, is_active
            """
        ),
        {
            "class_id": class_id,
            "title": normalized_title,
            "body": normalized_body,
            "created_by": author_row["id"],
        },
    )
    row = result.mappings().one()
    return dict(row)


async def set_product_announcement_active(
    db: AsyncSession,
    *,
    class_id: int,
    announcement_id: int,
    is_active: bool,
) -> bool:
    if class_id <= 0 or announcement_id <= 0:
        raise InvalidApplicationInput("شناسه اطلاعیه یا کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            UPDATE public.announcements
            SET is_active = :is_active
            WHERE id = :announcement_id AND class_id = :class_id
            RETURNING id
            """
        ),
        {
            "class_id": class_id,
            "announcement_id": announcement_id,
            "is_active": is_active,
        },
    )
    return result.scalar_one_or_none() is not None



async def update_product_school(
    db: AsyncSession,
    *,
    school_id: int,
    name: str,
) -> dict:
    normalized_name = " ".join(name.split())
    if school_id <= 0:
        raise InvalidApplicationInput("شناسه مدرسه معتبر نیست.")
    if not 2 <= len(normalized_name) <= 180:
        raise InvalidApplicationInput("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.")

    result = await db.execute(
        text(
            """
            UPDATE public.schools
            SET name = :name
            WHERE id = :school_id
            RETURNING id, name, timezone, is_active
            """
        ),
        {"school_id": school_id, "name": normalized_name},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceNotFound("مدرسه موردنظر پیدا نشد.")
    return dict(row)


async def set_product_school_active(
    db: AsyncSession,
    *,
    school_id: int,
    is_active: bool,
) -> dict:
    if school_id <= 0:
        raise InvalidApplicationInput("شناسه مدرسه معتبر نیست.")
    result = await db.execute(
        text(
            """
            UPDATE public.schools
            SET is_active = :is_active
            WHERE id = :school_id
            RETURNING id, name, timezone, is_active
            """
        ),
        {"school_id": school_id, "is_active": is_active},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceNotFound("مدرسه موردنظر پیدا نشد.")
    return dict(row)


async def update_product_class(
    db: AsyncSession,
    *,
    class_id: int,
    name: str,
    grade: str,
    section: str | None = None,
) -> dict:
    normalized_name = " ".join(name.split())
    normalized_grade = " ".join(grade.split())
    normalized_section = " ".join(section.split()) if section else None
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    if not 2 <= len(normalized_name) <= 80:
        raise InvalidApplicationInput("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
    if not 1 <= len(normalized_grade) <= 32:
        raise InvalidApplicationInput("پایه باید بین ۱ تا ۳۲ نویسه باشد.")
    if normalized_section is not None and len(normalized_section) > 32:
        raise InvalidApplicationInput("عنوان بخش کلاس حداکثر ۳۲ نویسه است.")

    result = await db.execute(
        text(
            """
            UPDATE public.classes
            SET name = :name, grade = :grade, section = :section
            WHERE id = :class_id
            RETURNING id, name, grade, section, is_active
            """
        ),
        {
            "class_id": class_id,
            "name": normalized_name,
            "grade": normalized_grade,
            "section": normalized_section,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceNotFound("کلاس موردنظر پیدا نشد.")
    return dict(row)


async def set_product_class_active(
    db: AsyncSession,
    *,
    class_id: int,
    is_active: bool,
) -> dict:
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    result = await db.execute(
        text(
            """
            UPDATE public.classes AS c
            SET is_active = :is_active
            FROM public.academic_years AS ay
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.id = :class_id
              AND c.academic_year_id = ay.id
              AND (:is_active IS FALSE OR s.is_active IS TRUE)
            RETURNING c.id, c.name, c.grade, c.section, c.is_active, ay.school_id
            """
        ),
        {"class_id": class_id, "is_active": is_active},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceNotFound(
            "کلاس پیدا نشد یا مدرسه غیرفعال است؛ ابتدا وضعیت مدرسه را بررسی کنید."
        )
    return dict(row)


async def set_product_class_member_role(
    db: AsyncSession,
    *,
    class_id: int,
    user_id: int,
    role: str,
) -> bool:
    allowed_roles = {"student", "class_manager", "trusted_approver"}
    if class_id <= 0 or user_id <= 0:
        raise InvalidApplicationInput("شناسه عضو یا کلاس معتبر نیست.")
    if role not in allowed_roles:
        raise InvalidApplicationInput("نقش انتخاب‌شده برای عضو کلاس مجاز نیست.")
    result = await db.execute(
        text(
            """
            UPDATE public.class_memberships AS cm
            SET role = :role
            FROM public.users AS u
            WHERE cm.class_id = :class_id
              AND cm.user_id = :user_id
              AND cm.user_id = u.id
              AND cm.is_active IS TRUE
              AND u.is_active IS TRUE
            RETURNING cm.id
            """
        ),
        {"class_id": class_id, "user_id": user_id, "role": role},
    )
    changed = result.scalar_one_or_none() is not None
    if changed:
        await db.execute(
            text(
                """
                UPDATE public.class_member_invite_codes
                SET used_at = COALESCE(used_at, NOW())
                WHERE class_id = :class_id AND user_id = :user_id
                """
            ),
            {"class_id": class_id, "user_id": user_id},
        )
    return changed


async def issue_product_group_link_code(
    db: AsyncSession,
    *,
    class_id: int,
    code: str,
    ttl_minutes: int = 15,
) -> dict:
    normalized_code = code.strip().upper()
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    if not 16 <= len(normalized_code) <= 64 or any(ch.isspace() for ch in normalized_code):
        raise InvalidApplicationInput("کد اتصال باید بین ۱۶ تا ۶۴ نویسه و بدون فاصله باشد.")
    if not 1 <= ttl_minutes <= 60:
        raise InvalidApplicationInput("مدت اعتبار کد اتصال باید بین ۱ تا ۶۰ دقیقه باشد.")

    class_info = await get_product_class(db, class_id=class_id)
    if class_info is None or not class_info["is_active"]:
        raise ResourceNotFound("کلاس فعال پیدا نشد.")
    code_hash = hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    result = await db.execute(
        text(
            """
            UPDATE public.classes AS c
            SET group_link_code_hash = :code_hash,
                group_link_code_expires_at = :expires_at
            FROM public.academic_years AS ay
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.id = :class_id
              AND c.academic_year_id = ay.id
              AND c.is_active IS TRUE
              AND s.is_active IS TRUE
            RETURNING c.id, c.name, c.group_chat_id, c.group_chat_title,
                      c.group_link_code_expires_at
            """
        ),
        {
            "class_id": class_id,
            "code_hash": code_hash,
            "expires_at": expires_at,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceConflict("مدرسه یا کلاس غیرفعال است؛ اتصال گروه انجام نشد.")
    return dict(row)



async def list_product_class_records(
    db: AsyncSession,
    *,
    class_id: int,
    status: str = "PENDING",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    allowed_statuses = {
        "PENDING",
        "REVISION_REQUESTED",
        "APPROVED",
        "REJECTED",
        "CANCELLED",
        "EXPIRED",
    }
    if class_id <= 0 or status not in allowed_statuses or not 1 <= limit <= 100 or offset < 0:
        raise InvalidApplicationInput("درخواست فهرست محتوای آموزشی معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT *
            FROM (
                SELECT
                    'homework' AS entity_type,
                    h.id,
                    h.title,
                    h.description AS body,
                    COALESCE(h.due_on::text, 'بدون مهلت') AS schedule_label,
                    h.status,
                    h.created_at,
                    s.name AS subject_name,
                    c.name AS class_name
                FROM public.homeworks AS h
                JOIN public.classes AS c ON c.id = h.class_id
                JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
                JOIN public.schools AS sc ON sc.id = ay.school_id
                JOIN public.subjects AS s ON s.id = h.subject_id
                WHERE h.class_id = :class_id
                  AND c.is_active IS TRUE
                  AND sc.is_active IS TRUE
                  AND h.status = :status

                UNION ALL

                SELECT
                    'exam' AS entity_type,
                    e.id,
                    e.title,
                    e.scope AS body,
                    e.scheduled_at::text AS schedule_label,
                    e.status,
                    e.created_at,
                    s.name AS subject_name,
                    c.name AS class_name
                FROM public.exams AS e
                JOIN public.classes AS c ON c.id = e.class_id
                JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
                JOIN public.schools AS sc ON sc.id = ay.school_id
                JOIN public.subjects AS s ON s.id = e.subject_id
                WHERE e.class_id = :class_id
                  AND c.is_active IS TRUE
                  AND sc.is_active IS TRUE
                  AND e.status = :status

                UNION ALL

                SELECT
                    'report' AS entity_type,
                    r.id,
                    COALESCE(r.title, 'گزارش روزانه') AS title,
                    r.body,
                    r.report_date::text AS schedule_label,
                    r.status,
                    r.created_at,
                    '—' AS subject_name,
                    c.name AS class_name
                FROM public.daily_reports AS r
                JOIN public.classes AS c ON c.id = r.class_id
                JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
                JOIN public.schools AS sc ON sc.id = ay.school_id
                WHERE r.class_id = :class_id
                  AND c.is_active IS TRUE
                  AND sc.is_active IS TRUE
                  AND r.status = :status
            ) AS records
            ORDER BY created_at DESC, entity_type ASC, id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"class_id": class_id, "status": status, "limit": limit, "offset": offset},
    )
    return [dict(row) for row in result.mappings().all()]


async def get_product_class_record(
    db: AsyncSession,
    *,
    class_id: int,
    entity_type: str,
    record_id: int,
) -> dict | None:
    tables = {
        "homework": ("homeworks", "description", "due_on::text"),
        "exam": ("exams", "scope", "scheduled_at::text"),
        "report": ("daily_reports", "body", "report_date::text"),
    }
    if class_id <= 0 or record_id <= 0 or entity_type not in tables:
        raise InvalidApplicationInput("شناسه یا نوع محتوای آموزشی معتبر نیست.")
    table, body_column, schedule_expression = tables[entity_type]
    title_expression = (
        "COALESCE(r.title, 'گزارش روزانه')" if entity_type == "report" else "r.title"
    )
    subject_expression = (
        "'—'::text" if entity_type == "report" else "s.name"
    )
    subject_join = (
        "" if entity_type == "report" else "JOIN public.subjects AS s ON s.id = r.subject_id"
    )
    result = await db.execute(
        text(
            f"""
            SELECT
                r.id,
                :entity_type AS entity_type,
                {title_expression} AS title,
                r.{body_column} AS body,
                {schedule_expression} AS schedule_label,
                r.status,
                r.created_at,
                {subject_expression} AS subject_name,
                c.id AS class_id,
                c.name AS class_name,
                c.group_chat_id
            FROM public.{table} AS r
            JOIN public.classes AS c ON c.id = r.class_id
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            JOIN public.schools AS sc ON sc.id = ay.school_id
            {subject_join}
            WHERE r.id = :record_id
              AND r.class_id = :class_id
              AND c.is_active IS TRUE
              AND sc.is_active IS TRUE
            """
        ),
        {
            "entity_type": entity_type,
            "record_id": record_id,
            "class_id": class_id,
        },
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def review_product_class_record(
    db: AsyncSession,
    *,
    class_id: int,
    entity_type: str,
    record_id: int,
    decision: str,
    reviewer_telegram_id: int,
    reviewer_name: str,
    note: str | None = None,
) -> dict:
    tables = {
        "homework": ("homeworks", "تکلیف"),
        "exam": ("exams", "آزمون"),
        "report": ("daily_reports", "گزارش روزانه"),
    }
    if class_id <= 0 or record_id <= 0 or entity_type not in tables:
        raise InvalidApplicationInput("شناسه یا نوع محتوای آموزشی معتبر نیست.")
    if decision not in {"approve", "reject"}:
        raise InvalidApplicationInput("تصمیم بررسی معتبر نیست.")
    if reviewer_telegram_id <= 0:
        raise InvalidApplicationInput("شناسه مدیر بررسی‌کننده معتبر نیست.")

    current = await get_product_class_record(
        db,
        class_id=class_id,
        entity_type=entity_type,
        record_id=record_id,
    )
    if current is None:
        raise ResourceNotFound("محتوای آموزشی موردنظر پیدا نشد.")
    if current["status"] not in {"PENDING", "REVISION_REQUESTED"}:
        raise ResourceConflict("این محتوا قبلاً بررسی شده و دیگر در صف انتظار نیست.")

    reviewer_result = await db.execute(
        text(
            """
            INSERT INTO public.users
                (telegram_id, full_name, is_platform_admin, private_chat_started, is_active)
            VALUES
                (:telegram_id, :full_name, FALSE, FALSE, TRUE)
            ON CONFLICT (telegram_id) DO NOTHING
            RETURNING id, is_active
            """
        ),
        {
            "telegram_id": reviewer_telegram_id,
            "full_name": " ".join(reviewer_name.split())[:160] or "مدیر ClassMate Work",
        },
    )
    reviewer = reviewer_result.mappings().one_or_none()
    if reviewer is None:
        existing = await db.execute(
            text(
                "SELECT id, is_active FROM public.users WHERE telegram_id = :telegram_id"
            ),
            {"telegram_id": reviewer_telegram_id},
        )
        reviewer = existing.mappings().one()
    if not reviewer["is_active"]:
        raise ResourceConflict("حساب مدیر بررسی‌کننده در ClassMate غیرفعال است.")

    table, entity_label = tables[entity_type]
    next_status = "APPROVED" if decision == "approve" else "REJECTED"
    now = datetime.now(timezone.utc)
    update_result = await db.execute(
        text(
            f"""
            UPDATE public.{table}
            SET status = :status,
                approved_by = CASE WHEN :status = 'APPROVED' THEN :reviewer_id ELSE approved_by END,
                approved_at = CASE WHEN :status = 'APPROVED' THEN :approved_at ELSE approved_at END
            WHERE id = :record_id
              AND class_id = :class_id
              AND status IN ('PENDING', 'REVISION_REQUESTED')
            RETURNING id, status
            """
        ),
        {
            "status": next_status,
            "reviewer_id": reviewer["id"],
            "approved_at": now,
            "record_id": record_id,
            "class_id": class_id,
        },
    )
    updated = update_result.mappings().one_or_none()
    if updated is None:
        raise ResourceConflict("وضعیت محتوا هم‌زمان تغییر کرده است؛ فهرست را تازه‌سازی کنید.")

    event_result = await db.execute(
        text(
            """
            INSERT INTO public.approval_events
                (entity_type, entity_id, actor_id, action, note)
            VALUES
                (:entity_type, :entity_id, :actor_id, :action, :note)
            RETURNING id
            """
        ),
        {
            "entity_type": entity_type,
            "entity_id": record_id,
            "actor_id": reviewer["id"],
            "action": decision,
            "note": note.strip()[:2000] if note and note.strip() else None,
        },
    )
    approval_event_id = event_result.scalar_one()

    published_to_group = False
    if decision == "approve" and current.get("group_chat_id") is not None:
        safe_title = html.escape(str(current["title"]))
        safe_subject = html.escape(str(current["subject_name"]))
        safe_class = html.escape(str(current["class_name"]))
        safe_schedule = html.escape(str(current["schedule_label"]))
        safe_body = html.escape(str(current["body"] or ""))[:2600]
        message_text = (
            f"📌 <b>{entity_label} تأیید شد</b>\n"
            f"کلاس: {safe_class}\n"
            f"عنوان: {safe_title}\n"
            f"درس: {safe_subject}\n"
            f"زمان/مهلت: {safe_schedule}\n\n"
            f"{safe_body}"
        )[:4096]
        await db.execute(
            text(
                """
                INSERT INTO public.telegram_outbox
                    (target_type, target_chat_id, class_id, entity_type, entity_id,
                     expected_record_status, recipient_user_id, text, parse_mode,
                     disable_notification, action_type, action_id, scheduled_at,
                     status, attempts, idempotency_key)
                VALUES
                    ('group', :chat_id, :class_id, :entity_type, :entity_id,
                     'APPROVED', NULL, :message, 'HTML', FALSE, NULL, NULL, :scheduled_at,
                     'pending', 0, :idempotency_key)
                """
            ),
            {
                "chat_id": current["group_chat_id"],
                "class_id": class_id,
                "entity_type": entity_type,
                "entity_id": record_id,
                "message": message_text,
                "scheduled_at": now,
                "idempotency_key": (
                    f"work-review:{entity_type}:{record_id}:approved:{uuid.uuid4().hex}"
                ),
            },
        )
        published_to_group = True

    return {
        **current,
        "status": next_status,
        "approval_event_id": approval_event_id,
        "published_to_group": published_to_group,
    }



async def issue_product_member_invite_code(
    db: AsyncSession,
    *,
    class_id: int,
    user_id: int,
    code: str,
    ttl_hours: int = 24,
) -> dict:
    normalized_code = code.strip().upper()
    if class_id <= 0 or user_id <= 0:
        raise InvalidApplicationInput("شناسه دانش‌آموز یا کلاس معتبر نیست.")
    if not 16 <= len(normalized_code) <= 64 or any(ch.isspace() for ch in normalized_code):
        raise InvalidApplicationInput("کد اختصاصی باید بین ۱۶ تا ۶۴ نویسه و بدون فاصله باشد.")
    if not 1 <= ttl_hours <= 168:
        raise InvalidApplicationInput("اعتبار کد عضویت باید بین ۱ تا ۱۶۸ ساعت باشد.")

    member_result = await db.execute(
        text(
            """
            SELECT
                cm.id,
                cm.role,
                cm.is_active AS membership_is_active,
                u.telegram_id,
                u.full_name,
                u.is_active AS user_is_active,
                c.name AS class_name
            FROM public.class_memberships AS cm
            JOIN public.users AS u ON u.id = cm.user_id
            JOIN public.classes AS c ON c.id = cm.class_id
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE cm.class_id = :class_id
              AND cm.user_id = :user_id
              AND c.is_active IS TRUE
              AND s.is_active IS TRUE
            """
        ),
        {"class_id": class_id, "user_id": user_id},
    )
    member = member_result.mappings().one_or_none()
    if member is None:
        raise ResourceNotFound("عضو این کلاس پیدا نشد.")
    if member["role"] != "student":
        raise ResourceConflict("کد اختصاصی فقط برای نقش دانش‌آموز صادر می‌شود.")
    if not member["membership_is_active"] or not member["user_is_active"]:
        raise ResourceConflict("عضویت یا حساب دانش‌آموز غیرفعال است.")

    code_hash = hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    invite_result = await db.execute(
        text(
            """
            INSERT INTO public.class_member_invite_codes
                (class_id, user_id, code_hash, expires_at, used_at)
            VALUES
                (:class_id, :user_id, :code_hash, :expires_at, NULL)
            ON CONFLICT (class_id, user_id)
            DO UPDATE SET
                code_hash = EXCLUDED.code_hash,
                expires_at = EXCLUDED.expires_at,
                used_at = NULL,
                created_at = NOW()
            RETURNING id, expires_at
            """
        ),
        {
            "class_id": class_id,
            "user_id": user_id,
            "code_hash": code_hash,
            "expires_at": expires_at,
        },
    )
    invite = invite_result.mappings().one()
    return {
        **dict(member),
        "invitation_id": invite["id"],
        "expires_at": invite["expires_at"],
    }



async def issue_product_class_join_code(
    db: AsyncSession,
    *,
    class_id: int,
    code: str,
) -> dict:
    normalized_code = code.strip().upper()
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    if not 8 <= len(normalized_code) <= 64 or any(ch.isspace() for ch in normalized_code):
        raise InvalidApplicationInput("کد ورود کلاس باید بین ۸ تا ۶۴ نویسه و بدون فاصله باشد.")

    code_hash = hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()
    result = await db.execute(
        text(
            """
            UPDATE public.classes AS c
            SET member_join_code_hash = :code_hash,
                member_join_code_token = :normalized_code,
                member_join_code_expires_at = NULL,
                member_invite_reminder_next_at = CASE
                    WHEN c.group_chat_id IS NOT NULL THEN NOW()
                    ELSE c.member_invite_reminder_next_at
                END
            FROM public.academic_years AS ay
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.id = :class_id
              AND c.academic_year_id = ay.id
              AND c.is_active IS TRUE
              AND s.is_active IS TRUE
            RETURNING c.id, c.name, c.member_join_code_expires_at,
                      c.member_join_code_token, c.member_invite_reminder_next_at
            """
        ),
        {
            "class_id": class_id,
            "code_hash": code_hash,
            "normalized_code": normalized_code,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceNotFound("کلاس یا مدرسهٔ فعال پیدا نشد.")
    return {
        **dict(row),
        "member_join_code": normalized_code,
        "has_member_join_code": True,
    }



async def sync_product_class_join_code_token(
    db: AsyncSession,
    *,
    class_id: int,
    code: str,
) -> dict:
    """Backfill the reusable raw deep-link token without changing its hash."""
    normalized_code = code.strip().upper()
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    if not 8 <= len(normalized_code) <= 64 or any(ch.isspace() for ch in normalized_code):
        raise InvalidApplicationInput("کد ورود کلاس معتبر نیست.")

    code_hash = hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()
    result = await db.execute(
        text(
            """
            UPDATE public.classes AS c
            SET member_join_code_token = :normalized_code,
                member_invite_reminder_next_at = CASE
                    WHEN c.group_chat_id IS NOT NULL THEN NOW()
                    ELSE c.member_invite_reminder_next_at
                END
            WHERE c.id = :class_id
              AND c.member_join_code_hash = :code_hash
            RETURNING c.id, c.name, c.member_join_code_token,
                      c.member_invite_reminder_next_at
            """
        ),
        {
            "class_id": class_id,
            "code_hash": code_hash,
            "normalized_code": normalized_code,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ResourceConflict("کد ذخیره‌شده با کد فعال کلاس یکسان نیست.")
    return dict(row)


async def count_missing_product_class_join_codes(db: AsyncSession) -> int:
    result = await db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM public.classes AS c
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.member_join_code_hash IS NULL
              AND c.is_active IS TRUE
              AND s.is_active IS TRUE
            """
        )
    )
    return int(result.scalar_one() or 0)


async def issue_missing_product_class_join_codes(
    db: AsyncSession,
    *,
    limit: int = 2000,
) -> list[dict]:
    if not 1 <= limit <= 5000:
        raise InvalidApplicationInput("تعداد کلاس‌های درخواستی برای صدور کد معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT
                c.id AS class_id,
                c.name AS class_name,
                s.id AS school_id,
                s.name AS school_name
            FROM public.classes AS c
            JOIN public.academic_years AS ay ON ay.id = c.academic_year_id
            JOIN public.schools AS s ON s.id = ay.school_id
            WHERE c.member_join_code_hash IS NULL
              AND c.is_active IS TRUE
              AND s.is_active IS TRUE
            ORDER BY s.name ASC, c.name ASC, c.id ASC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    candidates = [dict(row) for row in result.mappings().all()]
    issued: list[dict] = []
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    for item in candidates:
        raw_code = "".join(secrets.choice(alphabet) for _ in range(10))
        code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        stored = await db.execute(
            text(
                """
                UPDATE public.classes AS c
                SET member_join_code_hash = :code_hash,
                    member_join_code_token = :raw_code,
                    member_join_code_expires_at = NULL,
                    member_invite_reminder_next_at = CASE
                        WHEN c.group_chat_id IS NOT NULL THEN NOW()
                        ELSE c.member_invite_reminder_next_at
                    END
                FROM public.academic_years AS ay
                JOIN public.schools AS s ON s.id = ay.school_id
                WHERE c.id = :class_id
                  AND c.academic_year_id = ay.id
                  AND c.member_join_code_hash IS NULL
                  AND c.is_active IS TRUE
                  AND s.is_active IS TRUE
                RETURNING c.id
                """
            ),
            {
                "class_id": item["class_id"],
                "code_hash": code_hash,
                "raw_code": raw_code,
            },
        )
        if stored.scalar_one_or_none() is None:
            # Another manager may have issued the code since the candidate query.
            # Never overwrite it.
            continue
        issued.append({**item, "join_code": raw_code})

    return issued
