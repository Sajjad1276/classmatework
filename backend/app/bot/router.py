from __future__ import annotations

import secrets
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    ApplicationError,
)
from app.application.tenant_access import (
    create_tenant_role,
    list_admin_invitations,
    list_platform_roles,
    list_tenant_members_page,
    list_tenant_roles,
    preview_tenant_member_reactivation,
    preview_tenant_member_suspension,
    reactivate_tenant_member,
    suspend_tenant_member,
)
from app.application.tenants import create_tenant
from app.db.models import User
from app.modules.access.policy import has_permission
from app.modules.identity.pairing import (
    PairingNotAllowed,
    PairingRejected,
    consume_admin_pairing,
    create_admin_pairing,
    revoke_admin_pairing,
)
from app.modules.identity.principal import TelegramPrincipal

router = Router(name="admin")



class TenantCreation(StatesGroup):
    name = State()
    confirm = State()

class MemberSuspension(StatesGroup):
    reason = State()
    confirm = State()


class MemberReactivation(StatesGroup):
    confirm = State()


class MemberDirectory(StatesGroup):
    search = State()



def wizard_controls(flow_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"wizard:back:{flow_token}")],
            [InlineKeyboardButton(text="❌ لغو عملیات", callback_data=f"wizard:cancel:{flow_token}")],
        ]
    )

MAIN_MENU_ACCOUNT = "👤 حساب مدیریتی من"
MAIN_MENU_SCHOOLS = "🏫 مدیریت مدارس و کلاس‌ها"
MAIN_MENU_HELP = "❓ راهنما"
MAIN_MENU_REPORTS = "📊 داشبورد و گزارش‌ها"


async def main_menu(
    db: AsyncSession,
    principal: TelegramPrincipal,
) -> ReplyKeyboardMarkup:
    """Persistent reply keyboard for the root screen only.

    Every operational screen uses an inline keyboard; the root keyboard is
    removed on entry to those screens and restored only when returning home.
    """
    can_read_schools = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:schools:read",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    can_read_reports = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:reports:read",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )

    first_row = [KeyboardButton(text=MAIN_MENU_ACCOUNT)]
    if can_read_schools:
        first_row.append(KeyboardButton(text=MAIN_MENU_SCHOOLS))

    second_row = []
    if can_read_reports:
        second_row.append(KeyboardButton(text=MAIN_MENU_REPORTS))
    second_row.append(KeyboardButton(text=MAIN_MENU_HELP))

    keyboard = [first_row]
    if second_row:
        keyboard.append(second_row)

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="یکی از بخش‌های مدیریت را انتخاب کنید",
    )


async def _hide_root_keyboard(message: Message) -> None:
    """Remove the persistent root keyboard before showing an inline panel."""
    removal_message = await message.answer("·", reply_markup=ReplyKeyboardRemove())
    try:
        await removal_message.delete()
    except TelegramBadRequest:
        # The keyboard removal has already been sent; failure to delete the
        # invisible carrier message must not block navigation.
        pass



async def _show_tenant_list(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    """Compatibility fallback for stale keyboards; tenant/year navigation is retired."""
    await message.answer(
        "مدیریت مجموعه و سال تحصیلی از پنل حذف شده است. "
        "مدرسه و کلاس را مستقیماً از بخش مدیریت مدارس انتخاب کنید.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏫 ورود به مدیریت مدارس",
                        callback_data="product:schools",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")],
            ]
        ),
    )


async def _begin_tenant_creation(
    message: Message,
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
        await message.answer(
            "دسترسی ایجاد مجموعه را ندارید.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="خانه", callback_data="root:menu")]
                ]
            ),
        )
        return
    await state.clear()
    await state.update_data(flow_token=secrets.token_urlsafe(9))
    await state.set_state(TenantCreation.name)
    await _show_tenant_step(message, state, "name")



