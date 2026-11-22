from __future__ import annotations

from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.control_center import (
    create_class_group_handshake,
    get_class_telegram_group,
    get_platform_report,
    revoke_class_telegram_group,
)
from app.application.errors import ApplicationError
from app.db.models import ClassGroup
from app.modules.identity.principal import TelegramPrincipal

router = Router(name="control-center")
TEHRAN = ZoneInfo("Asia/Tehran")


def _report_text(report: dict) -> str:
    generated = report["generated_at"].astimezone(TEHRAN)
    return (
        "╔  🤖 آمار سامانه ClassMate\n"
        "║\n"
        f"╣  👥 کل کاربران: {report['total_users']:,} نفر\n"
        f"╣  ✅ کاربران فعال حساب: {report['active_users']:,} نفر\n"
        "║\n"
        "╣  📥 کاربران جدید در روز / هفته / ماه گذشته:\n"
        f"╣  {report['new_users_1d']:,} / {report['new_users_7d']:,} / {report['new_users_30d']:,} نفر\n"
        "║\n"
        "╣  📡 کاربران فعال در یک / چهار / دوازده ساعت گذشته:\n"
        f"╣  {report['active_users_1h']:,} / {report['active_users_4h']:,} / {report['active_users_12h']:,} نفر\n"
        "║\n"
        f"╣  🏫 مدارس: {report['schools']:,}\n"
        f"╣  👥 کلاس‌ها: {report['classes']:,}\n"
        f"╣  📚 درس‌ها: {report['subjects']:,}\n"
        f"╣  🔗 گروه‌های متصل به کلاس: {report['connected_groups']:,}\n"
        f"╣  📡 رویدادهای ثبت‌شده در ۲۴ ساعت: {report['events_24h']:,}\n"
        "║\n"
        f"╚  🗓 {generated:%Y-%m-%d}   ⏰ {generated:%H:%M}"
    )


async def _show_reports(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    try:
        report = await get_platform_report(db, actor_id=principal.user.id)
    except ApplicationError as exc:
        await message.answer(str(exc))
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 بروزرسانی گزارش", callback_data="control:reports:refresh")],
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")],
        ]
    )
    await message.answer(_report_text(report), reply_markup=keyboard)


async def _hide_root_keyboard(message: Message) -> None:
    carrier = await message.answer("·", reply_markup=ReplyKeyboardRemove())
    try:
        await carrier.delete()
    except TelegramBadRequest:
        pass


@router.message(Command("reports"))
@router.message(Command("dashboard"))
async def reports_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await _hide_root_keyboard(message)
    await _show_reports(message, principal, db)


@router.message(F.text == "📊 داشبورد و گزارش‌ها")
async def reports_menu_button(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await _hide_root_keyboard(message)
    await _show_reports(message, principal, db)


@router.callback_query(F.data == "control:reports:refresh")
async def reports_refresh_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    try:
        report = await get_platform_report(db, actor_id=principal.user.id)
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("گزارش بروزرسانی شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            _report_text(report),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 بروزرسانی گزارش", callback_data="control:reports:refresh")],
                    [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")],
                ]
            ),
        )


async def _render_group_panel(
    callback: CallbackQuery,
    *,
    class_group: ClassGroup,
    connection,
) -> None:
    rows: list[list[InlineKeyboardButton]] = []
    if connection is None:
        body = (
            f"📡 مدیریت گروه کلاس «{escape(class_group.name)}»\n\n"
            "هنوز گروهی به این کلاس متصل نشده است."
        )
    else:
        verified = (
            connection.last_verified_at.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M")
            if connection.last_verified_at
            else "ثبت نشده"
        )
        body = (
            f"📡 گروه متصل به کلاس «{escape(class_group.name)}»\n\n"
            f"نام گروه: {escape(connection.chat_title)}\n"
            f"شناسه گروه: <code>{connection.telegram_chat_id}</code>\n"
            f"نوع گروه: {connection.chat_type}\n"
            f"وضعیت: {connection.status}\n"
            f"آخرین بررسی: {verified}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔌 قطع اتصال گروه",
                    callback_data=f"class:group:revoke:{class_group.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔗 صدور کد اتصال گروه",
                callback_data=f"class:group:code:{class_group.id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ بازگشت به کلاس",
                callback_data=f"class:open:{class_group.id}",
            )
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(
            body,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^class:group:[0-9a-fA-F-]{36}$"))
async def class_group_panel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    try:
        class_id = UUID((callback.data or "").split(":")[-1])
        class_group = (
            await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
        ).scalar_one_or_none()
        if class_group is None:
            raise ApplicationError("کلاس پیدا نشد.")
        connection = await get_class_telegram_group(
            db,
            actor_id=principal.user.id,
            class_group_id=class_id,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer()
    await _render_group_panel(callback, class_group=class_group, connection=connection)


@router.callback_query(F.data.startswith("class:group:code:"))
async def class_group_code_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    try:
        class_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
        code, class_group, expires_at = await create_class_group_handshake(
            db,
            actor_id=principal.user.id,
            class_group_id=class_id,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("کد اتصال صادر شد.")
    expires_local = expires_at.astimezone(TEHRAN)
    if callback.message is not None:
        await callback.message.edit_text(
            f"🔐 کد اتصال گروه کلاس «{escape(class_group.name)}»\n\n"
            f"<code>{code}</code>\n\n"
            "۱. ربات اصلی ClassMate را به گروه موردنظر اضافه کنید و دسترسی مدیر بدهید.\n"
            "۲. همان مدیری که این کد را گرفته، در گروه دستور زیر را برای ربات اصلی بفرستد:\n"
            f"<code>/classmate_connect {code}</code>\n\n"
            f"کد فقط یک‌بار قابل استفاده است و تا {expires_local:%H:%M} اعتبار دارد.\n"
            "ربات اصلی باید مدیر بودن خودش و مدیر فرستنده را پیش از تکمیل اتصال بررسی کند.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به مدیریت گروه",
                            callback_data=f"class:group:{class_group.id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("class:group:revoke:confirm:"))
async def class_group_revoke_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    try:
        class_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
        revoked = await revoke_class_telegram_group(
            db,
            actor_id=principal.user.id,
            class_group_id=class_id,
        )
        class_group = (
            await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
        ).scalar_one()
        connection = await get_class_telegram_group(
            db,
            actor_id=principal.user.id,
            class_group_id=class_id,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("اتصال گروه قطع شد." if revoked else "اتصالی برای قطع‌کردن وجود نداشت.")
    if callback.message is not None:
        await _render_group_panel(
            callback,
            class_group=class_group,
            connection=connection,
        )


@router.callback_query(F.data.startswith("class:group:revoke:"))
async def class_group_revoke_prompt_callback(callback: CallbackQuery) -> None:
    class_id = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "اتصال این گروه به کلاس قطع شود؟\nاین کار عضویت گروه در تلگرام را تغییر نمی‌دهد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="بله، قطع اتصال",
                            callback_data=f"class:group:revoke:confirm:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="لغو",
                            callback_data=f"class:group:{class_id}",
                        )
                    ],
                ]
            ),
        )
