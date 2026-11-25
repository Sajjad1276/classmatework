from __future__ import annotations

import logging
import secrets

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.errors import ApplicationError, ResourceConflict
from app.application.product_management import (
    add_product_class_member,
    create_and_assign_product_subject,
    create_product_announcement,
    create_product_class,
    create_product_school,
    create_product_timetable_entry,
    get_product_class,
    get_product_school,
    list_class_subjects,
    list_class_timetable,
    list_product_class_announcements,
    list_product_class_members,
    list_product_classes,
    list_product_schools,
    set_product_announcement_active,
    set_product_class_active,
    set_product_class_member_active,
    set_product_school_active,
    update_product_class,
    update_product_school,
)
from app.modules.access.policy import has_permission
from app.modules.audit.service import record_event
from app.modules.identity.principal import TelegramPrincipal

logger = logging.getLogger(__name__)
router = Router(name="product-management")

SCHOOLS_BUTTON = "🏫 مدیریت مدارس و کلاس‌ها"
CREATE_SCHOOL_BUTTON = "➕ ایجاد مدرسه"


class SchoolCreation(StatesGroup):
    name = State()
    confirm = State()


class SchoolEditing(StatesGroup):
    name = State()
    confirm = State()


class ClassCreation(StatesGroup):
    name = State()
    grade = State()
    section = State()
    confirm = State()


class ClassEditing(StatesGroup):
    name = State()
    grade = State()
    section = State()
    confirm = State()


class SubjectCreation(StatesGroup):
    name = State()


class TimetableCreation(StatesGroup):
    weekday = State()
    subject = State()
    period = State()
    time_range = State()
    room = State()
    confirm = State()


class MemberCreation(StatesGroup):
    telegram_id = State()
    full_name = State()
    confirm = State()


class AnnouncementCreation(StatesGroup):
    title = State()
    body = State()
    confirm = State()


async def _has_permission(
    db: AsyncSession,
    principal: TelegramPrincipal,
    permission_key: str,
) -> bool:
    return await has_permission(
        db,
        user_id=principal.user.id,
        permission_key=permission_key,
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )


