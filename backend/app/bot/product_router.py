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

from app.application.errors import AccessDenied, ApplicationError
from app.application.product_management import (
    create_product_school,
    get_product_school,
    create_product_class,
    list_product_classes,
    list_product_schools,
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


class ClassCreation(StatesGroup):
    name = State()
    grade = State()
    section = State()
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
        f"تعداد کلاس‌ها: {school['class_count']}",
        "",
        "کلاس‌ها:",
    ]
    if classes:
        for item in classes[:30]:
            section = f" · {item['section']}" if item.get("section") else ""
            state_label = "فعال" if item["is_active"] else "غیرفعال"
            lines.append(
                f"• {item['name']} · پایه {item['grade']}{section} · {state_label}"
            )
        if len(classes) > 30:
            lines.append(f"… و {len(classes) - 30} کلاس دیگر")
    else:
        lines.append("هنوز کلاسی برای این مدرسه ثبت نشده است.")

    can_create_class = (
        school["is_active"]
        and await _has_permission(db, principal, "platform:classes:create")
    )
    rows = []
    if can_create_class:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ افزودن کلاس",
                    callback_data=f"product:class:create:{school_id}",
                )
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
