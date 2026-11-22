from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
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
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    ApplicationError,
)
from app.application.tenant_access import (
    create_tenant_role,
    list_admin_invitations,
    list_platform_roles,
    list_tenant_members,
    list_tenant_roles,
    preview_tenant_member_reactivation,
    preview_tenant_member_suspension,
    reactivate_tenant_member,
    suspend_tenant_member,
)
from app.application.tenants import (
    create_tenant,
    list_accessible_tenants,
    list_tenants,
)
from app.db.models import TenantMembership, User
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
    confirm = State()


class MemberReactivation(StatesGroup):
    confirm = State()



def wizard_controls(flow_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"wizard:back:{flow_token}")],
            [InlineKeyboardButton(text="❌ لغو عملیات", callback_data=f"wizard:cancel:{flow_token}")],
        ]
    )

MAIN_MENU_ACCOUNT = "👤 حساب مدیریتی من"
MAIN_MENU_TENANTS = "🏫 مدیریت مدارس و کلاس‌ها"
MAIN_MENU_CREATE_TENANT = "➕ ایجاد مجموعه جدید"
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
    now = datetime.now(UTC)
    can_read_reports = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:reports:read",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    has_active_membership = (
        await db.execute(
            select(TenantMembership.id)
            .where(
                TenantMembership.user_id == principal.user.id,
                TenantMembership.status == "active",
                or_(TenantMembership.starts_at.is_(None), TenantMembership.starts_at <= now),
                or_(TenantMembership.ends_at.is_(None), TenantMembership.ends_at > now),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None

    first_row = [KeyboardButton(text=MAIN_MENU_ACCOUNT)]
    if can_list_tenants or has_active_membership:
        first_row.append(KeyboardButton(text=MAIN_MENU_TENANTS))

    second_row = []
    if can_read_reports:
        second_row.append(KeyboardButton(text=MAIN_MENU_REPORTS))
    if can_create_tenants:
        second_row.append(KeyboardButton(text=MAIN_MENU_CREATE_TENANT))
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
    try:
        tenants = await list_tenants(db, actor_id=principal.user.id, limit=50)
    except AccessDenied:
        tenants = await list_accessible_tenants(
            db,
            actor_id=principal.user.id,
            limit=50,
        )

    can_create = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="platform:tenants:create",
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )
    rows = [
        [InlineKeyboardButton(
            text=f"{tenant.name} · {tenant.slug}",
            callback_data=f"tenant:open:{tenant.id}",
        )]
        for tenant in tenants
    ]
    if can_create:
        rows.append([InlineKeyboardButton(text="➕ ایجاد مجموعه", callback_data="tenant:create")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")])

    body = (
        f"🏫 مدیریت مدارس و کلاس‌ها\n\nمجموعه‌ها: {len(tenants)}\n"
        "برای مدیریت مدارس و کلاس‌های هر مجموعه، آن را انتخاب کنید."
        if tenants
        else "هنوز مجموعه‌ای ثبت نشده است. ابتدا یک مجموعه ایجاد کنید."
    )
    await message.answer(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

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
        "برای مدیریت مدارس و کلاس‌ها، وارد «مدیریت مدارس و کلاس‌ها» شوید. "
        "سپس مجموعه، مدرسه و سال تحصیلی موردنظر را انتخاب کنید.\n\n"
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
            MAIN_MENU_TENANTS,
            MAIN_MENU_CREATE_TENANT,
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
    elif selected == MAIN_MENU_TENANTS:
        await _show_tenant_list(message, principal, db)
    elif selected == MAIN_MENU_CREATE_TENANT:
        await _begin_tenant_creation(message, principal, db, state)
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


@router.message(Command("roles"))
async def roles_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (message.text or "").strip().split(maxsplit=1)
    try:
        if len(parts) == 2:
            try:
                tenant_id = UUID(parts[1])
            except ValueError:
                await message.answer("قالب فرمان: /roles [TENANT_UUID]")
                return
            roles = await list_tenant_roles(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                limit=100,
            )
            heading = f"نقش‌های مجموعه {tenant_id}"
        else:
            roles = await list_platform_roles(
                db,
                actor_id=principal.user.id,
                limit=100,
            )
            heading = "نقش‌های قابل واگذاری در پلتفرم"
    except AccessDenied:
        await message.answer(
            "برای مشاهده نقش‌های مجموعه، /roles TENANT_UUID را اجرا کنید؛ "
            "فهرست نقش‌ها فقط در محدوده مجاز شما نمایش داده می‌شود."
        )
        return
    except ApplicationError as exc:
        await message.answer(str(exc))
        return

    if not roles:
        await message.answer("هنوز هیچ نقشی در این محدوده تعریف نشده است.")
        return

    output = [f"{heading}\n"]
    for role in roles:
        permissions = ", ".join(
            f"{'-' if effect == 'deny' else '+'}{key}"
            for key, effect in role.permissions
        ) or "بدون مجوز"
        scope_label = "Platform" if role.tenant_id is None else (
            f"{role.tenant_name or 'Tenant'} · {role.tenant_id}"
        )
        output.append(
            f"• {role.key} — {role.name}\n"
            f"  محدوده: {scope_label}\n"
            f"  مجوزها: {permissions}"
        )
    await message.answer("\n\n".join(output))


@router.message(Command("role_create"))
async def role_create_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    text_value = (message.text or "").strip()
    if "|" not in text_value:
        await message.answer(
            "قالب فرمان:\n"
            "/role_create TENANT_UUID ROLE_KEY PERMISSION1,PERMISSION2 | نام نمایشی نقش\n\n"
            "فقط مجوزهایی قابل واگذاری‌اند که خودتان در همان مجموعه دارید."
        )
        return

    left, display_name = text_value.split("|", maxsplit=1)
    parts = left.split()
    if len(parts) != 4 or parts[0].split("@", maxsplit=1)[0] != "/role_create":
        await message.answer(
            "قالب فرمان:\\n"
            "/role_create TENANT_UUID ROLE_KEY PERMISSION1,PERMISSION2 | نام نمایشی نقش"
        )
        return
    try:
        tenant_id = UUID(parts[1])
    except ValueError:
        await message.answer("شناسه Tenant معتبر نیست.")
        return

    permission_keys = tuple(
        item.strip()
        for item in parts[3].split(",")
        if item.strip()
    )
    try:
        role = await create_tenant_role(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            key=parts[2],
            name=display_name,
            permission_keys=permission_keys,
        )
    except AccessDenied:
        await message.answer(
            "مجوز ساخت این نقش یا واگذاری یکی از Permissionهای انتخاب‌شده را ندارید."
        )
        return
    except ApplicationError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        "نقش جدید ایجاد شد.\n\n"
        f"شناسه: {role.key}\n"
        f"نام: {role.name}\n"
        f"محدوده: {tenant_id}\n"
        f"تعداد مجوزها: {len(permission_keys)}"
    )


@router.message(Command("members"))
async def members_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("قالب فرمان: /members TENANT_UUID")
        return
    try:
        tenant_id = UUID(parts[1])
        members = await list_tenant_members(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=50,
        )
    except ValueError:
        await message.answer("شناسه Tenant معتبر نیست.")
        return
    except AccessDenied:
        await message.answer("مجوز مشاهده فهرست اعضای این مجموعه را ندارید.")
        return
    except ApplicationError as exc:
        await message.answer(str(exc))
        return

    if not members:
        await message.answer("عضو فعالی در این مجموعه وجود ندارد.")
        return

    lines = [f"اعضای فعال مجموعه {tenant_id}\n"]
    for index, member in enumerate(members, start=1):
        roles = ", ".join(member.role_keys) or "بدون نقش فعال"
        telegram_label = (
            str(member.telegram_user_id)
            if member.telegram_user_id is not None
            else "اتصال تلگرام ندارد"
        )
        lines.append(
            f"{index}. {member.display_name}\n"
            f"   User ID: {member.user_id}\n"
            f"   Telegram ID: {telegram_label}\n"
            f"   نقش‌ها: {roles}"
        )
    await message.answer("\n\n".join(lines))


@router.message(Command("invites"))
async def invitations_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (message.text or "").strip().split(maxsplit=1)
    tenant_id: UUID | None = None
    if len(parts) == 2:
        try:
            tenant_id = UUID(parts[1])
        except ValueError:
            await message.answer("قالب فرمان: /invites [TENANT_UUID]")
            return

    try:
        invitations = await list_admin_invitations(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=50,
        )
    except AccessDenied:
        await message.answer(
            "مجوز مشاهده دعوت‌های این محدوده را ندارید. "
            "برای Tenant از /invites TENANT_UUID استفاده کنید."
        )
        return
    except ApplicationError as exc:
        await message.answer(str(exc))
        return

    if not invitations:
        await message.answer("دعوت‌نامه باز یا منقضی‌نشده‌ای در این محدوده پیدا نشد.")
        return

    heading = (
        f"دعوت‌های مجموعه {tenant_id}"
        if tenant_id is not None
        else "دعوت‌های مدیران پلتفرم و مجموعه‌ها"
    )
    lines = [f"{heading}\n"]
    for index, invitation in enumerate(invitations, start=1):
        scope_label = (
            "Platform"
            if invitation.scope_type == "platform"
            else f"Tenant {invitation.tenant_id}"
        )
        status_label = (
            "منقضی‌شده — نیازمند صدور مجدد"
            if invitation.status == "expired"
            else "منتظر پذیرش"
        )
        lines.append(
            f"{index}. {invitation.display_name}\n"
            f"   Invitation ID: {invitation.invitation_id}\n"
            f"   Telegram ID: {invitation.target_telegram_user_id}\n"
            f"   Role: {invitation.role_key}\n"
            f"   Scope: {scope_label}\n"
            f"   وضعیت: {status_label}\n"
            f"   انقضا: {invitation.expires_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"   لغو: /revoke_invite {invitation.invitation_id}"
        )
    await message.answer("\n\n".join(lines))


@router.message(Command("member_suspend"))
async def member_suspend_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    text_value = (message.text or "").strip()
    if "|" not in text_value:
        await message.answer(
            "قالب فرمان:\n"
            "/member_suspend TENANT_UUID USER_UUID | دلیل تعلیق\n\n"
            "دلیل باید حداقل ۱۰ نویسه باشد. تعلیق فقط پس از تأیید دکمه انجام می‌شود."
        )
        return

    left, reason = text_value.split("|", maxsplit=1)
    parts = left.split()
    if len(parts) != 3 or parts[0].split("@", maxsplit=1)[0] != "/member_suspend":
        await message.answer(
            "قالب فرمان:\n/member_suspend TENANT_UUID USER_UUID | دلیل تعلیق"
        )
        return
    try:
        tenant_id = UUID(parts[1])
        member_user_id = UUID(parts[2])
        preview = await preview_tenant_member_suspension(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_user_id,
        )
    except (ValueError, ApplicationError) as exc:
        await message.answer(str(exc) or "شناسه‌های ورودی معتبر نیستند.")
        return
    except AccessDenied:
        await message.answer("مجوز مدیریت عضویت‌های این مجموعه را ندارید.")
        return

    normalized_reason = reason.strip()
    if not 10 <= len(normalized_reason) <= 500:
        await message.answer("دلیل تعلیق باید بین ۱۰ تا ۵۰۰ نویسه باشد.")
        return

    flow_token = secrets.token_urlsafe(9)
    await state.clear()
    await state.update_data(
        flow_name="member_suspend",
        flow_token=flow_token,
        suspend_tenant_id=str(tenant_id),
        suspend_member_user_id=str(member_user_id),
        suspend_reason=normalized_reason,
    )
    await state.set_state(MemberSuspension.confirm)
    role_warning = (
        "\nاین کاربر مدیر مجموعه است؛ پیش از تأیید، وجود مدیر جایگزین بررسی شده است."
        if preview.is_tenant_admin
        else ""
    )
    await message.answer(
        "پیش‌نمایش تعلیق عضویت\n\n"
        f"نام عضو: {preview.display_name}\n"
        f"User ID: {preview.user_id}\n"
        f"Tenant: {tenant_id}\n"
        f"دلیل: {normalized_reason}\n"
        f"{role_warning}\n\n"
        "در صورت تأیید، عضویت و RoleBindingهای همین Tenant پایان می‌یابند. "
        "حساب تلگرام و عضویت‌های سایر Tenantها تغییر نمی‌کنند.",
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
    if callback.message is not None:
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
            "این دکمه متعلق به درخواست قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await state.clear()
    await callback.answer("تعلیق لغو شد.")
    if callback.message is not None:
        await callback.message.answer("درخواست تعلیق لغو شد؛ تغییری در عضویت انجام نشد.")


@router.message(Command("member_reactivate"))
async def member_reactivate_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    text_value = (message.text or "").strip()
    if "|" not in text_value:
        await message.answer(
            "قالب فرمان:\n"
            "/member_reactivate TENANT_UUID USER_UUID ROLE_KEY | دلیل فعال‌سازی مجدد\n\n"
            "حساب تلگرام کاربر باید فعال باشد و دلیل حداقل ۱۰ نویسه داشته باشد."
        )
        return

    left, reason = text_value.split("|", maxsplit=1)
    parts = left.split()
    if len(parts) != 4 or parts[0].split("@", maxsplit=1)[0] != "/member_reactivate":
        await message.answer(
            "قالب فرمان:\n"
            "/member_reactivate TENANT_UUID USER_UUID ROLE_KEY | دلیل فعال‌سازی مجدد"
        )
        return

    try:
        tenant_id = UUID(parts[1])
        member_user_id = UUID(parts[2])
        preview = await preview_tenant_member_reactivation(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_user_id,
            role_key=parts[3],
        )
    except AccessDenied:
        await message.answer(
            "مجوز فعال‌سازی عضویت یا واگذاری یکی از مجوزهای نقش انتخاب‌شده را ندارید."
        )
        return
    except (ValueError, ApplicationError) as exc:
        await message.answer(str(exc) or "شناسه‌ها یا نقش انتخاب‌شده معتبر نیستند.")
        return

    normalized_reason = reason.strip()
    if not 10 <= len(normalized_reason) <= 500:
        await message.answer("دلیل فعال‌سازی مجدد باید بین ۱۰ تا ۵۰۰ نویسه باشد.")
        return

    flow_token = secrets.token_urlsafe(9)
    await state.clear()
    await state.update_data(
        flow_name="member_reactivate",
        flow_token=flow_token,
        reactivate_tenant_id=str(tenant_id),
        reactivate_member_user_id=str(member_user_id),
        reactivate_role_key=preview.role_key,
        reactivate_reason=normalized_reason,
    )
    await state.set_state(MemberReactivation.confirm)
    permissions = ", ".join(preview.permission_keys)
    await message.answer(
        "پیش‌نمایش فعال‌سازی مجدد عضویت\n\n"
        f"نام عضو: {preview.display_name}\n"
        f"User ID: {preview.user_id}\n"
        f"Tenant: {tenant_id}\n"
        f"نقش جدید: {preview.role_key}\n"
        f"مجوزهای نقش: {permissions}\n"
        f"دلیل: {normalized_reason}\n\n"
        "نقش‌های قبلی برنمی‌گردند؛ نقش انتخاب‌شده به‌عنوان RoleBinding جدید ثبت می‌شود.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="تأیید فعال‌سازی",
                        callback_data=f"member:reactivate:confirm:{flow_token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="لغو",
                        callback_data=f"member:reactivate:cancel:{flow_token}",
                    )
                ],
            ]
        ),
    )


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


