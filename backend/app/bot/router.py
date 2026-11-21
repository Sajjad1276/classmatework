from datetime import UTC
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.pairing import (
    PairingNotAllowed,
    PairingRejected,
    consume_admin_pairing,
    create_admin_pairing,
)
from app.modules.identity.principal import TelegramPrincipal
from app.db.models import User

router = Router(name="admin")


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="حساب مدیریتی من",
                    callback_data="account:me",
                )
            ],
            [
                InlineKeyboardButton(
                    text="راهنما",
                    callback_data="help:show",
                )
            ],
        ]
    )


async def show_main_menu(message: Message, principal: TelegramPrincipal) -> None:
    await message.answer(
        f"**ClassMate Work — مرکز مدیریت**\n\n"
        f"درود {principal.user.display_name}.\n"
        "هویت تلگرامی شما تأیید شده است. قابلیت‌های مدیریتی پس از تکمیل و آزمون "
        "در همین منو فعال می‌شوند.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


@router.message(CommandStart())
async def start_command(
    message: Message,
    principal: TelegramPrincipal | None = None,
    pairing_candidate: bool = False,
    db: AsyncSession | None = None,
) -> None:
    if principal is not None:
        await show_main_menu(message, principal)
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
    await show_main_menu(message, TelegramPrincipal(user=user, telegram_account=account))


@router.message(Command("menu"))
async def menu_command(
    message: Message,
    principal: TelegramPrincipal,
    state: FSMContext,
) -> None:
    await state.clear()
    await show_main_menu(message, principal)


@router.message(Command("back"))
async def back_command(
    message: Message,
    principal: TelegramPrincipal,
    state: FSMContext,
) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "در حال حاضر در منوی اصلی هستید.",
            reply_markup=main_menu(),
        )
        return
    await message.answer(
        "در این مرحله هنوز صفحه فرعی تعریف نشده است؛ برای خروج از فرم از /cancel استفاده کنید.",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cancel_command(
    message: Message,
    principal: TelegramPrincipal,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(
        "عملیات مرحله‌ای جاری لغو شد. هیچ تغییری ثبت نشد.",
        reply_markup=main_menu(),
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
    expires = invitation.expires_at.astimezone(ZoneInfo("Asia/Tehran")).strftime(
        "%Y-%m-%d %H:%M"
    )
    await message.answer(
        "دعوت‌نامه یک‌بارمصرف ساخته شد.\n\n"
        f"نام: {display_name}\n"
        f"Telegram ID مقصد: {telegram_user_id}\n"
        f"انقضا به وقت تهران: {expires}\n\n"
        "این لینک را فقط برای همان شخص بفرستید:\n"
        f"{pairing_link}\n\n"
        "لینک پس از یک بار استفاده یا پایان زمان اعتبار، دیگر قابل استفاده نیست."
    )


@router.message(Command("help"))
async def help_command(
    message: Message,
    principal: TelegramPrincipal,
) -> None:
    await message.answer(
        "**راهنمای ربات مدیریتی**\n\n"
        "/start — شروع و بررسی دسترسی یا پذیرش دعوت‌نامه\n"
        "/menu — منوی اصلی\n"
        "/back — بازگشت در Flow جاری\n"
        "/cancel — لغو فرم جاری\n"
        "/invite TELEGRAM_ID نام — دعوت مدیر جدید (نیازمند مجوز)\n"
        "/help — راهنما\n"
        "/status — بررسی سلامت اتصال‌ها\n\n"
        "در این نسخه فقط هویت، دعوت‌نامه و وضعیت سرویس‌ها فعال‌اند؛ "
        "گزینه‌های عملیاتی تا تکمیل Use Case و تست مربوطه نمایش داده نمی‌شوند.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
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
        "**وضعیت سرویس‌ها**\n\n"
        f"Database: {database_label}\n"
        f"Redis: {redis_label}\n"
        "ربات: فعال",
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "account:me")
async def account_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
) -> None:
    account = principal.telegram_account
    await callback.answer()
    if callback.message is None:
        return
    await callback.message.answer(
        "**حساب مدیریتی**\n\n"
        f"نام: {principal.user.display_name}\n"
        f"شناسه کاربر سامانه: {principal.user.id}\n"
        f"Telegram ID: {account.telegram_user_id}\n"
        f"وضعیت حساب: {principal.user.status}\n"
        f"وضعیت اتصال تلگرام: {account.status}",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "help:show")
async def help_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "برای راهنمای فرمان‌ها از /help استفاده کنید.",
            reply_markup=main_menu(),
        )


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer(
        "این دکمه معتبر نیست یا منقضی شده است. از /menu شروع کنید.",
        show_alert=True,
    )
