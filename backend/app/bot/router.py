from __future__ import annotations

import logging
import re
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    ApplicationError,
)
from app.application.tenants import create_tenant, list_tenants
from app.db.models import User
from app.modules.access.policy import has_permission
from app.modules.identity.pairing import (
    PairingNotAllowed,
    PairingRejected,
    consume_admin_pairing,
    create_admin_pairing,
)
from app.modules.identity.principal import TelegramPrincipal

logger = logging.getLogger(__name__)
router = Router(name="admin")


class TenantCreation(StatesGroup):
    name = State()
    slug = State()
    timezone = State()
    locale = State()
    confirm = State()


def wizard_controls(flow_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="بازگشت",
                    callback_data=f"wizard:back:{flow_token}",
                ),
                InlineKeyboardButton(
                    text="لغو عملیات",
                    callback_data=f"wizard:cancel:{flow_token}",
                ),
            ]
        ]
    )


async def main_menu(
    db: AsyncSession,
    principal: TelegramPrincipal,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="حساب مدیریتی من",
                callback_data="account:me",
            )
        ]
    ]
    can_list_tenants = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:tenants:read",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    can_create_tenants = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:tenants:create",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    if can_list_tenants:
        rows.append(
            [InlineKeyboardButton(text="فهرست مجموعه‌ها", callback_data="tenant:list")]
        )
    if can_create_tenants:
        rows.append(
            [InlineKeyboardButton(text="ایجاد مجموعه جدید", callback_data="tenant:create")]
        )
    rows.append([InlineKeyboardButton(text="راهنما", callback_data="help:show")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_main_menu(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await message.answer(
        f"ClassMate Work — مرکز مدیریت\n\n"
        f"درود {principal.user.display_name}.\n"
        "هویت تلگرامی شما تأیید شده است. گزینه‌های زیر بر اساس مجوزهای فعلی شما نمایش داده می‌شوند.",
        reply_markup=await main_menu(db, principal),
    )


async def _show_tenant_step(
    message: Message,
    state: FSMContext,
    step: str,
) -> None:
    prompts = {
        "name": "نام مجموعه را وارد کنید (۲ تا ۱۸۰ نویسه):",
        "slug": "شناسه انگلیسی مجموعه را وارد کنید؛ فقط حروف کوچک، عدد و خط تیره:",
        "timezone": "منطقه زمانی را وارد کنید؛ نمونه: Asia/Tehran",
        "locale": "زبان/محلی‌سازی را وارد کنید؛ نمونه: fa-IR",
    }
    data = await state.get_data()
    flow_token = data.get("flow_token", "")
    if not flow_token:
        await state.clear()
        await message.answer("فرم منقضی شده است. از منو دوباره شروع کنید.")
        return
    if step == "confirm":
        summary = (
            "پیش‌نمایش ایجاد مجموعه\n\n"
            f"نام: {data.get('tenant_name', '')}\n"
            f"شناسه: {data.get('tenant_slug', '')}\n"
            f"منطقه زمانی: {data.get('tenant_timezone', '')}\n"
            f"زبان: {data.get('tenant_locale', '')}\n\n"
            "برای ثبت نهایی تأیید کنید."
        )
        await message.answer(
            summary,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="تأیید و ایجاد",
                            callback_data=f"tenant:confirm:{flow_token}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="بازگشت",
                            callback_data=f"wizard:back:{flow_token}",
                        ),
                        InlineKeyboardButton(
                            text="لغو عملیات",
                            callback_data=f"wizard:cancel:{flow_token}",
                        ),
                    ],
                ]
            ),
        )
        return

    await message.answer(prompts[step], reply_markup=wizard_controls(flow_token))


