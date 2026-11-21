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

from app.modules.identity.principal import TelegramPrincipal

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
    principal: TelegramPrincipal,
) -> None:
    await show_main_menu(message, principal)


@router.message(Command("menu"))
async def menu_command(
    message: Message,
    principal: TelegramPrincipal,
) -> None:
    await show_main_menu(message, principal)


@router.message(Command("back"))
async def back_command(
    message: Message,
    principal: TelegramPrincipal,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(
        "به منوی اصلی برگشتید.",
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


@router.message(Command("help"))
async def help_command(
    message: Message,
    principal: TelegramPrincipal,
) -> None:
    await message.answer(
        "**راهنمای ربات مدیریتی**\n\n"
        "/start — آغاز و بررسی دسترسی\n"
        "/menu — منوی اصلی\n"
        "/back — بازگشت یک مرحله\n"
        "/cancel — لغو فرم جاری\n"
        "/help — راهنما\n"
        "/status — بررسی سلامت اتصال‌ها\n\n"
        "در این نسخه فقط بخش هویت مدیریتی فعال است؛ گزینه‌های عملیاتی تا تکمیل "
        "Use Case و تست مربوطه نمایش داده نمی‌شوند.",
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