async def _show_account_panel(
    message: Message,
    principal: TelegramPrincipal,
) -> None:
    account = principal.telegram_account
    await message.answer(
        "حساب مدیریتی\n\n"
        f"نام: {principal.user.display_name}\n"
        f"شناسه کاربر سامانه: {principal.user.id}\n"
        f"Telegram ID: {account.telegram_user_id}\n"
        f"وضعیت حساب: {principal.user.status}\n"
        f"وضعیت اتصال تلگرام: {account.status}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")]
            ]
        ),
    )


async def _show_help_panel(message: Message) -> None:
    await message.answer(
        "راهنمای ClassMate Work\n\n"
        "برای مدیریت مدارس، کلاس‌ها، درس‌ها، اعضا و برنامه هفتگی، "
        "وارد «مدیریت مدارس و کلاس‌ها» شوید.\n\n"
        "در پنل‌های داخلی فقط دکمه «بازگشت» برای رفتن به سطح قبلی وجود دارد. "
        "فرم‌ها کدهای سیستمی را خودکار می‌سازند و اطلاعات قابل پیش‌فرض را از شما نمی‌پرسند.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")]
            ]
        ),
    )

async def show_main_menu(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await message.answer(
        f"ClassMate Work — مرکز مدیریت\n\n"
        f"درود {principal.user.display_name}.\n"
        "گزینه‌های اصلی را از کیبورد پایین انتخاب کنید. پنل‌های داخلی با دکمه‌های شیشه‌ای باز می‌شوند.",
        reply_markup=await main_menu(db, principal),
    )


@router.message(
    F.text.in_(
        {
            MAIN_MENU_ACCOUNT,
            MAIN_MENU_SCHOOLS,
            MAIN_MENU_HELP,
        }
    )
)
async def root_menu_button_handler(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Route root reply-keyboard selections, including selections during a wizard."""
    selected = (message.text or "").strip()
    await state.clear()
    await _hide_root_keyboard(message)

    if selected == MAIN_MENU_ACCOUNT:
        await _show_account_panel(message, principal)
    elif selected == MAIN_MENU_SCHOOLS:
        await _show_tenant_list(message, principal, db)
    elif selected == MAIN_MENU_HELP:
        await _show_help_panel(message)


@router.callback_query(F.data == "root:menu")
async def root_menu_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    await callback.answer()
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            "به منوی اصلی برگشتید.",
            reply_markup=await main_menu(db, principal),
        )



async def _show_tenant_step(
    message: Message,
    state: FSMContext,
    step: str,
) -> None:
    data = await state.get_data()
    flow_token = data.get("flow_token", "")
    if not flow_token:
        await state.clear()
        await message.answer("فرم منقضی شده است. از منو دوباره شروع کنید.")
        return

    if step == "confirm":
        body = (
            "پیش‌نمایش ایجاد مجموعه\n\n"
            f"نام مجموعه: {data.get('tenant_name', '')}\n"
            f"شناسه سیستمی: {data.get('tenant_slug', '')}\n"
            "منطقه زمانی پیش‌فرض: Asia/Tehran\n"
            "زبان پیش‌فرض: fa-IR\n\n"
            "برای ثبت نهایی تأیید کنید."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ تأیید و ایجاد",
                    callback_data=f"tenant:confirm:{flow_token}",
                )],
                [InlineKeyboardButton(
                    text="⬅️ بازگشت",
                    callback_data=f"wizard:back:{flow_token}",
                )],
                [InlineKeyboardButton(
                    text="❌ لغو عملیات",
                    callback_data=f"wizard:cancel:{flow_token}",
                )],
            ]
        )
    else:
        body = "نام مجموعه را وارد کنید (۲ تا ۱۸۰ نویسه):"
        keyboard = wizard_controls(flow_token)

    prompt_id = data.get("flow_prompt_message_id")
    chat_id = data.get("flow_chat_id") or message.chat.id
    if prompt_id:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=prompt_id,
                text=body,
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest:
            pass

    sent = await message.answer(body, reply_markup=keyboard)
    await state.update_data(
        flow_prompt_message_id=sent.message_id,
        flow_chat_id=sent.chat.id,
    )



async def _move_tenant_wizard_back(
    message: Message,
    state: FSMContext,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    current = await state.get_state()
    if current == TenantCreation.confirm.state:
        await state.set_state(TenantCreation.name)
        await _show_tenant_step(message, state, "name")
        return

    await state.clear()
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await message.answer(
        "به منوی اصلی برگشتید.",
        reply_markup=await main_menu(db, principal),
    )

@router.message(CommandStart())
async def start_command(
    message: Message,
    principal: TelegramPrincipal | None = None,
    pairing_candidate: bool = False,
    db: AsyncSession | None = None,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
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


async def _member_directory_content(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    page_number: int,
    search_query: str | None,
    flow_token: str,
) -> tuple[str, InlineKeyboardMarkup]:
    result = await list_tenant_members_page(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        page=page_number,
        page_size=8,
        search_query=search_query,
    )
    can_manage_members = await has_permission(
        db,
        user_id=actor_id,
        permission_key="membership:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    member_action_rows: list[list[InlineKeyboardButton]] = []
    if result.items:
        first_index = (result.page - 1) * result.page_size + 1
        last_index = first_index + len(result.items) - 1
        lines = [
            "👥 اعضای فعال مجموعه",
            f"نمایش {first_index} تا {last_index} از {result.total_items}",
            f"صفحه {result.page} از {max(result.total_pages, 1)}",
        ]
        if result.search_query:
            lines.append(f"جست‌وجو: {result.search_query}")
        lines.append("")
        for index, member in enumerate(result.items, start=first_index):
            roles = ", ".join(member.role_keys) or "بدون نقش فعال"
            telegram_label = (
                str(member.telegram_user_id)
                if member.telegram_user_id is not None
                else "اتصال تلگرام فعال ندارد"
            )
            lines.append(
                f"{index}. {member.display_name}\n"
                f"شناسه سامانه: {member.user_id}\n"
                f"Telegram ID: {telegram_label}\n"
                f"نقش‌ها: {roles}"
            )
            if can_manage_members:
                member_action_rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"⛔ تعلیق {member.display_name[:24]}",
                            callback_data=_member_suspend_callback_data(
                                flow_token,
                                member.user_id,
                            ),
                        )
                    ]
                )
        body = "\n\n".join(lines)
    else:
        body = "هیچ عضو فعالی با این مشخصات پیدا نشد."
        if result.search_query:
            body += f"\nعبارت جست‌وجو: {result.search_query}"
        else:
            body += "\nاین مجموعه عضو فعال ندارد."
        body += f"\n\nتعداد نتایج: {result.total_items}"

    rows: list[list[InlineKeyboardButton]] = member_action_rows
    if result.total_pages > 1:
        navigation = []
        if result.page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ قبلی",
                    callback_data=f"member:list:page:{flow_token}:{result.page - 1}",
                )
            )
        if result.page < result.total_pages:
            navigation.append(
                InlineKeyboardButton(
                    text="بعدی ➡️",
                    callback_data=f"member:list:page:{flow_token}:{result.page + 1}",
                )
            )
        if navigation:
            rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔎 جست‌وجو" if not result.search_query else "🔎 تغییر جست‌وجو",
                callback_data=f"member:list:search:{flow_token}",
            )
        ]
    )
    if result.search_query:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧹 حذف جست‌وجو",
                    callback_data=f"member:list:clear:{flow_token}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ بازگشت به مدارس", callback_data="product:schools")]
    )
    return body, InlineKeyboardMarkup(inline_keyboard=rows)



def _member_suspend_callback_data(flow_token: str, member_user_id: UUID) -> str:
    # Keep this callback under Telegram's 64-byte limit (UUID hex = 32 bytes).
    callback_data = f"m:s:start:{flow_token}:{member_user_id.hex}"
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Member action callback exceeds Telegram's 64-byte limit.")
    return callback_data


async def _restore_member_directory_after_suspend(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    data: dict,
    *,
    notice: str,
) -> bool:
    if not data.get("return_member_directory") or callback.message is None:
        return False

    try:
        tenant_id = UUID(data["return_member_directory_tenant_id"])
        search_query = data.get("return_member_directory_query")
        flow_token = secrets.token_urlsafe(6)
        body, keyboard = await _member_directory_content(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            page_number=1,
            search_query=search_query,
            flow_token=flow_token,
        )
    except (ValueError, KeyError, ApplicationError, AccessDenied):
        return False

    await state.clear()
    await state.update_data(
        flow_name="member_directory",
        flow_token=flow_token,
        member_directory_tenant_id=str(tenant_id),
        member_directory_query=search_query,
    )
    await state.set_state(None)
    await callback.message.edit_text(
        f"{notice}\n\n{body}",
        reply_markup=keyboard,
    )
    return True


@router.callback_query(F.data.startswith("m:s:start:"))
async def member_suspend_start_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    supplied_token = parts[3] if len(parts) == 5 else ""
    if len(parts) != 5 or parts[0:3] != ["m", "s", "start"]:
        await callback.answer("درخواست تعلیق معتبر نیست.", show_alert=True)
        return

    data = await state.get_data()
    if (
        data.get("flow_name") != "member_directory"
        or not supplied_token
        or supplied_token != data.get("flow_token")
    ):
        await callback.answer("این فهرست دیگر فعال نیست.", show_alert=True)
        return

    try:
        tenant_id = UUID(data["member_directory_tenant_id"])
        member_user_id = UUID(hex=parts[4])
        preview = await preview_tenant_member_suspension(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_user_id,
        )
    except AccessDenied:
        await callback.answer("مجوز مدیریت عضویت‌های این مجموعه را ندارید.", show_alert=True)
        return
    except (ValueError, KeyError, ApplicationError) as exc:
        await callback.answer(str(exc) or "عضو انتخاب‌شده معتبر نیست.", show_alert=True)
        return

    suspend_token = secrets.token_urlsafe(9)
    await state.update_data(
        flow_name="member_suspend",
        flow_token=suspend_token,
        suspend_tenant_id=str(tenant_id),
        suspend_member_user_id=str(member_user_id),
        return_member_directory=True,
        return_member_directory_tenant_id=str(tenant_id),
        return_member_directory_query=data.get("member_directory_query"),
    )
    await state.set_state(MemberSuspension.reason)
    await callback.answer()
    role_warning = (
        "\nاین کاربر مدیر مجموعه است؛ آخرین مدیر فعال قابل تعلیق نیست."
        if preview.is_tenant_admin
        else ""
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "⛔ تعلیق عضویت\n\n"
            f"عضو: {preview.display_name}\n"
            f"شناسه سامانه: {preview.user_id}"
            f"{role_warning}\n\n"
            "دلیل تعلیق را در پیام بعدی وارد کنید. دلیل باید بین ۱۰ تا ۵۰۰ نویسه باشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت بدون تغییر",
                            callback_data=f"member:suspend:cancel:{suspend_token}",
                        )
                    ]
                ]
            ),
        )


@router.message(MemberSuspension.reason, F.text)
async def member_suspend_reason_input(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if data.get("flow_name") != "member_suspend":
        await state.clear()
        await message.answer("درخواست تعلیق منقضی شده است؛ دوباره فهرست اعضا را باز کنید.")
        return

    reason = (message.text or "").strip()
    if not 10 <= len(reason) <= 500:
        await message.answer("دلیل باید بین ۱۰ تا ۵۰۰ نویسه باشد. دوباره وارد کنید.")
        return

    try:
        tenant_id = UUID(data["suspend_tenant_id"])
        member_user_id = UUID(data["suspend_member_user_id"])
        preview = await preview_tenant_member_suspension(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_user_id,
        )
    except AccessDenied:
        await state.clear()
        await message.answer("مجوز تعلیق عضویت را ندارید.")
        return
    except (ValueError, KeyError, ApplicationError) as exc:
        await state.clear()
        await message.answer(str(exc) or "عضو انتخاب‌شده دیگر قابل تعلیق نیست.")
        return

    await state.update_data(suspend_reason=reason)
    await state.set_state(MemberSuspension.confirm)
    role_warning = (
        "\nاین کاربر مدیر مجموعه است؛ امکان تعلیق آخرین مدیر فعال وجود ندارد."
        if preview.is_tenant_admin
        else ""
    )
    flow_token = data["flow_token"]
    await message.answer(
        "پیش‌نمایش تعلیق عضویت\n\n"
        f"نام عضو: {preview.display_name}\n"
        f"شناسه سامانه: {preview.user_id}\n"
        f"مجموعه: {tenant_id}\n"
        f"دلیل: {reason}"
        f"{role_warning}\n\n"
        "فقط با تأیید دکمه زیر عضویت این کاربر در همین مجموعه پایان می‌یابد.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="تأیید تعلیق",
                        callback_data=f"member:suspend:confirm:{flow_token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="لغو",
                        callback_data=f"member:suspend:cancel:{flow_token}",
                    )
                ],
            ]
        ),
    )



@router.callback_query(F.data.startswith("member:directory:"))
async def member_directory_open_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    raw_tenant_id = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    try:
        tenant_id = UUID(raw_tenant_id)
        flow_token = secrets.token_urlsafe(6)
        body, keyboard = await _member_directory_content(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            page_number=1,
            search_query=None,
            flow_token=flow_token,
        )
    except AccessDenied:
        await callback.answer(
            "مجوز مشاهده اعضای این مجموعه را ندارید.",
            show_alert=True,
        )
        return
    except (ValueError, ApplicationError) as exc:
        await callback.answer(
            str(exc) or "شناسه مجموعه معتبر نیست.",
            show_alert=True,
        )
        return

    await state.update_data(
        flow_name="member_directory",
        flow_token=flow_token,
        member_directory_tenant_id=str(tenant_id),
        member_directory_query=None,
    )
    await state.set_state(None)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(body, reply_markup=keyboard)


@router.callback_query(F.data.startswith("member:list:page:"))
async def member_directory_page_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    parts = (callback.data or "").split(":")
    supplied_token = parts[3] if len(parts) == 5 else ""
    if (
        data.get("flow_name") != "member_directory"
        or not supplied_token
        or supplied_token != data.get("flow_token")
    ):
        await callback.answer(
            "این صفحه متعلق به فهرست قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    try:
        tenant_id = UUID(data["member_directory_tenant_id"])
        page_number = int(parts[4])
        body, keyboard = await _member_directory_content(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            page_number=page_number,
            search_query=data.get("member_directory_query"),
            flow_token=supplied_token,
        )
    except (ValueError, KeyError, ApplicationError) as exc:
        await callback.answer(str(exc) or "درخواست صفحه معتبر نیست.", show_alert=True)
        return
    except AccessDenied:
        await state.clear()
        await callback.answer("دسترسی مشاهده اعضای این مجموعه را ندارید.", show_alert=True)
        return

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(body, reply_markup=keyboard)


@router.callback_query(F.data.startswith("member:list:search:"))
async def member_directory_search_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        data.get("flow_name") != "member_directory"
        or not supplied_token
        or supplied_token != data.get("flow_token")
    ):
        await callback.answer("این فهرست دیگر فعال نیست.", show_alert=True)
        return
    await state.set_state(MemberDirectory.search)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "نام عضو یا Telegram ID دقیق را وارد کنید.\n"
            "برای لغو جست‌وجو /cancel را بزنید."
        )


@router.message(MemberDirectory.search, F.text)
async def member_directory_search_input(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    search_query = (message.text or "").strip()
    if not search_query:
        await message.answer("عبارت جست‌وجو خالی است؛ نام یا Telegram ID را وارد کنید.")
        return
    if search_query.startswith("/"):
        await message.answer("عبارت جست‌وجو معتبر نیست؛ برای خروج از /cancel استفاده کنید.")
        return

    data = await state.get_data()
    if (
        data.get("flow_name") != "member_directory"
        or not data.get("flow_token")
        or not data.get("member_directory_tenant_id")
    ):
        await state.clear()
        await message.answer("فهرست اعضا منقضی شده است. دوباره /members را اجرا کنید.")
        return
    try:
        tenant_id = UUID(data["member_directory_tenant_id"])
        body, keyboard = await _member_directory_content(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            page_number=1,
            search_query=search_query,
            flow_token=data["flow_token"],
        )
    except (ValueError, KeyError, ApplicationError) as exc:
        await message.answer(str(exc) or "عبارت جست‌وجو معتبر نیست.")
        return
    except AccessDenied:
        await state.clear()
        await message.answer("مجوز مشاهده اعضای این مجموعه را ندارید.")
        return

    await state.update_data(member_directory_query=search_query)
    await state.set_state(None)
    await message.answer(body, reply_markup=keyboard)


@router.callback_query(F.data.startswith("member:list:clear:"))
async def member_directory_clear_search_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        data.get("flow_name") != "member_directory"
        or not supplied_token
        or supplied_token != data.get("flow_token")
    ):
        await callback.answer("این فهرست دیگر فعال نیست.", show_alert=True)
        return
    try:
        tenant_id = UUID(data["member_directory_tenant_id"])
        body, keyboard = await _member_directory_content(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            page_number=1,
            search_query=None,
            flow_token=supplied_token,
        )
    except (ValueError, KeyError, ApplicationError) as exc:
        await callback.answer(str(exc) or "درخواست معتبر نیست.", show_alert=True)
        return
    except AccessDenied:
        await state.clear()
        await callback.answer("مجوز مشاهده اعضای این مجموعه را ندارید.", show_alert=True)
        return

    await state.update_data(member_directory_query=None)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(body, reply_markup=keyboard)


@router.callback_query(F.data.startswith("member:suspend:confirm:"))
async def member_suspend_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberSuspension.confirm.state
        or not supplied_token
        or supplied_token != data.get("flow_token")
        or data.get("flow_name") != "member_suspend"
    ):
        await callback.answer(
            "این تأیید متعلق به درخواست قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return

    try:
        tenant_id = UUID(data["suspend_tenant_id"])
        member_user_id = UUID(data["suspend_member_user_id"])
        result = await suspend_tenant_member(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_user_id,
            reason=data.get("suspend_reason", ""),
        )
    except (ValueError, ApplicationError) as exc:
        await state.clear()
        await callback.answer(str(exc), show_alert=True)
        return
    except AccessDenied:
        await state.clear()
        await callback.answer("مجوز تعلیق عضویت را ندارید.", show_alert=True)
        return

    await state.clear()
    await callback.answer("عضویت تعلیق شد.")
    restored = await _restore_member_directory_after_suspend(
        callback,
        principal,
        db,
        state,
        data,
        notice=f"عضویت {result.display_name} تعلیق شد.",
    )
    if not restored and callback.message is not None:
        await callback.message.answer(
            "عضویت با موفقیت تعلیق شد.\n\n"
            f"عضو: {result.display_name}\n"
            f"Tenant: {result.tenant_id}\n"
            f"RoleBindingهای پایان‌یافته: {result.ended_role_bindings}\n"
            f"زمان: {result.suspended_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"دلیل: {result.reason}"
        )


@router.callback_query(F.data.startswith("member:suspend:cancel:"))
async def member_suspend_cancel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() not in {
            MemberSuspension.reason.state,
            MemberSuspension.confirm.state,
        }
        or not supplied_token
        or supplied_token != data.get("flow_token")
        or data.get("flow_name") != "member_suspend"
    ):
        await callback.answer(
            "این دکمه متعلق به درخواست قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await state.clear()
    await callback.answer("تعلیق لغو شد.")
    restored = await _restore_member_directory_after_suspend(
        callback,
        principal,
        db,
        state,
        data,
        notice="درخواست تعلیق لغو شد؛ تغییری اعمال نشد.",
    )
    if not restored and callback.message is not None:
        await callback.message.answer("درخواست تعلیق لغو شد؛ تغییری در عضویت انجام نشد.")


@router.callback_query(F.data.startswith("member:reactivate:confirm:"))
async def member_reactivate_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberReactivation.confirm.state
        or not supplied_token
        or supplied_token != data.get("flow_token")
        or data.get("flow_name") != "member_reactivate"
    ):
        await callback.answer(
            "این تأیید متعلق به درخواست قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return

    try:
        tenant_id = UUID(data["reactivate_tenant_id"])
        member_user_id = UUID(data["reactivate_member_user_id"])
        result = await reactivate_tenant_member(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_user_id,
            role_key=data.get("reactivate_role_key", ""),
            reason=data.get("reactivate_reason", ""),
        )
    except AccessDenied:
        await state.clear()
        await callback.answer(
            "مجوز فعال‌سازی مجدد یا واگذاری نقش را ندارید.",
            show_alert=True,
        )
        return
    except (ValueError, ApplicationError) as exc:
        await state.clear()
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await callback.answer("عضویت دوباره فعال شد.")
    if callback.message is not None:
        await callback.message.answer(
            "عضویت با موفقیت دوباره فعال شد.\n\n"
            f"عضو: {result.display_name}\n"
            f"Tenant: {result.tenant_id}\n"
            f"نقش جدید: {result.role_key}\n"
            f"زمان: {result.reactivated_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"دلیل: {data.get('reactivate_reason', '')}"
        )


@router.callback_query(F.data.startswith("member:reactivate:cancel:"))
async def member_reactivate_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberReactivation.confirm.state
        or not supplied_token
        or supplied_token != data.get("flow_token")
        or data.get("flow_name") != "member_reactivate"
    ):
        await callback.answer(
            "این دکمه متعلق به درخواست قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await state.clear()
    await callback.answer("فعال‌سازی لغو شد.")
    if callback.message is not None:
        await callback.message.answer(
            "درخواست فعال‌سازی مجدد لغو شد؛ تغییری در عضویت انجام نشد."
        )


@router.callback_query(F.data == "tenant:list")
async def tenant_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _hide_root_keyboard(callback.message)
        await _show_tenant_list(callback.message, principal, db)


@router.callback_query(F.data == "tenant:create")
async def tenant_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _begin_tenant_creation(callback.message, principal, db, state)



@router.message(TenantCreation.name)
async def tenant_name_step(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip()
    if not 2 <= len(value) <= 180:
        await message.answer("نام مجموعه باید بین ۲ تا ۱۸۰ نویسه باشد. دوباره وارد کنید.")
        return
    await state.update_data(
        tenant_name=value,
        tenant_slug=f"tenant-{secrets.token_hex(8)}",
        tenant_timezone="Asia/Tehran",
        tenant_locale="fa-IR",
    )
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
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
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

    try:
        await create_tenant(
            db,
            actor_id=principal.user.id,
            name=data.get("tenant_name", ""),
            slug=data.get("tenant_slug", ""),
            timezone="Asia/Tehran",
            locale="fa-IR",
        )
    except AccessDenied:
        await state.clear()
        await callback.answer("دسترسی ایجاد مجموعه را ندارید.", show_alert=True)
        return
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await callback.answer("مجموعه ایجاد شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            (
                "عملیات قبلی ثبت شد.\n\n"
                "برای مدیریت مدرسه و کلاس‌ها از بخش مدیریت مدارس استفاده کنید."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🏫 مدیریت مدارس",
                        callback_data="product:schools",
                    )],
                    [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")],
                ]
            ),
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
    supplied_token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    current_data = await state.get_data()
    if not supplied_token or supplied_token != current_data.get("flow_token"):
        await callback.answer(
            "این دکمه متعلق به فرم قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("عملیات لغو شد؛ هیچ تغییری ثبت نشد.")
        await callback.message.answer(
            "به منوی اصلی برگشتید.",
            reply_markup=await main_menu(db, principal),
        )

@router.callback_query(F.data == "account:me")
async def account_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _hide_root_keyboard(callback.message)
        await _show_account_panel(callback.message, principal)


@router.callback_query(F.data == "help:show")
async def help_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await _hide_root_keyboard(callback.message)
        await _show_help_panel(callback.message)


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer(
        "این دکمه معتبر نیست یا منقضی شده است. از /menu شروع کنید.",
        show_alert=True,
    )
