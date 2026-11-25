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

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به مدارس",
                            callback_data="product:schools",
                        )
                    ],
                    [InlineKeyboardButton(text="خانه", callback_data="root:menu")],
                ]
            ),
        )