async def _move_tenant_wizard_back(
    message: Message,
    state: FSMContext,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    current = await state.get_state()
    previous = {
        TenantCreation.slug.state: "name",
        TenantCreation.timezone.state: "slug",
        TenantCreation.locale.state: "timezone",
        TenantCreation.confirm.state: "locale",
    }
    if current not in previous:
        await state.clear()
        await message.answer(
            "به منوی اصلی برگشتید.",
            reply_markup=await main_menu(db, principal),
        )
        return
    step = previous[current]
    await state.set_state(getattr(TenantCreation, step))
    await _show_tenant_step(message, state, step)


@router.message(CommandStart())
async def start_command(
    message: Message,
    principal: TelegramPrincipal | None = None,
    pairing_candidate: bool = False,
    db: AsyncSession | None = None,
) -> None:
    if principal is not None and db is not None:
        await show_main_menu(message, principal, db)
        return

    if not pairing_candidate or db is None or message.from_user is None:
        await message.answer("دسترسی مدیریتی این حساب فعال نیست.")
        return

    parts = (message.text or "").strip().split(maxsplit=1)
    raw_token = parts[1] if len(parts) == 2 else ""
    try:
        account = await consume_admin_pairing(
            db,
            raw_token=raw_token,
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    except PairingRejected:
        await message.answer(
            "دعوت‌نامه معتبر نیست، منقضی شده یا برای حساب تلگرامی دیگری صادر شده است. "
            "از مالک سامانه دعوت‌نامه جدید بگیرید."
        )
        return

    user = await db.get(User, account.user_id)
    if user is None:
        await message.answer("اتصال حساب کامل نشد. با پشتیبانی سامانه تماس بگیرید.")
        return

    await message.answer("اتصال حساب تلگرامی با موفقیت انجام شد.")
    await show_main_menu(message, TelegramPrincipal(user=user, telegram_account=account), db)


@router.message(Command("menu"))
async def menu_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    await show_main_menu(message, principal, db)


@router.message(Command("back"))
async def back_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await _move_tenant_wizard_back(message, state, principal, db)


@router.message(Command("cancel"))
async def cancel_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(
        "عملیات مرحله‌ای جاری لغو شد. هیچ تغییری ثبت نشد.",
        reply_markup=await main_menu(db, principal),
    )


@router.message(Command("invite"))
async def invite_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) != 3:
        await message.answer(
            "قالب فرمان:\n/invite TELEGRAM_ID نام مدیر\n\n"
            "Telegram ID باید عددی باشد و نام مدیر را بعد از آن وارد کنید."
        )
        return

    raw_telegram_id, display_name = parts[1], parts[2]
    if not raw_telegram_id.isdigit():
        await message.answer("Telegram ID باید فقط شامل رقم باشد.")
        return

    telegram_user_id = int(raw_telegram_id)
    if telegram_user_id <= 0 or telegram_user_id > 9_223_372_036_854_775_807:
        await message.answer("Telegram ID در محدوده معتبر نیست.")
        return

    try:
        invitation = await create_admin_pairing(
            db,
            actor_id=principal.user.id,
            target_telegram_user_id=telegram_user_id,
            display_name=display_name,
        )
    except PairingNotAllowed:
        await message.answer("مجوز ایجاد دعوت‌نامه مدیریتی را ندارید.")
        return
    except ValueError as exc:
        await message.answer(str(exc))
        return

    bot_info = await message.bot.get_me()
    if not bot_info.username:
        await message.answer(
            "دعوت‌نامه ساخته شد، اما نام کاربری ربات تنظیم نشده است؛ "
            "لینک را از پشتیبانی فنی دریافت کنید."
        )
        return

    pairing_link = f"https://t.me/{bot_info.username}?start={invitation.raw_token}"
    expires = invitation.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    await message.answer(
        "دعوت‌نامه یک‌بارمصرف ساخته شد.\n\n"
        f"نام: {display_name}\n"
        f"Telegram ID مقصد: {telegram_user_id}\n"
        f"انقضا: {expires}\n\n"
        "این لینک را فقط برای همان شخص بفرستید:\n"
        f"{pairing_link}\n\n"
        "لینک پس از یک بار استفاده یا پایان زمان اعتبار، دیگر قابل استفاده نیست."
    )


@router.callback_query(F.data == "tenant:list")
async def tenant_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    try:
        tenants = await list_tenants(db, actor_id=principal.user.id, limit=50)
    except AccessDenied:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    if not tenants:
        body = "هنوز مجموعه‌ای در سامانه ثبت نشده است."
    else:
        lines = [
            f"{index}. {tenant.name} | {tenant.slug} | {tenant.status}"
            for index, tenant in enumerate(tenants, start=1)
        ]
        body = "فهرست مجموعه‌ها\n\n" + "\n".join(lines)
    if callback.message is not None:
        await callback.message.answer(body, reply_markup=await main_menu(db, principal))


@router.callback_query(F.data == "tenant:create")
async def tenant_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    allowed = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:tenants:create",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    if not allowed:
        await callback.answer("دسترسی ایجاد مجموعه را ندارید.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.update_data(flow_token=secrets.token_urlsafe(9))
    await state.set_state(TenantCreation.name)
    if callback.message is not None:
        await _show_tenant_step(callback.message, state, "name")


@router.message(TenantCreation.name)
async def tenant_name_step(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip()
    if not 2 <= len(value) <= 180:
        await message.answer("نام باید بین ۲ تا ۱۸۰ نویسه باشد. دوباره وارد کنید.")
        return
    await state.update_data(tenant_name=value)
    await state.set_state(TenantCreation.slug)
    await _show_tenant_step(message, state, "slug")


@router.message(TenantCreation.slug)
async def tenant_slug_step(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?", value):
        await message.answer(
            "شناسه نامعتبر است. فقط حروف کوچک انگلیسی، عدد و خط تیره مجاز است؛ "
            "ابتدا و انتهای شناسه باید حرف یا عدد باشد."
        )
        return
    await state.update_data(tenant_slug=value)
    await state.set_state(TenantCreation.timezone)
    await _show_tenant_step(message, state, "timezone")


@router.message(TenantCreation.timezone)
async def tenant_timezone_step(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip()
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        await message.answer("منطقه زمانی شناخته نشد. نمونه معتبر: Asia/Tehran")
        return
    await state.update_data(tenant_timezone=value)
    await state.set_state(TenantCreation.locale)
    await _show_tenant_step(message, state, "locale")


@router.message(TenantCreation.locale)
async def tenant_locale_step(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip()
    if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", value):
        await message.answer("کد زبان نامعتبر است؛ نمونه: fa-IR یا en-US")
        return
    await state.update_data(tenant_locale=value)
    await state.set_state(TenantCreation.confirm)
    await _show_tenant_step(message, state, "confirm")


@router.callback_query(F.data.startswith("tenant:confirm:"))
async def tenant_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").split(":", maxsplit=2)[-1]
    if (
        await state.get_state() != TenantCreation.confirm.state
        or not supplied_token
        or supplied_token != data.get("flow_token")
    ):
        await callback.answer(
            "این دکمه متعلق به فرم قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await callback.answer()
    try:
        tenant = await create_tenant(
            db,
            actor_id=principal.user.id,
            name=data.get("tenant_name", ""),
            slug=data.get("tenant_slug", ""),
            timezone=data.get("tenant_timezone", "Asia/Tehran"),
            locale=data.get("tenant_locale", "fa-IR"),
        )
    except AccessDenied:
        await state.clear()
        if callback.message is not None:
            await callback.message.answer("دسترسی ایجاد مجموعه را ندارید.")
        return
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if callback.message is not None:
        await callback.message.answer(
            "مجموعه با موفقیت ایجاد شد.\n\n"
            f"نام: {tenant.name}\n"
            f"شناسه: {tenant.slug}\n"
            f"شناسه داخلی: {tenant.id}",
            reply_markup=await main_menu(db, principal),
        )


@router.callback_query(F.data.startswith("wizard:back:"))
async def wizard_back_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    supplied_token = (callback.data or "").split(":", maxsplit=2)[-1]
    current_data = await state.get_data()
    if not supplied_token or supplied_token != current_data.get("flow_token"):
        await callback.answer(
            "این دکمه متعلق به فرم قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await callback.answer()
    if callback.message is None:
        return
    await _move_tenant_wizard_back(callback.message, state, principal, db)


@router.callback_query(F.data.startswith("wizard:cancel:"))
async def wizard_cancel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    supplied_token = (callback.data or "").split(":", maxsplit=2)[-1]
    current_data = await state.get_data()
    if not supplied_token or supplied_token != current_data.get("flow_token"):
        await callback.answer(
            "این دکمه متعلق به فرم قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await callback.answer()
    await state.clear()
    if callback.message is not None:
        await callback.message.answer(
            "عملیات لغو شد. هیچ تغییری ثبت نشد.",
            reply_markup=await main_menu(db, principal),
        )


@router.message(Command("help"))
async def help_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await message.answer(
        "راهنمای ربات مدیریتی\n\n"
        "/start — شروع و بررسی دسترسی یا پذیرش دعوت‌نامه\n"
        "/menu — منوی اصلی\n"
        "/back — بازگشت یک مرحله در فرم\n"
        "/cancel — لغو فرم جاری\n"
        "/invite TELEGRAM_ID نام — دعوت مدیر جدید (نیازمند مجوز)\n"
        "/help — راهنما\n"
        "/status — بررسی سلامت اتصال‌ها\n\n"
        "گزینه‌های منو بر اساس مجوز شما نمایش داده می‌شوند.",
        reply_markup=await main_menu(db, principal),
    )


@router.message(Command("status"))
async def status_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    redis: object,
) -> None:
    database_ok = False
    redis_ok = False
    try:
        await db.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False

    try:
        redis_ok = bool(await redis.ping())  # type: ignore[attr-defined]
    except Exception:
        redis_ok = False

    database_label = "فعال" if database_ok else "قطع"
    redis_label = "فعال" if redis_ok else "قطع"
    await message.answer(
        "وضعیت سرویس‌ها\n\n"
        f"Database: {database_label}\n"
        f"Redis: {redis_label}\n"
        "ربات: فعال",
    )


@router.callback_query(F.data == "account:me")
async def account_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    account = principal.telegram_account
    await callback.answer()
    if callback.message is None:
        return
    await callback.message.answer(
        "حساب مدیریتی\n\n"
        f"نام: {principal.user.display_name}\n"
        f"شناسه کاربر سامانه: {principal.user.id}\n"
        f"Telegram ID: {account.telegram_user_id}\n"
        f"وضعیت حساب: {principal.user.status}\n"
        f"وضعیت اتصال تلگرام: {account.status}",
        reply_markup=await main_menu(db, principal),
    )


@router.callback_query(F.data == "help:show")
async def help_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("برای راهنمای فرمان‌ها از /help استفاده کنید.")


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer(
        "این دکمه معتبر نیست یا منقضی شده است. از /menu شروع کنید.",
        show_alert=True,
    )