@router.message(Command("invite"))
async def invite_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (message.text or "").strip().split(maxsplit=5)
    if len(parts) != 6:
        await message.answer(
            "قالب دعوت مدیر:\n"
            "/invite TELEGRAM_ID ROLE_KEY SCOPE_TYPE SCOPE_ID نام مدیر\n\n"
            "نمونه نقش سراسری:\n"
            "/invite 123456789 platform_owner platform - مدیر پلتفرم\n\n"
            "نمونه نقش مدیر یک مجموعه:\n"
            "/invite 123456789 tenant_admin tenant TENANT_UUID مدیر مجموعه\n\n"
            "برای دیدن نقش‌ها ابتدا /roles را اجرا کنید.\n"
            "در این نسخه فقط محدوده platform و tenant پشتیبانی می‌شود."
        )
        return

    raw_telegram_id, role_key, scope_type, raw_scope_id, display_name = parts
    if not raw_telegram_id.isdigit():
        await message.answer("Telegram ID باید فقط شامل رقم باشد.")
        return

    telegram_user_id = int(raw_telegram_id)
    if telegram_user_id <= 0 or telegram_user_id > 9_223_372_036_854_775_807:
        await message.answer("Telegram ID در محدوده معتبر نیست.")
        return

    if scope_type not in {"platform", "tenant"}:
        await message.answer("در فاز فعلی scope_type فقط platform یا tenant است.")
        return

    if scope_type == "platform":
        if raw_scope_id != "-":
            await message.answer("برای Platform scope، مقدار SCOPE_ID باید - باشد.")
            return
        scope_id = None
    else:
        try:
            scope_id = UUID(raw_scope_id)
        except ValueError:
            await message.answer("برای Tenant scope باید UUID معتبر Tenant را وارد کنید.")
            return

    bot_info = await message.bot.get_me()
    if not bot_info.username:
        await message.answer(
            "نام کاربری ربات تنظیم نشده است؛ دعوت‌نامه ساخته نشد. "
            "ابتدا Username ربات را تنظیم کنید."
        )
        return

    try:
        invitation = await create_admin_pairing(
            db,
            actor_id=principal.user.id,
            target_telegram_user_id=telegram_user_id,
            display_name=display_name,
            role_key=role_key,
            scope_type=scope_type,
            scope_id=scope_id,
        )
    except PairingNotAllowed:
        await message.answer(
            "مجوز واگذاری این نقش را ندارید؛ نقش درخواستی ممکن است "
            "مجوزی فراتر از سطح دسترسی فعلی شما داشته باشد."
        )
        return
    except ValueError as exc:
        await message.answer(str(exc))
        return

    pairing_link = f"https://t.me/{bot_info.username}?start={invitation.raw_token}"
    expires = invitation.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    scope_label = (
        "کل پلتفرم"
        if invitation.scope_type == "platform"
        else f"مجموعه {invitation.tenant_id}"
    )
    await message.answer(
        "دعوت‌نامه یک‌بارمصرف با نقش و محدوده مشخص ساخته شد.\n\n"
        f"نام: {display_name}\n"
        f"Telegram ID مقصد: {telegram_user_id}\n"
        f"شناسه دعوت: {invitation.pairing_id}\n"
        f"نقش: {invitation.role_key}\n"
        f"محدوده: {scope_label}\n"
        f"انقضا: {expires}\n\n"
        "این لینک را فقط برای همان شخص بفرستید:\n"
        f"{pairing_link}\n\n"
        "با پذیرش لینک، نقش و عضویت فوق به‌صورت هم‌زمان فعال می‌شوند."
    )