def _school_directory_keyboard(
    schools: list[dict],
    *,
    can_create: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if school['is_active'] else '⚪'} {school['name'][:35]} · {school['class_count']} کلاس",
                callback_data=f"product:school:{school['id']}",
            )
        ]
        for school in schools
    ]
    if can_create:
        rows.append(
            [InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="product:school:create")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_school_directory(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:read"):
        await message.answer("مجوز مشاهدهٔ مدارس را ندارید.")
        return
    if product_session_factory is None:
        await message.answer(
            "اتصال دیتابیس اصلی ClassMate فعال نیست؛ فهرست مدارس در دسترس نیست."
        )
        return

    can_create = await _has_permission(db, principal, "platform:schools:create")
    try:
        async with product_session_factory() as product_db:
            schools = await list_product_schools(product_db)
    except Exception:
        logger.exception("Could not load ClassMate product schools.")
        await message.answer("دریافت فهرست مدارس از دیتابیس ClassMate ناموفق بود.")
        return

    body = (
        f"🏫 مدیریت مدارس ClassMate\n\nتعداد مدارس: {len(schools)}\n"
        "مدرسه را برای مشاهدهٔ کلاس‌ها انتخاب کنید."
        if schools
        else "هنوز مدرسه‌ای در ClassMate ثبت نشده است."
    )
    await message.answer(
        body,
        reply_markup=_school_directory_keyboard(schools, can_create=can_create),
    )


@router.message(F.text == SCHOOLS_BUTTON)
@router.message(Command("schools"))
async def schools_button_handler(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await _show_school_directory(
        message,
        principal=principal,
        db=db,
        product_session_factory=product_session_factory,
    )


@router.callback_query(F.data == "product:schools")
async def schools_refresh_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _show_school_directory(
            callback.message,
            principal=principal,
            db=db,
            product_session_factory=product_session_factory,
        )


async def _begin_school_creation(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:create"):
        await message.answer("مجوز ایجاد مدرسه را ندارید.")
        return
    await state.clear()
    token = secrets.token_urlsafe(8)
    await state.update_data(product_school_token=token)
    await state.set_state(SchoolCreation.name)
    await message.answer(
        "نام مدرسه را وارد کنید (۲ تا ۱۸۰ نویسه):\n\n"
        "برای لغو، /cancel را بفرستید."
    )


@router.message(F.text == CREATE_SCHOOL_BUTTON)
@router.message(Command("school_create"))
async def school_create_button_handler(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await _begin_school_creation(
        message,
        principal=principal,
        db=db,
        state=state,
    )


@router.callback_query(F.data == "product:school:create")
async def school_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _begin_school_creation(
            callback.message,
            principal=principal,
            db=db,
            state=state,
        )


@router.message(Command("cancel"), SchoolCreation.name)
@router.message(Command("cancel"), SchoolCreation.confirm)
@router.message(Command("cancel"), SchoolEditing.name)
@router.message(Command("cancel"), SchoolEditing.confirm)
async def school_creation_cancel_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer("ایجاد مدرسه لغو شد؛ هیچ تغییری ثبت نشد.")


@router.message(Command("cancel"), ClassCreation.name)
@router.message(Command("cancel"), ClassCreation.grade)
@router.message(Command("cancel"), ClassCreation.section)
@router.message(Command("cancel"), ClassCreation.confirm)
@router.message(Command("cancel"), ClassEditing.name)
@router.message(Command("cancel"), ClassEditing.grade)
@router.message(Command("cancel"), ClassEditing.section)
@router.message(Command("cancel"), ClassEditing.confirm)
async def class_creation_cancel_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer("ایجاد کلاس لغو شد؛ هیچ تغییری ثبت نشد.")


@router.message(SchoolCreation.name, F.text)
async def school_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 180:
        await message.answer("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد. دوباره وارد کنید.")
        return

    data = await state.get_data()
    token = data.get("product_school_token")
    if not token:
        await state.clear()
        await message.answer("فرم ایجاد مدرسه منقضی شده است؛ دوباره شروع کنید.")
        return

    await state.update_data(product_school_name=name)
    await state.set_state(SchoolCreation.confirm)
    await message.answer(
        f"پیش‌نمایش ایجاد مدرسه\n\nنام: {name}\nمنطقهٔ زمانی: Asia/Tehran\n\n"
        "ثبت نهایی شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ایجاد مدرسه",
                        callback_data=f"product:school:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:school:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:school:confirm:"))
async def school_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolCreation.confirm.state
        or not token
        or token != data.get("product_school_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:schools:create"):
        await state.clear()
        await callback.answer("مجوز ایجاد مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school = await create_product_school(
                    product_db,
                    name=data.get("product_school_name", ""),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create ClassMate product school.")
        await state.clear()
        await callback.answer("ایجاد مدرسه در دیتابیس ClassMate ناموفق بود.", show_alert=True)
        return

    audit_warning = False
    try:
        await record_event(
            db,
            event_type="product.school.created",
            actor_id=principal.user.id,
            aggregate_type="school",
            event_data={"school_id": school["id"], "name": school["name"]},
        )
        await db.commit()
    except Exception:
        audit_warning = True
        await db.rollback()
        logger.exception("School %s was created but Work audit recording failed.", school["id"])

    await state.clear()
    await callback.answer("مدرسه ایجاد شد.")
    if callback.message is not None:
        notice = (
            "⚠️ مدرسه ثبت شد، اما ثبت رویداد ممیزی نیازمند بررسی است. "
            if audit_warning
            else ""
        )
        await callback.message.edit_text(
            f"✅ {notice}مدرسه ایجاد شد.\n\nشناسه: {school['id']}\nنام: {school['name']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به فهرست مدارس",
                            callback_data="product:schools",
                        )
                    ],
                    [InlineKeyboardButton(text="خانه", callback_data="root:menu")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:school:cancel:"))
async def school_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolCreation.confirm.state
        or not token
        or token != data.get("product_school_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ایجاد مدرسه لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.regexp(r"^product:school:[0-9]+$"))
async def product_school_detail_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:read"):
        await callback.answer("مجوز مشاهدهٔ مدارس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            school = await get_product_school(product_db, school_id=school_id)
            if school is None:
                await callback.answer("مدرسه پیدا نشد.", show_alert=True)
                return
            classes = await list_product_classes(product_db, school_id=school_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate school %s.", callback.data)
        await callback.answer("دریافت جزئیات مدرسه ناموفق بود.", show_alert=True)
        return

    status = "فعال" if school["is_active"] else "غیرفعال"
    lines = [
        f"🏫 مدرسه: {school['name']}",
        f"شناسه: {school['id']}",
        f"وضعیت: {status}",
        f"منطقه زمانی: {school['timezone']}",
        f"تعداد کلاس‌ها: {school['class_count']}",
        "",
        "کلاس‌ها:",
    ]
    if classes:
        lines.append("برای ورود به مدیریت هر کلاس، دکمهٔ آن را انتخاب کنید.")
    else:
        lines.append("هنوز کلاسی برای این مدرسه ثبت نشده است.")

    rows = [
        [
            InlineKeyboardButton(
                text=f"👥 {item['name'][:24]} · {item['grade'][:12]}",
                callback_data=f"product:class:{item['id']}",
            )
        ]
        for item in classes[:30]
    ]
    can_create_class = (
        school["is_active"]
        and await _has_permission(db, principal, "platform:classes:create")
    )
    if can_create_class:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ افزودن کلاس",
                    callback_data=f"product:class:create:{school_id}",
                )
            ]
        )
    if await _has_permission(db, principal, "platform:schools:manage"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ ویرایش نام مدرسه",
                    callback_data=f"product:school:edit:{school_id}",
                ),
                InlineKeyboardButton(
                    text=("⛔ غیرفعال‌سازی مدرسه" if school["is_active"] else "♻️ فعال‌سازی مدرسه"),
                    callback_data=f"product:school:status:{school_id}:{0 if school['is_active'] else 1}",
                ),
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به مدارس",
                    callback_data="product:schools",
                )
            ],
            [InlineKeyboardButton(text="خانه", callback_data="root:menu")],
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


async def _begin_class_creation(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    school_id: int,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:create"):
        await message.answer("مجوز ایجاد کلاس را ندارید.")
        return
    await state.clear()
    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_class_token=token,
        product_class_school_id=school_id,
    )
    await state.set_state(ClassCreation.name)
    await message.answer("نام کلاس را وارد کنید (۲ تا ۸۰ نویسه):")


@router.callback_query(F.data.regexp(r"^product:class:create:[0-9]+$"))
async def product_class_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            school = await get_product_school(product_db, school_id=school_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not validate school before class creation.")
        await callback.answer("بررسی مدرسه ناموفق بود.", show_alert=True)
        return
    if school is None or not school["is_active"]:
        await callback.answer("مدرسه فعال پیدا نشد.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _begin_class_creation(
            callback.message,
            principal=principal,
            db=db,
            state=state,
            school_id=school_id,
        )


@router.message(Command("cancel"), SubjectCreation.name)
@router.message(Command("cancel"), TimetableCreation.weekday)
@router.message(Command("cancel"), TimetableCreation.subject)
@router.message(Command("cancel"), TimetableCreation.period)
@router.message(Command("cancel"), TimetableCreation.time_range)
@router.message(Command("cancel"), TimetableCreation.room)
@router.message(Command("cancel"), TimetableCreation.confirm)
async def class_configuration_cancel_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer("عملیات مدیریت کلاس لغو شد؛ تغییری ثبت نشد.")


@router.message(Command("cancel"), MemberCreation.telegram_id)
@router.message(Command("cancel"), MemberCreation.full_name)
@router.message(Command("cancel"), MemberCreation.confirm)
@router.message(Command("cancel"), AnnouncementCreation.title)
@router.message(Command("cancel"), AnnouncementCreation.body)
@router.message(Command("cancel"), AnnouncementCreation.confirm)
async def member_announcement_cancel_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer("عملیات لغو شد؛ تغییری ثبت نشد.")


@router.message(ClassCreation.name, F.text)
async def class_name_input(message: Message, state: FSMContext) -> None:
    value = " ".join((message.text or "").split())
    if not 2 <= len(value) <= 80:
        await message.answer("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
        return
    await state.update_data(product_class_name=value)
    await state.set_state(ClassCreation.grade)
    await message.answer("پایه یا مقطع کلاس را وارد کنید (مثلاً دهم):")


@router.message(ClassCreation.grade, F.text)
async def class_grade_input(message: Message, state: FSMContext) -> None:
    value = " ".join((message.text or "").split())
    if not 1 <= len(value) <= 32:
        await message.answer("پایه باید بین ۱ تا ۳۲ نویسه باشد.")
        return
    await state.update_data(product_class_grade=value)
    await state.set_state(ClassCreation.section)
    await message.answer("نام بخش/شعبه را وارد کنید؛ اگر ندارد، فقط «-» بفرستید:")


@router.message(ClassCreation.section, F.text)
async def class_section_input(message: Message, state: FSMContext) -> None:
    value = " ".join((message.text or "").split())
    section = None if value == "-" else value
    if section is not None and len(section) > 32:
        await message.answer("عنوان بخش کلاس حداکثر ۳۲ نویسه است.")
        return
    data = await state.get_data()
    token = data.get("product_class_token")
    if not token:
        await state.clear()
        await message.answer("فرم ایجاد کلاس منقضی شده است؛ دوباره شروع کنید.")
        return
    await state.update_data(product_class_section=section)
    await state.set_state(ClassCreation.confirm)
    await message.answer(
        "پیش‌نمایش ایجاد کلاس\n\n"
        f"نام: {data.get('product_class_name')}\n"
        f"پایه: {data.get('product_class_grade')}\n"
        f"بخش: {section or 'بدون بخش'}\n\n"
        "ثبت نهایی شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ایجاد کلاس",
                        callback_data=f"product:class:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:class:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:class:confirm:"))
async def product_class_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassCreation.confirm.state
        or not token
        or token != data.get("product_class_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:classes:create"):
        await state.clear()
        await callback.answer("مجوز ایجاد کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    school_id = int(data.get("product_class_school_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school_class = await create_product_class(
                    product_db,
                    school_id=school_id,
                    name=data.get("product_class_name", ""),
                    grade=data.get("product_class_grade", ""),
                    section=data.get("product_class_section"),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create ClassMate product class.")
        await state.clear()
        await callback.answer("ایجاد کلاس در دیتابیس ClassMate ناموفق بود.", show_alert=True)
        return

    audit_warning = False
    try:
        await record_event(
            db,
            event_type="product.class.created",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": school_class["id"],
                "school_id": school_id,
                "name": school_class["name"],
                "grade": school_class["grade"],
            },
        )
        await db.commit()
    except Exception:
        audit_warning = True
        await db.rollback()
        logger.exception(
            "Class %s was created but Work audit recording failed.",
            school_class["id"],
        )

    await state.clear()
    await callback.answer("کلاس ایجاد شد.")
    if callback.message is not None:
        notice = (
            "⚠️ کلاس ثبت شد، اما ثبت رویداد ممیزی نیازمند بررسی است. "
            if audit_warning
            else ""
        )
        await callback.message.edit_text(
            f"✅ {notice}کلاس ایجاد شد.\n\n"
            f"شناسه: {school_class['id']}\n"
            f"نام: {school_class['name']}\n"
            f"پایه: {school_class['grade']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به مدرسه",
                            callback_data=f"product:school:{school_id}",
                        )
                    ],
                    [InlineKeyboardButton(text="خانه", callback_data="root:menu")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:cancel:"))
async def product_class_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassCreation.confirm.state
        or not token
        or token != data.get("product_class_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ایجاد کلاس لغو شد؛ تغییری ثبت نشد.")




@router.callback_query(F.data.regexp(r"^product:school:edit:[0-9]+$"))
async def product_school_edit_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await callback.answer("مجوز ویرایش مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    async with product_session_factory() as product_db:
        school = await get_product_school(product_db, school_id=school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(product_school_edit_id=school_id)
    await state.set_state(SchoolEditing.name)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"نام فعلی مدرسه: {school['name']}\nنام جدید را وارد کنید:"
        )


@router.message(SchoolEditing.name, F.text)
async def product_school_edit_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 180:
        await message.answer("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.")
        return
    data = await state.get_data()
    school_id = int(data.get("product_school_edit_id") or 0)
    if school_id <= 0:
        await state.clear()
        await message.answer("فرم ویرایش مدرسه منقضی شده است؛ دوباره شروع کنید.")
        return
    token = secrets.token_urlsafe(8)
    await state.update_data(product_school_edit_name=name, product_school_edit_token=token)
    await state.set_state(SchoolEditing.confirm)
    await message.answer(
        f"پیش‌نمایش ویرایش مدرسه\n\nشناسه: {school_id}\nنام جدید: {name}\n\nثبت شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ویرایش",
                        callback_data=f"product:school:edit:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:school:edit:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:school:edit:confirm:"))
async def product_school_edit_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolEditing.confirm.state
        or not token
        or token != data.get("product_school_edit_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await state.clear()
        await callback.answer("مجوز ویرایش مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    school_id = int(data.get("product_school_edit_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school = await update_product_school(
                    product_db,
                    school_id=school_id,
                    name=data.get("product_school_edit_name", ""),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate school %s.", school_id)
        await state.clear()
        await callback.answer("ویرایش مدرسه ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.school.updated",
            actor_id=principal.user.id,
            aggregate_type="school",
            event_data={"school_id": school_id, "name": school["name"]},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("School update audit recording failed for %s.", school_id)
    await state.clear()
    await callback.answer("مدرسه ویرایش شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ نام مدرسه به «{school['name']}» تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به مدرسه",
                            callback_data=f"product:school:{school_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:school:edit:cancel:"))
async def product_school_edit_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolEditing.confirm.state
        or not token
        or token != data.get("product_school_edit_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ویرایش مدرسه لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.regexp(r"^product:school:status:[0-9]+:[01]$"))
async def product_school_status_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await callback.answer("مجوز تغییر وضعیت مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    school_id, desired = int(parts[3]), parts[4] == "1"
    async with product_session_factory() as product_db:
        school = await get_product_school(product_db, school_id=school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    action = "فعال‌سازی" if desired else "غیرفعال‌سازی"
    warning = (
        "\n\nبا غیرفعال‌سازی مدرسه، دسترسی آموزشی کلاس‌های آن نیز متوقف می‌شود؛ "
        "هیچ داده‌ای حذف نخواهد شد."
        if not desired
        else ""
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید مدرسهٔ «{school['name']}» را {action} کنید؟{warning}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=f"product:school:status:confirm:{school_id}:{1 if desired else 0}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:school:{school_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:school:status:confirm:[0-9]+:[01]$"))
async def product_school_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await callback.answer("مجوز تغییر وضعیت مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    school_id, desired = int(parts[4]), parts[5] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school = await set_product_school_active(
                    product_db,
                    school_id=school_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not change ClassMate school status.")
        await callback.answer("تغییر وضعیت مدرسه ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.school.status_changed",
            actor_id=principal.user.id,
            aggregate_type="school",
            event_data={"school_id": school_id, "is_active": desired},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("School status audit recording failed for %s.", school_id)
    await callback.answer("وضعیت مدرسه تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"وضعیت مدرسه «{school['name']}» به {'فعال' if desired else 'غیرفعال'} تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به مدرسه",
                            callback_data=f"product:school:{school_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:class:edit:[0-9]+$"))
async def product_class_edit_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز ویرایش کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    async with product_session_factory() as product_db:
        class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        product_class_edit_id=class_id,
        product_class_edit_name=class_info["name"],
        product_class_edit_grade=class_info["grade"],
        product_class_edit_section=class_info["section"],
    )
    await state.set_state(ClassEditing.name)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"نام فعلی کلاس: {class_info['name']}\nنام جدید را وارد کنید:"
        )


@router.message(ClassEditing.name, F.text)
async def product_class_edit_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 80:
        await message.answer("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
        return
    await state.update_data(product_class_edit_name=name)
    await state.set_state(ClassEditing.grade)
    await message.answer("پایهٔ جدید کلاس را وارد کنید:")


@router.message(ClassEditing.grade, F.text)
async def product_class_edit_grade_input(message: Message, state: FSMContext) -> None:
    grade = " ".join((message.text or "").split())
    if not 1 <= len(grade) <= 32:
        await message.answer("پایه باید بین ۱ تا ۳۲ نویسه باشد.")
        return
    await state.update_data(product_class_edit_grade=grade)
    await state.set_state(ClassEditing.section)
    await message.answer("بخش جدید را وارد کنید؛ برای پاک‌کردن بخش فعلی «-» بفرستید:")


@router.message(ClassEditing.section, F.text)
async def product_class_edit_section_input(message: Message, state: FSMContext) -> None:
    section = " ".join((message.text or "").split())
    if section == "-":
        section = None
    if section is not None and len(section) > 32:
        await message.answer("عنوان بخش کلاس حداکثر ۳۲ نویسه است.")
        return
    data = await state.get_data()
    class_id = int(data.get("product_class_edit_id") or 0)
    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_class_edit_section=section,
        product_class_edit_token=token,
    )
    await state.set_state(ClassEditing.confirm)
    await message.answer(
        "پیش‌نمایش ویرایش کلاس\n\n"
        f"شناسه: {class_id}\n"
        f"نام: {data.get('product_class_edit_name')}\n"
        f"پایه: {data.get('product_class_edit_grade')}\n"
        f"بخش: {section or 'بدون بخش'}\n\n"
        "ثبت تغییرات انجام شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ویرایش",
                        callback_data=f"product:class:edit:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:class:edit:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:class:edit:confirm:"))
async def product_class_edit_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassEditing.confirm.state
        or not token
        or token != data.get("product_class_edit_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await state.clear()
        await callback.answer("مجوز ویرایش کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int(data.get("product_class_edit_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                class_info = await update_product_class(
                    product_db,
                    class_id=class_id,
                    name=data.get("product_class_edit_name", ""),
                    grade=data.get("product_class_edit_grade", ""),
                    section=data.get("product_class_edit_section"),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate class %s.", class_id)
        await state.clear()
        await callback.answer("ویرایش کلاس ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class.updated",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "name": class_info["name"],
                "grade": class_info["grade"],
                "section": class_info["section"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class update audit recording failed for %s.", class_id)
    await state.clear()
    await callback.answer("کلاس ویرایش شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ کلاس «{class_info['name']}» ویرایش شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="بازگشت به مدیریت کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:edit:cancel:"))
async def product_class_edit_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassEditing.confirm.state
        or not token
        or token != data.get("product_class_edit_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ویرایش کلاس لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.regexp(r"^product:class:status:[0-9]+:[01]$"))
async def product_class_status_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز تغییر وضعیت کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, desired = int(parts[3]), parts[4] == "1"
    async with product_session_factory() as product_db:
        class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    action = "فعال‌سازی" if desired else "غیرفعال‌سازی"
    warning = (
        "\n\nبا غیرفعال‌سازی کلاس، دسترسی آموزشی و ارسال‌های زمان‌بندی‌شدهٔ آن متوقف می‌شود؛ "
        "هیچ داده‌ای حذف نخواهد شد."
        if not desired
        else ""
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید کلاس «{class_info['name']}» را {action} کنید؟{warning}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=f"product:class:status:confirm:{class_id}:{1 if desired else 0}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:class:status:confirm:[0-9]+:[01]$"))
async def product_class_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز تغییر وضعیت کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, desired = int(parts[4]), parts[5] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                class_info = await set_product_class_active(
                    product_db,
                    class_id=class_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not change ClassMate class status.")
        await callback.answer("تغییر وضعیت کلاس ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class.status_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={"class_id": class_id, "is_active": desired},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class status audit recording failed for %s.", class_id)
    await callback.answer("وضعیت کلاس تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"وضعیت کلاس «{class_info['name']}» به {'فعال' if desired else 'غیرفعال'} تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="بازگشت به مدیریت کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:class:[0-9]+$"))
async def product_class_detail_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:read"):
        await callback.answer("مجوز مشاهدهٔ کلاس‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            if class_info is None:
                await callback.answer("کلاس پیدا نشد.", show_alert=True)
                return
            subjects = await list_class_subjects(product_db, class_id=class_id)
            timetable = await list_class_timetable(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate class %s.", callback.data)
        await callback.answer("دریافت جزئیات کلاس ناموفق بود.", show_alert=True)
        return

    lines = [
        f"👥 کلاس: {class_info['name']}",
        f"مدرسه: {class_info['school_name']}",
        f"پایه: {class_info['grade']}",
        f"بخش: {class_info['section'] or '—'}",
        f"وضعیت: {'فعال' if class_info['is_active'] else 'غیرفعال'}",
        f"گروه تلگرام: {class_info['group_chat_title'] or 'متصل نیست'}",
        "",
        f"درس‌های تخصیص‌یافته: {len(subjects)}",
    ]
    lines.extend(f"• {item['name']}" for item in subjects[:20])
    lines.extend(["", f"زنگ‌های برنامه هفتگی: {len(timetable)}"])
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="➕ افزودن درس",
                            callback_data=f"product:subject:add:{class_id}",
                        ),
                        InlineKeyboardButton(
                            text="📅 مشاهده برنامه هفتگی",
                            callback_data=f"product:timetable:view:{class_id}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="➕ افزودن زنگ",
                            callback_data=f"product:timetable:add:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👥 اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        ),
                        InlineKeyboardButton(
                            text="📢 اطلاعیه‌ها",
                            callback_data=f"product:announcements:{class_id}",
                        ),
                    ],
                    *(
                        [
                            InlineKeyboardButton(
                                text="✏️ ویرایش کلاس",
                                callback_data=f"product:class:edit:{class_id}",
                            ),
                            InlineKeyboardButton(
                                text=("⛔ غیرفعال‌سازی کلاس" if class_info["is_active"] else "♻️ فعال‌سازی کلاس"),
                                callback_data=f"product:class:status:{class_id}:{0 if class_info['is_active'] else 1}",
                            ),
                        ]
                    ] if await _has_permission(db, principal, "platform:classes:manage") else []
                    ),
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به مدرسه",
                            callback_data=f"product:school:{class_info['school_id']}",
                        )
                    ],
                    [InlineKeyboardButton(text="خانه", callback_data="root:menu")],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:subject:add:[0-9]+$"))
async def product_subject_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:subjects:manage"):
        await callback.answer("مجوز مدیریت درس‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not validate class for subject creation.")
        await callback.answer("بررسی کلاس ناموفق بود.", show_alert=True)
        return
    if class_info is None or not class_info["is_active"]:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(product_subject_class_id=class_id)
    await state.set_state(SubjectCreation.name)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("نام درس را وارد کنید (۲ تا ۱۰۰ نویسه):")


@router.message(SubjectCreation.name, F.text)
async def product_subject_name_input(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 100:
        await message.answer("نام درس باید بین ۲ تا ۱۰۰ نویسه باشد.")
        return
    if not await _has_permission(db, principal, "platform:subjects:manage"):
        await state.clear()
        await message.answer("مجوز مدیریت درس‌ها را ندارید.")
        return
    if product_session_factory is None:
        await state.clear()
        await message.answer("اتصال دیتابیس ClassMate فعال نیست.")
        return

    data = await state.get_data()
    class_id = int(data.get("product_subject_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                subject = await create_and_assign_product_subject(
                    product_db,
                    class_id=class_id,
                    name=name,
                )
    except ApplicationError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        logger.exception("Could not create or assign ClassMate subject.")
        await state.clear()
        await message.answer("ثبت درس ناموفق بود.")
        return

    try:
        await record_event(
            db,
            event_type="product.class_subject.assigned",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "school_id": subject["school_id"],
                "subject_id": subject["id"],
                "subject_name": subject["name"],
                "created": subject["created"],
                "assigned": subject["assigned"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Subject assignment audit failed for class %s.", class_id)

    await state.clear()
    result_label = "درس ایجاد و به کلاس تخصیص داده شد." if subject["created"] else "درس موجود به کلاس تخصیص داده شد."
    if not subject["assigned"]:
        result_label = "این درس از قبل به کلاس تخصیص داشت."
    await message.answer(
        f"✅ {result_label}\nدرس: {subject['name']}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="بازگشت به مدیریت کلاس",
                        callback_data=f"product:class:{class_id}",
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^product:timetable:view:[0-9]+$"))
async def product_timetable_view_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:timetable:manage"):
        await callback.answer("مجوز مشاهده برنامه هفتگی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            if class_info is None:
                await callback.answer("کلاس پیدا نشد.", show_alert=True)
                return
            entries = await list_class_timetable(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load timetable for class %s.", callback.data)
        await callback.answer("دریافت برنامه هفتگی ناموفق بود.", show_alert=True)
        return

    day_names = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
    lines = [f"📅 برنامه هفتگی کلاس {class_info['name']}", ""]
    if not entries:
        lines.append("هنوز زنگی برای این کلاس ثبت نشده است.")
    else:
        for weekday in range(7):
            day_entries = [item for item in entries if item["weekday"] == weekday]
            if not day_entries:
                continue
            lines.append(f"**{day_names[weekday]}**")
            for item in day_entries:
                start = item["starts_at"].strftime("%H:%M") if item["starts_at"] else "—"
                end = item["ends_at"].strftime("%H:%M") if item["ends_at"] else "—"
                room = f" · {item['room']}" if item["room"] else ""
                lines.append(
                    f"زنگ {item['period']}: {item['subject_name']} · {start} تا {end}{room}"
                )
            lines.append("")
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="➕ افزودن زنگ",
                            callback_data=f"product:timetable:add:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:add:[0-9]+$"))
async def product_timetable_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:timetable:manage"):
        await callback.answer("مجوز مدیریت برنامه هفتگی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            subjects = await list_class_subjects(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not prepare timetable creation.")
        await callback.answer("آماده‌سازی برنامه هفتگی ناموفق بود.", show_alert=True)
        return
    if class_info is None or not class_info["is_active"]:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    if not subjects:
        await callback.answer(
            "ابتدا حداقل یک درس به کلاس تخصیص دهید.",
            show_alert=True,
        )
        return

    await state.clear()
    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_timetable_token=token,
        product_timetable_class_id=class_id,
        product_timetable_subjects=subjects,
    )
    await state.set_state(TimetableCreation.weekday)
    day_names = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
    rows = [
        [
            InlineKeyboardButton(
                text=day_names[index],
                callback_data=f"product:timetable:weekday:{class_id}:{index}",
            )
        ]
        for index in range(7)
    ]
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"روز هفته برای کلاس {class_info['name']} را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:weekday:[0-9]+:[0-6]$"))
async def timetable_weekday_select_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    parts = (callback.data or "").split(":")
    if (
        await state.get_state() != TimetableCreation.weekday.state
        or len(parts) != 5
        or int(parts[3]) != int(data.get("product_timetable_class_id") or 0)
    ):
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    weekday = int(parts[4])
    await state.update_data(product_timetable_weekday=weekday)
    await state.set_state(TimetableCreation.subject)
    subjects = data.get("product_timetable_subjects", [])
    rows = [
        [
            InlineKeyboardButton(
                text=subject["name"][:45],
                callback_data=f"product:timetable:subject:{subject['id']}",
            )
        ]
        for subject in subjects[:50]
    ]
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "درس این زنگ را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:subject:[0-9]+$"))
async def timetable_subject_select_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if await state.get_state() != TimetableCreation.subject:
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    subject_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    subjects = data.get("product_timetable_subjects", [])
    subject = next((item for item in subjects if int(item["id"]) == subject_id), None)
    if subject is None:
        await callback.answer("درس انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    await state.update_data(
        product_timetable_subject_id=subject_id,
        product_timetable_subject_name=subject["name"],
    )
    await state.set_state(TimetableCreation.period)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("شماره زنگ را از ۱ تا ۱۶ وارد کنید:")


@router.message(TimetableCreation.period, F.text)
async def timetable_period_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 16:
        await message.answer("شماره زنگ باید عددی بین ۱ تا ۱۶ باشد.")
        return
    await state.update_data(product_timetable_period=int(value))
    await state.set_state(TimetableCreation.time_range)
    await message.answer("بازه زمانی را با قالب 08:00-08:45 وارد کنید؛ اگر زمان مشخص نیست، «-» بفرستید:")


@router.message(TimetableCreation.time_range, F.text)
async def timetable_time_range_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value == "-":
        start_time = end_time = None
    else:
        parts = value.split("-")
        if len(parts) != 2:
            await message.answer("قالب زمان نامعتبر است؛ نمونه صحیح: 08:00-08:45")
            return
        try:
            from datetime import time as time_type

            start = time_type.fromisoformat(parts[0].strip())
            end = time_type.fromisoformat(parts[1].strip())
        except ValueError:
            await message.answer("زمان باید با قالب HH:MM باشد.")
            return
        if end <= start:
            await message.answer("زمان پایان باید بعد از زمان شروع باشد.")
            return
        start_time, end_time = start.strftime("%H:%M"), end.strftime("%H:%M")
    await state.update_data(
        product_timetable_start=start_time,
        product_timetable_end=end_time,
    )
    await state.set_state(TimetableCreation.room)
    await message.answer("نام کلاس/اتاق را وارد کنید؛ اگر ندارد، «-» بفرستید:")


@router.message(TimetableCreation.room, F.text)
async def timetable_room_input(message: Message, state: FSMContext) -> None:
    room = " ".join((message.text or "").split())
    if room == "-":
        room = None
    if room is not None and len(room) > 80:
        await message.answer("نام کلاس/اتاق حداکثر ۸۰ نویسه است.")
        return
    await state.update_data(product_timetable_room=room)
    data = await state.get_data()
    await state.set_state(TimetableCreation.confirm)
    day_names = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
    time_label = (
        f"{data.get('product_timetable_start')} تا {data.get('product_timetable_end')}"
        if data.get("product_timetable_start")
        else "زمان مشخص نشده"
    )
    await message.answer(
        "پیش‌نمایش ثبت زنگ\n\n"
        f"روز: {day_names[data['product_timetable_weekday']]}\n"
        f"درس: {data['product_timetable_subject_name']}\n"
        f"زنگ: {data['product_timetable_period']}\n"
        f"زمان: {time_label}\n"
        f"کلاس/اتاق: {room or '—'}\n\n"
        "ثبت نهایی شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ ثبت زنگ",
                        callback_data=f"product:timetable:confirm:{data['product_timetable_token']}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:timetable:cancel:{data['product_timetable_token']}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:timetable:confirm:"))
async def timetable_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != TimetableCreation.confirm.state
        or not token
        or token != data.get("product_timetable_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:timetable:manage"):
        await state.clear()
        await callback.answer("مجوز مدیریت برنامه هفتگی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_timetable_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                entry = await create_product_timetable_entry(
                    product_db,
                    class_id=class_id,
                    subject_id=int(data.get("product_timetable_subject_id") or 0),
                    weekday=int(data.get("product_timetable_weekday") or 0),
                    period=int(data.get("product_timetable_period") or 0),
                    starts_at=data.get("product_timetable_start"),
                    ends_at=data.get("product_timetable_end"),
                    room=data.get("product_timetable_room"),
                )
    except ResourceConflict as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create ClassMate timetable entry.")
        await state.clear()
        await callback.answer("ثبت زنگ در دیتابیس ClassMate ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type="product.timetable_entry.created",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "entry_id": entry["id"],
                "weekday": entry["weekday"],
                "period": entry["period"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Timetable audit recording failed for class %s.", class_id)

    await state.clear()
    await callback.answer("زنگ ثبت شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "✅ زنگ برنامه هفتگی ثبت شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📅 مشاهده برنامه هفتگی",
                            callback_data=f"product:timetable:view:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:timetable:cancel:"))
async def timetable_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != TimetableCreation.confirm.state
        or not token
        or token != data.get("product_timetable_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ثبت زنگ لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.regexp(r"^product:members:[0-9]+$"))
async def product_class_members_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت اعضای کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            members = await list_product_class_members(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate class members.")
        await callback.answer("دریافت اعضای کلاس ناموفق بود.", show_alert=True)
        return
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return

    lines = [
        f"👥 اعضای کلاس {class_info['name']}",
        f"تعداد اعضا: {len(members)}",
        "",
    ]
    rows = []
    if members:
        for member in members[:30]:
            active = member["membership_is_active"] and member["user_is_active"]
            lines.append(
                f"• {member['full_name']} · {member['telegram_id']} · "
                f"{'فعال' if active else 'غیرفعال'}"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"⛔ تعلیق · {member['full_name'][:22]}"
                            if active
                            else f"♻️ فعال‌سازی · {member['full_name'][:22]}"
                        ),
                        callback_data=(
                            f"product:member:status:{class_id}:{member['user_id']}:"
                            f"{0 if active else 1}"
                        ),
                    )
                ]
            )
    else:
        lines.append("هنوز عضوی به این کلاس اضافه نشده است.")
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ افزودن عضو با Telegram ID",
                    callback_data=f"product:member:add:{class_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به کلاس",
                    callback_data=f"product:class:{class_id}",
                )
            ],
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:member:add:[0-9]+$"))
async def product_class_member_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز افزودن عضو را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    async with product_session_factory() as product_db:
        class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None or not class_info["is_active"]:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(product_member_class_id=class_id)
    await state.set_state(MemberCreation.telegram_id)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("Telegram ID عضو را فقط به‌صورت عدد وارد کنید:")


@router.message(MemberCreation.telegram_id, F.text)
async def product_member_telegram_id_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or not 0 < int(value) <= 9_223_372_036_854_775_807:
        await message.answer("Telegram ID معتبر نیست؛ فقط عدد مثبت وارد کنید.")
        return
    await state.update_data(product_member_telegram_id=int(value))
    await state.set_state(MemberCreation.full_name)
    await message.answer("نام و نام خانوادگی عضو را وارد کنید:")


@router.message(MemberCreation.full_name, F.text)
async def product_member_full_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 1 <= len(name) <= 160:
        await message.answer("نام باید بین ۱ تا ۱۶۰ نویسه باشد.")
        return
    data = await state.get_data()
    token = secrets.token_urlsafe(8)
    await state.update_data(product_member_full_name=name, product_member_token=token)
    await state.set_state(MemberCreation.confirm)
    await message.answer(
        "پیش‌نمایش افزودن عضو\n\n"
        f"Telegram ID: {data['product_member_telegram_id']}\n"
        f"نام: {name}\n\n"
        "عضویت ثبت شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ افزودن عضو",
                        callback_data=f"product:member:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:member:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:member:confirm:"))
async def product_member_add_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberCreation.confirm.state
        or not token
        or token != data.get("product_member_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await state.clear()
        await callback.answer("مجوز افزودن عضو را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int(data.get("product_member_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                member = await add_product_class_member(
                    product_db,
                    class_id=class_id,
                    telegram_id=int(data["product_member_telegram_id"]),
                    full_name=data["product_member_full_name"],
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not add ClassMate class member.")
        await state.clear()
        await callback.answer("افزودن عضو ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type="product.class_member.added",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "user_id": member["user_id"],
                "telegram_id": member["telegram_id"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class member audit recording failed.")

    await state.clear()
    await callback.answer("عضویت ثبت شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ عضو به کلاس اضافه شد.\nنام: {member['full_name']}\nTelegram ID: {member['telegram_id']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👥 بازگشت به اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:member:status:[0-9]+:[0-9]+:[01]$"))
async def product_member_status_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت اعضای کلاس را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, user_id, desired = int(parts[3]), int(parts[4]), parts[5] == "1"
    action = "فعال‌سازی مجدد" if desired else "تعلیق"
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید این عضویت را {action} کنید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=(
                                f"product:member:status:confirm:{class_id}:{user_id}:"
                                f"{1 if desired else 0}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:members:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:member:status:confirm:[0-9]+:[0-9]+:[01]$"))
async def product_member_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت اعضای کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, user_id, desired = int(parts[4]), int(parts[5]), parts[6] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                changed = await set_product_class_member_active(
                    product_db,
                    class_id=class_id,
                    user_id=user_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate membership status.")
        await callback.answer("تغییر وضعیت عضویت ناموفق بود.", show_alert=True)
        return
    if not changed:
        await callback.answer("عضویت پیدا نشد.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class_member.status_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={"class_id": class_id, "user_id": user_id, "is_active": desired},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class member status audit recording failed.")
    await callback.answer("وضعیت عضویت تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "وضعیت عضویت تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👥 بازگشت به اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:announcements:[0-9]+$"))
async def product_class_announcements_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز مدیریت اطلاعیه‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    try:
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            announcements = await list_product_class_announcements(product_db, class_id=class_id)
    except Exception:
        logger.exception("Could not load ClassMate announcements.")
        await callback.answer("دریافت اطلاعیه‌ها ناموفق بود.", show_alert=True)
        return
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return

    lines = [f"📢 اطلاعیه‌های کلاس {class_info['name']}", ""]
    rows = []
    if announcements:
        for item in announcements[:20]:
            state_label = "فعال" if item["is_active"] else "غیرفعال"
            lines.append(f"• {item['title']} · {state_label}")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"غیرفعال‌سازی · {item['title'][:22]}"
                            if item["is_active"]
                            else f"فعال‌سازی · {item['title'][:22]}"
                        ),
                        callback_data=(
                            f"product:announcement:status:{class_id}:{item['id']}:"
                            f"{0 if item['is_active'] else 1}"
                        ),
                    )
                ]
            )
    else:
        lines.append("هنوز اطلاعیه‌ای برای این کلاس ثبت نشده است.")
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ ایجاد اطلاعیه",
                    callback_data=f"product:announcement:add:{class_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به کلاس",
                    callback_data=f"product:class:{class_id}",
                )
            ],
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:announcement:add:[0-9]+$"))
async def product_announcement_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز ایجاد اطلاعیه را ندارید.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    await state.clear()
    await state.update_data(product_announcement_class_id=class_id)
    await state.set_state(AnnouncementCreation.title)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("عنوان اطلاعیه را وارد کنید (۲ تا ۱۸۰ نویسه):")


@router.message(AnnouncementCreation.title, F.text)
async def product_announcement_title_input(message: Message, state: FSMContext) -> None:
    title = " ".join((message.text or "").split())
    if not 2 <= len(title) <= 180:
        await message.answer("عنوان اطلاعیه باید بین ۲ تا ۱۸۰ نویسه باشد.")
        return
    await state.update_data(product_announcement_title=title)
    await state.set_state(AnnouncementCreation.body)
    await message.answer("متن کامل اطلاعیه را وارد کنید (۲ تا ۸۰۰۰ نویسه):")


@router.message(AnnouncementCreation.body, F.text)
async def product_announcement_body_input(message: Message, state: FSMContext) -> None:
    body = (message.text or "").strip()
    if not 2 <= len(body) <= 8000:
        await message.answer("متن اطلاعیه باید بین ۲ تا ۸۰۰۰ نویسه باشد.")
        return
    data = await state.get_data()
    token = secrets.token_urlsafe(8)
    await state.update_data(product_announcement_body=body, product_announcement_token=token)
    await state.set_state(AnnouncementCreation.confirm)
    await message.answer(
        "پیش‌نمایش اطلاعیه\n\n"
        f"عنوان: {data['product_announcement_title']}\n\n"
        f"{body[:1200]}{'…' if len(body) > 1200 else ''}\n\n"
        "انتشار انجام شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ انتشار اطلاعیه",
                        callback_data=f"product:announcement:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:announcement:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:announcement:confirm:"))
async def product_announcement_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != AnnouncementCreation.confirm.state
        or not token
        or token != data.get("product_announcement_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await state.clear()
        await callback.answer("مجوز ایجاد اطلاعیه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_announcement_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                announcement = await create_product_announcement(
                    product_db,
                    class_id=class_id,
                    telegram_id=principal.telegram_account.telegram_user_id,
                    author_name=principal.user.display_name,
                    title=data["product_announcement_title"],
                    body=data["product_announcement_body"],
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not publish ClassMate announcement.")
        await state.clear()
        await callback.answer("انتشار اطلاعیه ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type="product.announcement.created",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "announcement_id": announcement["id"],
                "title": announcement["title"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Announcement audit recording failed.")

    await state.clear()
    await callback.answer("اطلاعیه منتشر شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ اطلاعیه منتشر شد.\nعنوان: {announcement['title']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📢 بازگشت به اطلاعیه‌ها",
                            callback_data=f"product:announcements:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:announcement:status:[0-9]+:[0-9]+:[01]$"))
async def product_announcement_status_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز مدیریت اطلاعیه‌ها را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, announcement_id, desired = int(parts[3]), int(parts[4]), parts[5] == "1"
    action = "فعال‌سازی" if desired else "غیرفعال‌سازی"
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید این اطلاعیه را {action} کنید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=(
                                f"product:announcement:status:confirm:{class_id}:"
                                f"{announcement_id}:{1 if desired else 0}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:announcements:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:announcement:status:confirm:[0-9]+:[0-9]+:[01]$"))
async def product_announcement_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز مدیریت اطلاعیه‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, announcement_id, desired = int(parts[4]), int(parts[5]), parts[6] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                changed = await set_product_announcement_active(
                    product_db,
                    class_id=class_id,
                    announcement_id=announcement_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate announcement status.")
        await callback.answer("تغییر وضعیت اطلاعیه ناموفق بود.", show_alert=True)
        return
    if not changed:
        await callback.answer("اطلاعیه پیدا نشد.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.announcement.status_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "announcement_id": announcement_id,
                "is_active": desired,
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Announcement status audit recording failed.")
    await callback.answer("وضعیت اطلاعیه تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "وضعیت اطلاعیه تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📢 بازگشت به اطلاعیه‌ها",
                            callback_data=f"product:announcements:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:member:cancel:"))
async def product_member_add_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberCreation.confirm.state
        or not token
        or token != data.get("product_member_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("افزودن عضو لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.startswith("product:announcement:cancel:"))
async def product_announcement_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != AnnouncementCreation.confirm.state
        or not token
        or token != data.get("product_announcement_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ایجاد اطلاعیه لغو شد؛ تغییری ثبت نشد.")
