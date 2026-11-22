from __future__ import annotations

from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
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
    list_school_class_group_directory,
    revoke_class_telegram_group,
)
from app.application.errors import ApplicationError
from app.db.models import ClassGroup
from app.modules.access.policy import has_permission
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
    can_manage: bool,
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
        if can_manage:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔌 قطع اتصال گروه",
                        callback_data=f"class:group:revoke:{class_group.id}",
                    )
                ]
            )

    if can_manage:
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
        can_manage = await has_permission(
            db,
            user_id=principal.user.id,
            permission_key="class:group:manage",
            tenant_id=class_group.tenant_id,
            scope_type="class",
            scope_id=class_group.id,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer()
    await _render_group_panel(
        callback,
        class_group=class_group,
        connection=connection,
        can_manage=can_manage,
    )


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
            can_manage=True,
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

@router.callback_query(F.data.regexp(r"^school:groups(?::[0-9]+)?$"))
async def school_group_directory_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    try:
        tenant_id = UUID(data["context_tenant_id"])
        school_id = UUID(data["context_school_id"])
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        if page < 1:
            raise ValueError
        directory = await list_school_class_group_directory(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=8,
            offset=(page - 1) * 8,
        )
    except (KeyError, TypeError, ValueError):
        await callback.answer(
            "مدرسه یا شماره صفحه معتبر نیست؛ دوباره مدرسه را انتخاب کنید.",
            show_alert=True,
        )
        return
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if directory["pages"] and page > directory["pages"]:
        page = directory["pages"]
        directory = await list_school_class_group_directory(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=8,
            offset=(page - 1) * 8,
        )
    await callback.answer()

    lines = [
        "╔  📡 مدیریت گروه‌های کلاس",
        "║",
        f"╣  🏫 مدرسه: {directory['school_name']}",
        f"╣  👥 تعداد کلاس‌ها: {directory['total_classes']:,}",
        f"╣  🔗 گروه‌های متصل: {directory['connected_classes']:,}",
        f"╣  ⚠️ کلاس‌های بدون گروه: {directory['disconnected_classes']:,}",
        "║",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if not directory["items"]:
        lines.append("╣  هنوز کلاسی برای این مدرسه ثبت نشده است.")
    else:
        for index, item in enumerate(directory["items"], start=(page - 1) * 8 + 1):
            class_group = item["class_group"]
            connection = item["connection"]
            if connection is None:
                lines.append(
                    f"╣  {index:02d}. {class_group.name[:72]} · پایه {class_group.grade[:24]} · بدون گروه"
                )
                label = f"⚠️ {class_group.name[:42]} · بدون گروه"
            else:
                lines.append(
                    f"╣  {index:02d}. {class_group.name[:72]} · پایه {class_group.grade[:24]}"
                )
                lines.append(f"║      🔗 {connection.chat_title[:72]}")
                label = f"🔗 {class_group.name[:42]} · متصل"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label[:60],
                        callback_data=f"class:group:{class_group.id}",
                    )
                ]
            )

    lines.extend(["║", f"╚  صفحهٔ {page} از {max(1, directory['pages'])}"])
    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"school:groups:{page - 1}",
            )
        )
    if page < directory["pages"]:
        navigation.append(
            InlineKeyboardButton(
                text="صفحه بعد ➡️",
                callback_data=f"school:groups:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [InlineKeyboardButton(text="⬅️ بازگشت به مدرسه", callback_data="school:menu")]
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