@router.message(Command("revoke_invite"))
async def revoke_invite_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "قالب فرمان:\n/revoke_invite INVITATION_UUID\n\n"
            "شناسه دعوت در پیام ایجاد دعوت‌نامه نمایش داده می‌شود."
        )
        return
    try:
        invitation_id = UUID(parts[1])
        await revoke_admin_pairing(
            db,
            actor_id=principal.user.id,
            pairing_id=invitation_id,
        )
    except ValueError:
        await message.answer("شناسه دعوت معتبر نیست.")
        return
    except PairingNotAllowed:
        await message.answer("مجوز لغو این دعوت‌نامه را ندارید.")
        return
    except PairingRejected:
        await message.answer(
            "دعوت‌نامه وجود ندارد، قبلاً لغو شده یا توسط کاربر پذیرفته شده است."
        )
        return
    await message.answer("دعوت‌نامه لغو شد؛ لینک قبلی دیگر قابل استفاده نیست.")


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
        tenant = await create_tenant(
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
                "✅ مجموعه ایجاد شد.\n\n"
                f"نام: {tenant.name}\n"
                f"شناسه: {tenant.slug}\n\n"
                "برای مدیریت مدارس و کلاس‌های این مجموعه وارد آن شوید."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🏫 مدیریت مدارس این مجموعه",
                        callback_data=f"tenant:open:{tenant.id}",
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
        "/roles [TENANT_UUID] — فهرست نقش‌های قابل واگذاری\n"
        "/role_create TENANT_UUID KEY PERMISSIONS | NAME — ساخت نقش محدود به اختیارات شما\n"
        "/members TENANT_UUID — فهرست اعضای فعال مجموعه\n"
        "/member_suspend TENANT_UUID USER_UUID | REASON — تعلیق عضو پس از تأیید\n"
        "/member_reactivate TENANT_UUID USER_UUID ROLE_KEY | REASON — فعال‌سازی مجدد با نقش مشخص\n"
        "/invite TELEGRAM_ID ROLE_KEY SCOPE_TYPE SCOPE_ID نام — دعوت با نقش مشخص\n"
        "/invites [TENANT_UUID] — فهرست دعوت‌های باز و منقضی‌شده\n"
        "/revoke_invite INVITATION_UUID — لغو دعوت پذیرفته‌نشده\n"
        "/help — راهنما\n"
        "/status — بررسی سلامت اتصال‌ها\n\n"
        "گزینه‌های منو بر اساس مجوز شما نمایش داده می‌شوند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")]
        ]),
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
