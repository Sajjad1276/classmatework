from __future__ import annotations

import re
import secrets
from datetime import date, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.bot.ux import ensure_back_button
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup as TelegramInlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    ApplicationError,
    ResourceNotFound,
)
from app.application.tenant_access import (
    create_tenant_role,
    list_admin_invitations,
    list_assignable_tenant_permissions,
    list_assignable_tenant_roles,
    list_suspended_tenant_members,
    list_tenant_roles,
    preview_tenant_member_reactivation,
    reactivate_tenant_member,
)
from app.application.tenants import (
    archive_academic_year,
    archive_class,
    archive_school,
    archive_subject,
    create_academic_year,
    create_class,
    create_school,
    create_subject,
    list_academic_years,
    list_accessible_tenants,
    list_archived_school_structure,
    list_archived_schools,
    list_classes,
    list_schools,
    list_subjects,
    list_tenants,
    restore_academic_year,
    restore_class,
    restore_school,
    restore_subject,
    update_academic_year,
    update_class,
    update_school,
    update_subject,
)
from app.db.models import AcademicYear, ClassGroup, School, Subject, Tenant
from app.modules.access.policy import has_permission
from app.modules.identity.pairing import (
    PairingNotAllowed,
    PairingRejected,
    create_admin_pairing,
    revoke_admin_pairing,
)
from app.modules.identity.principal import TelegramPrincipal

router = Router(name="academic-structure")


def InlineKeyboardMarkup(*args, **kwargs) -> TelegramInlineKeyboardMarkup:
    """Create an inline keyboard with a safe back path by default."""
    return ensure_back_button(TelegramInlineKeyboardMarkup(*args, **kwargs))


class TenantRoleWizard(StatesGroup):
    key = State()
    name = State()
    permissions = State()
    confirm = State()


class TenantInviteWizard(StatesGroup):
    telegram_id = State()
    display_name = State()
    role = State()
    confirm = State()
    revoke_confirm = State()


class TenantMemberRestoreWizard(StatesGroup):
    reason = State()
    confirm = State()


class StructureWizard(StatesGroup):
    school_name = State()
    school_confirm = State()
    year_name = State()
    year_confirm = State()
    subject_name = State()
    subject_confirm = State()
    class_name = State()
    class_grade = State()
    class_confirm = State()
    edit_value = State()
    edit_confirm = State()


_CONTEXT_KEYS = {
    "context_tenant_id",
    "context_school_id",
    "context_academic_year_id",
}
_FIELD_PROMPTS = {
    StructureWizard.school_name.state: "نام مدرسه را وارد کنید:",
    StructureWizard.year_name.state: "نام سال تحصیلی را وارد کنید؛ نمونه: ۱۴۰۵-۱۴۰۶",
    StructureWizard.subject_name.state: "نام درس را وارد کنید:",
    StructureWizard.class_name.state: "نام کلاس را وارد کنید؛ نمونه: دهم الف",
    StructureWizard.class_grade.state: "پایه/مقطع کلاس را وارد کنید؛ نمونه: دهم",
    StructureWizard.edit_value.state: "مقدار جدید را وارد کنید:",
}
_FIELD_KEYS = {
    StructureWizard.school_name.state: "school_name",
    StructureWizard.year_name.state: "year_name",
    StructureWizard.subject_name.state: "subject_name",
    StructureWizard.class_name.state: "class_name",
    StructureWizard.class_grade.state: "class_grade",
}
_PREVIOUS = {
    StructureWizard.school_confirm.state: StructureWizard.school_name,
    StructureWizard.year_confirm.state: StructureWizard.year_name,
    StructureWizard.subject_confirm.state: StructureWizard.subject_name,
    StructureWizard.class_grade.state: StructureWizard.class_name,
    StructureWizard.class_confirm.state: StructureWizard.class_grade,
    StructureWizard.edit_confirm.state: StructureWizard.edit_value,
}
_CONFIRM_STATES = {
    "school": StructureWizard.school_confirm.state,
    "year": StructureWizard.year_confirm.state,
    "subject": StructureWizard.subject_confirm.state,
    "class": StructureWizard.class_confirm.state,
    "edit": StructureWizard.edit_confirm.state,
}


async def _render_panel(
    message: Message,
    body: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    """Edit the active bot message instead of stacking a new message for every screen."""
    if message.from_user is not None and message.from_user.is_bot:
        try:
            await message.edit_text(body, reply_markup=keyboard)
            return
        except TelegramBadRequest:
            pass
    await message.answer(body, reply_markup=keyboard)


def _generated_code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def _ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _jalali_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert a Jalali date to Gregorian without an extra runtime dependency."""
    year += 1595
    days = (
        -355668
        + 365 * year
        + (year // 33) * 8
        + ((year % 33 + 3) // 4)
        + day
    )
    days += (month - 1) * 31 if month < 7 else 186 + (month - 7) * 30
    gregorian_year = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gregorian_year += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            gregorian_year += days // 365
            days %= 365
    gregorian_year += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gregorian_year += (days - 1) // 365
        days = (days - 1) % 365
    return date.fromordinal(date(gregorian_year, 1, 1).toordinal() + days)


def _default_academic_year_range(name: str) -> tuple[date, date]:
    normalized = _ascii_digits(name)
    match = re.search(
        r"(?<!\d)(13\d{2}|14\d{2})\s*[-–/]\s*(13\d{2}|14\d{2})(?!\d)",
        normalized,
    )
    if match:
        first_year, second_year = (int(value) for value in match.groups())
        if second_year == first_year + 1:
            starts_on = _jalali_to_gregorian(first_year, 1, 1)
            next_year_start = _jalali_to_gregorian(second_year, 1, 1)
            return starts_on, next_year_start - timedelta(days=1)
    starts_on = date.today()
    return starts_on, starts_on + timedelta(days=364)


async def _clear_flow(state: FSMContext) -> None:
    """Clear only the active wizard while preserving selected navigation context."""
    current_data = await state.get_data()
    preserved = {
        key: value for key, value in current_data.items() if key in _CONTEXT_KEYS
    }
    await state.clear()
    if preserved:
        await state.set_data(preserved)


async def _context_ids(state: FSMContext) -> tuple[UUID, UUID | None, UUID | None]:
    data = await state.get_data()
    tenant_value = data.get("context_tenant_id")
    if not tenant_value:
        raise ResourceNotFound("ابتدا یک مجموعه را انتخاب کنید.")
    try:
        tenant_id = UUID(tenant_value)
        school_id = UUID(data["context_school_id"]) if data.get("context_school_id") else None
        year_id = (
            UUID(data["context_academic_year_id"])
            if data.get("context_academic_year_id")
            else None
        )
    except (ValueError, TypeError, KeyError):
        raise ResourceNotFound("زمینه انتخاب‌شده معتبر نیست؛ دوباره انتخاب کنید.") from None
    return tenant_id, school_id, year_id


async def _has_any_school_access(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
) -> bool:
    for permission in (
        "school:read",
        "school:create",
        "academic_year:create",
        "class:create",
        "subject:create",
    ):
        allowed = await has_permission(
            db,
            user_id=actor_id,
            permission_key=permission,
            tenant_id=tenant_id,
            scope_type="tenant",
            scope_id=tenant_id,
        )
        if allowed:
            return True
    return False


async def _has_school_permission(
    db: AsyncSession,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    school_id: UUID,
    permission: str,
) -> bool:
    return await has_permission(
        db,
        user_id=actor_id,
        permission_key=permission,
        tenant_id=tenant_id,
        scope_type="school",
        scope_id=school_id,
    )


def _tenant_keyboard(
    *,
    tenant_id: UUID,
    can_list: bool,
    can_create: bool,
    can_archive: bool,
    can_read_members: bool,
    can_manage_roles: bool,
    can_invite_members: bool,
    can_reactivate_members: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_read_members:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👥 مدیریت اعضای مجموعه",
                    callback_data=f"member:directory:{tenant_id}",
                )
            ]
        )
    if can_manage_roles:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🛡 نقش‌ها و دسترسی‌ها",
                    callback_data="role:directory",
                )
            ]
        )
    if can_invite_members:
        rows.append(
            [InlineKeyboardButton(text="✉️ دعوت عضو یا مدیر", callback_data="tenant:invite:create")]
        )
        rows.append(
            [InlineKeyboardButton(text="📨 دعوت‌های در انتظار", callback_data="tenant:invite:list")]
        )
    if can_reactivate_members:
        rows.append(
            [InlineKeyboardButton(text="♻️ اعضای تعلیق‌شده", callback_data="tenant:member:restore:list")]
        )
    if can_list:
        rows.append([InlineKeyboardButton(text="🏫 مدارس مجموعه", callback_data="school:list")])
    if can_create:
        rows.append([InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="school:create")])
    if can_archive:
        rows.append([InlineKeyboardButton(text="🗄 مدارس بایگانی‌شده", callback_data="life:bin:s:1")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="tenant:directory")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _show_tenant_menu(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
    notice: str | None = None,
) -> None:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active":
        await message.answer("این مجموعه فعال نیست یا پیدا نشد.")
        return
    can_list = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="school:read",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    can_create = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="school:create",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    can_archive = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="school:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    can_read_members = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="membership:read",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    can_manage_roles = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="role:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    can_invite_members = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="membership:invite",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    ) and can_manage_roles
    can_reactivate_members = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="membership:manage",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    ) and can_manage_roles
    body = (
        f"🏢 مدیریت مجموعه: {tenant.name}\n"
        f"شناسه: {tenant.slug}\n\n"
        "مدیریت مدارس، سال‌های تحصیلی، کلاس‌ها و درس‌ها از این بخش انجام می‌شود."
    )
    if notice:
        body = f"{notice}\n\n{body}"
    await _render_panel(
        message,
        body,
        _tenant_keyboard(
            tenant_id=tenant_id,
            can_list=can_list,
            can_create=can_create,
            can_archive=can_archive,
            can_read_members=can_read_members,
            can_manage_roles=can_manage_roles,
            can_invite_members=can_invite_members,
            can_reactivate_members=can_reactivate_members,
        ),
    )

async def _show_role_directory(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
    notice: str | None = None,
) -> None:
    roles = await list_tenant_roles(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        limit=50,
    )
    lines = ["🛡 نقش‌ها و دسترسی‌های مجموعه", ""]
    if not roles:
        lines.append("هنوز نقش اختصاصی برای این مجموعه تعریف نشده است.")
    else:
        for role in roles[:12]:
            permissions = [
                f"{'−' if effect == 'deny' else '+'}{key}"
                for key, effect in role.permissions
            ]
            permission_text = "، ".join(permissions[:8]) or "بدون مجوز"
            if len(permissions) > 8:
                permission_text += f" و {len(permissions) - 8} مورد دیگر"
            lines.extend(
                [
                    f"• {role.name} ({role.key})",
                    f"  دسترسی‌ها: {permission_text}",
                    "",
                ]
            )
        if len(roles) > 12:
            lines.append(f"۱۲ نقش از {len(roles)} نقش نمایش داده شد.")
    if notice:
        lines.insert(0, notice)
        lines.insert(1, "")

    await _render_panel(
        message,
        "\n".join(lines).strip(),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ ساخت نقش جدید", callback_data="role:create")],
                [InlineKeyboardButton(text="⬅️ بازگشت به مجموعه", callback_data="tenant:menu")],
            ]
        ),
    )


def _role_cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ لغو و بازگشت", callback_data=f"role:cancel:{token}")]
        ]
    )


async def _show_role_permission_picker(
    message: Message,
    state: FSMContext,
    *,
    page: int = 0,
) -> None:
    data = await state.get_data()
    token = data.get("role_flow_token", "")
    available = data.get("role_available_permissions", [])
    selected = set(data.get("role_selected_permissions", []))
    if not token or not available:
        await state.clear()
        await message.answer("فهرست مجوزها منقضی شده است؛ دوباره وارد بخش نقش‌ها شوید.")
        return

    page_size = 8
    total_pages = max(1, (len(available) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = min(start + page_size, len(available))
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(start, end):
        key = available[index]
        marker = "✅" if key in selected else "▫️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker} {key}",
                    callback_data=f"role:toggle:{token}:{index}:{page}",
                )
            ]
        )

    if total_pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ قبلی",
                    callback_data=f"role:page:{token}:{page - 1}",
                )
            )
        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="بعدی ➡️",
                    callback_data=f"role:page:{token}:{page + 1}",
                )
            )
        if navigation:
            rows.append(navigation)

    if selected:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"ادامه · {len(selected)} مجوز انتخاب‌شده",
                    callback_data=f"role:review:{token}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ لغو و بازگشت", callback_data=f"role:cancel:{token}")]
    )
    body = (
        "انتخاب مجوزهای نقش\n\n"
        f"شناسه: {data.get('role_key')}\n"
        f"نام: {data.get('role_name')}\n"
        f"انتخاب‌شده: {len(selected)} از ۲۰\n"
        f"صفحه {page + 1} از {total_pages}\n\n"
        "برای فعال/غیرفعال کردن هر مجوز روی آن بزنید."
    )
    await _render_panel(
        message,
        body,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.update_data(role_permissions_page=page)


@router.callback_query(F.data == "role:directory")
async def role_directory_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        if callback.message is not None:
            await _show_role_directory(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
            )
    except (ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "مجوز مدیریت نقش‌ها را ندارید.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "role:create")
async def role_create_start_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        available = await list_assignable_tenant_permissions(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
        )
    except (ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "امکان ساخت نقش را ندارید.", show_alert=True)
        return
    if not available:
        await callback.answer("هیچ مجوز قابل واگذاری در دسترس شما نیست.", show_alert=True)
        return

    await _clear_flow(state)
    token = secrets.token_urlsafe(6)
    await state.update_data(
        role_flow_token=token,
        role_tenant_id=str(tenant_id),
        role_available_permissions=list(available),
        role_selected_permissions=[],
        role_permissions_page=0,
    )
    await state.set_state(TenantRoleWizard.key)
    await callback.answer()
    if callback.message is not None:
        await _render_panel(
            callback.message,
            "ساخت نقش جدید\n\n"
            "ابتدا شناسه کوتاه نقش را وارد کنید؛ فقط حروف انگلیسی کوچک، عدد و زیرخط. "
            "نمونه: teacher_assistant",
            _role_cancel_keyboard(token),
        )


@router.message(TenantRoleWizard.key, F.text)
async def tenant_role_key_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    token = data.get("role_flow_token")
    if not token:
        await state.clear()
        await message.answer("فرم ساخت نقش منقضی شده است.")
        return

    key = (message.text or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key):
        await message.answer("شناسه باید با حرف انگلیسی کوچک شروع شود و ۲ تا ۶۴ نویسه باشد.")
        return
    await state.update_data(role_key=key)
    await state.set_state(TenantRoleWizard.name)
    await message.answer(
        "نام نمایشی نقش را وارد کنید (۲ تا ۱۲۰ نویسه):",
        reply_markup=_role_cancel_keyboard(token),
    )


@router.message(TenantRoleWizard.name, F.text)
async def tenant_role_name_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    token = data.get("role_flow_token")
    if not token:
        await state.clear()
        await message.answer("فرم ساخت نقش منقضی شده است.")
        return

    name = (message.text or "").strip()
    if not 2 <= len(name) <= 120:
        await message.answer("نام نقش باید بین ۲ تا ۱۲۰ نویسه باشد.")
        return
    await state.update_data(role_name=name)
    await state.set_state(TenantRoleWizard.permissions)
    await _show_role_permission_picker(message, state, page=0)


@router.callback_query(F.data.startswith("role:toggle:"))
async def role_permission_toggle_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[2] if len(parts) == 5 else ""
    if (
        await state.get_state() != TenantRoleWizard.permissions.state
        or not token
        or token != data.get("role_flow_token")
    ):
        await callback.answer("این فرم دیگر فعال نیست.", show_alert=True)
        return
    try:
        index = int(parts[3])
        page = int(parts[4])
        available = data.get("role_available_permissions", [])
        if not 0 <= index < len(available):
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer("گزینه انتخاب‌شده معتبر نیست.", show_alert=True)
        return

    selected = set(data.get("role_selected_permissions", []))
    key = available[index]
    if key in selected:
        selected.remove(key)
    elif len(selected) >= 20:
        await callback.answer("حداکثر ۲۰ مجوز برای هر نقش قابل انتخاب است.", show_alert=True)
        return
    else:
        selected.add(key)

    await state.update_data(role_selected_permissions=sorted(selected))
    await callback.answer()
    if callback.message is not None:
        await _show_role_permission_picker(callback.message, state, page=page)


@router.callback_query(F.data.startswith("role:page:"))
async def role_permission_page_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[2] if len(parts) == 4 else ""
    if (
        await state.get_state() != TenantRoleWizard.permissions.state
        or not token
        or token != data.get("role_flow_token")
    ):
        await callback.answer("این فرم دیگر فعال نیست.", show_alert=True)
        return
    try:
        page = int(parts[3])
    except ValueError:
        await callback.answer("شماره صفحه معتبر نیست.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_role_permission_picker(callback.message, state, page=page)


@router.callback_query(F.data.startswith("role:review:"))
async def role_review_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    selected = data.get("role_selected_permissions", [])
    if (
        await state.get_state() != TenantRoleWizard.permissions.state
        or not token
        or token != data.get("role_flow_token")
        or not 1 <= len(selected) <= 20
    ):
        await callback.answer("ابتدا بین ۱ تا ۲۰ مجوز انتخاب کنید.", show_alert=True)
        return

    await state.set_state(TenantRoleWizard.confirm)
    await callback.answer()
    if callback.message is not None:
        body = (
            "پیش‌نمایش ساخت نقش\n\n"
            f"شناسه: {data.get('role_key')}\n"
            f"نام: {data.get('role_name')}\n"
            f"تعداد مجوزها: {len(selected)}\n\n"
            + "\n".join(f"• {key}" for key in selected)
            + "\n\nاین نقش فقط در همین مجموعه قابل واگذاری است."
        )
        await _render_panel(
            callback.message,
            body,
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ ساخت نقش", callback_data=f"role:confirm:{token}")],
                    [InlineKeyboardButton(text="⬅️ تغییر مجوزها", callback_data=f"role:back:{token}")],
                    [InlineKeyboardButton(text="لغو", callback_data=f"role:cancel:{token}")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("role:back:"))
async def role_permission_back_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if (
        await state.get_state() != TenantRoleWizard.confirm.state
        or not token
        or token != data.get("role_flow_token")
    ):
        await callback.answer("این فرم دیگر فعال نیست.", show_alert=True)
        return
    await state.set_state(TenantRoleWizard.permissions)
    await callback.answer()
    if callback.message is not None:
        await _show_role_permission_picker(
            callback.message,
            state,
            page=int(data.get("role_permissions_page", 0)),
        )


@router.callback_query(F.data.startswith("role:confirm:"))
async def role_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if (
        await state.get_state() != TenantRoleWizard.confirm.state
        or not token
        or token != data.get("role_flow_token")
    ):
        await callback.answer("این تأیید متعلق به فرم قبلی است یا منقضی شده است.", show_alert=True)
        return

    try:
        tenant_id = UUID(data["role_tenant_id"])
        role = await create_tenant_role(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            key=data.get("role_key", ""),
            name=data.get("role_name", ""),
            permission_keys=tuple(data.get("role_selected_permissions", [])),
        )
    except AccessDenied:
        await _clear_flow(state)
        await callback.answer("مجوز واگذاری یکی از دسترسی‌های انتخاب‌شده را ندارید.", show_alert=True)
        return
    except (ValueError, KeyError, ApplicationError) as exc:
        await _clear_flow(state)
        await callback.answer(str(exc) or "ساخت نقش انجام نشد.", show_alert=True)
        return

    await _clear_flow(state)
    await callback.answer("نقش ایجاد شد.")
    if callback.message is not None:
        await _show_role_directory(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            notice=f"✅ نقش «{role.name}» ایجاد شد.",
        )


@router.callback_query(F.data.startswith("role:cancel:"))
async def role_create_cancel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if not token or token != data.get("role_flow_token"):
        await callback.answer("این دکمه متعلق به فرم قبلی یا منقضی شده است.", show_alert=True)
        return
    try:
        tenant_id = UUID(data["role_tenant_id"])
    except (ValueError, KeyError):
        await callback.answer("زمینه مجموعه منقضی شده است؛ دوباره انتخاب کنید.", show_alert=True)
        return

    await _clear_flow(state)
    await callback.answer("ساخت نقش لغو شد.")
    if callback.message is not None:
        await _show_role_directory(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            notice="ساخت نقش لغو شد؛ تغییری ثبت نشد.",
        )


def _tenant_invite_clear_data() -> dict:
    return {
        "tenant_invite_token": None,
        "tenant_invite_tenant_id": None,
        "tenant_invite_roles": None,
        "tenant_invite_telegram_id": None,
        "tenant_invite_display_name": None,
        "tenant_invite_role_key": None,
        "tenant_invite_list_token": None,
        "tenant_invite_list_ids": None,
        "tenant_invite_selected_id": None,
    }


async def _show_tenant_invitation_list(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    state: FSMContext,
    tenant_id: UUID,
    page: int = 0,
    notice: str | None = None,
) -> None:
    invitations = await list_admin_invitations(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        limit=100,
    )
    token = secrets.token_urlsafe(6)
    ids = [str(item.invitation_id) for item in invitations]
    page_size = 8
    page_count = max(1, (len(ids) + page_size - 1) // page_size)
    page = min(max(page, 0), page_count - 1)
    rows: list[list[InlineKeyboardButton]] = []
    start = page * page_size
    for index in range(start, min(start + page_size, len(invitations))):
        item = invitations[index]
        status = "منقضی" if item.status == "expired" else "در انتظار"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"لغو · {item.display_name[:18]} · {status}",
                    callback_data=f"tenant:invite:revoke:prompt:{token}:{index}",
                )
            ]
        )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"tenant:invite:page:{token}:{page - 1}")
        )
    if page < page_count - 1:
        navigation.append(
            InlineKeyboardButton(text="بعدی ➡️", callback_data=f"tenant:invite:page:{token}:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ دعوت جدید", callback_data="tenant:invite:create")],
            [InlineKeyboardButton(text="⬅️ بازگشت به مجموعه", callback_data="tenant:menu")],
        ]
    )
    lines = [
        "📨 دعوت‌های مجموعه",
        f"تعداد دعوت‌های باز یا منقضی: {len(invitations)}",
        f"صفحه {page + 1} از {page_count}",
        "",
    ]
    if notice:
        lines = [notice, "", *lines]
    for item in invitations[start : start + page_size]:
        label = "منقضی‌شده" if item.status == "expired" else "منتظر پذیرش"
        lines.append(
            f"• {item.display_name} · {item.role_key}\n"
            f"  Telegram ID: {item.target_telegram_user_id} · {label}\n"
            f"  انقضا: {item.expires_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    if not invitations:
        lines.append("دعوت باز یا منقضی‌شده‌ای برای این مجموعه وجود ندارد.")
    await state.update_data(
        **_tenant_invite_clear_data(),
        tenant_invite_list_token=token,
        tenant_invite_list_ids=ids,
        context_tenant_id=str(tenant_id),
    )
    await state.set_state(None)
    await _render_panel(message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _tenant_invite_roles_keyboard(
    roles: list[dict],
    *,
    token: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    page_size = 8
    page_count = max(1, (len(roles) + page_size - 1) // page_size)
    page = min(max(page, 0), page_count - 1)
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(page * page_size, min((page + 1) * page_size, len(roles))):
        role = roles[index]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{role['name'][:18]} · {role['key'][:18]}",
                    callback_data=f"tenant:invite:role:{token}:{index}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"tenant:invite:rolepage:{token}:{page - 1}")
        )
    if page < page_count - 1:
        nav.append(
            InlineKeyboardButton(text="بعدی ➡️", callback_data=f"tenant:invite:rolepage:{token}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="لغو", callback_data=f"tenant:invite:cancel:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "tenant:invite:create")
async def tenant_invite_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        can_invite = await has_permission(
            db,
            user_id=principal.user.id,
            permission_key="membership:invite",
            tenant_id=tenant_id,
            scope_type="tenant",
            scope_id=tenant_id,
        )
        if not can_invite:
            raise AccessDenied
        roles = await list_assignable_tenant_roles(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=100,
        )
    except (ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "مجوز دعوت اعضا را ندارید.", show_alert=True)
        return
    if not roles:
        await callback.answer("هیچ نقش قابل واگذاری برای این مجموعه وجود ندارد.", show_alert=True)
        return
    token = secrets.token_urlsafe(6)
    await state.update_data(
        tenant_invite_token=token,
        tenant_invite_tenant_id=str(tenant_id),
        tenant_invite_roles=[{"key": role.key, "name": role.name} for role in roles],
    )
    await state.set_state(TenantInviteWizard.telegram_id)
    await callback.answer()
    if callback.message is not None:
        await _render_panel(
            callback.message,
            "✉️ دعوت عضو یا مدیر\n\nTelegram ID شخص را وارد کنید. نام کاربری (@username) کافی نیست.",
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data=f"tenant:invite:cancel:{token}")]]
            ),
        )


@router.message(TenantInviteWizard.telegram_id, F.text)
async def tenant_invite_telegram_id_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("tenant_invite_token"):
        await state.set_state(None)
        await message.answer("فرم دعوت منقضی شده است.")
        return
    raw = (message.text or "").strip()
    if not raw.isdecimal():
        await message.answer("Telegram ID باید فقط شامل رقم باشد.")
        return
    telegram_id = int(raw)
    if not 0 < telegram_id <= 9_223_372_036_854_775_807:
        await message.answer("Telegram ID خارج از محدوده معتبر است.")
        return
    await state.update_data(tenant_invite_telegram_id=telegram_id)
    await state.set_state(TenantInviteWizard.display_name)
    await message.answer(
        "نام نمایشی شخص را وارد کنید (۱ تا ۱۶۰ نویسه):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data=f"tenant:invite:cancel:{data['tenant_invite_token']}")]]
        ),
    )


@router.message(TenantInviteWizard.display_name, F.text)
async def tenant_invite_display_name_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    token = data.get("tenant_invite_token")
    if not token:
        await state.set_state(None)
        await message.answer("فرم دعوت منقضی شده است.")
        return
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 160:
        await message.answer("نام نمایشی باید بین ۱ تا ۱۶۰ نویسه باشد.")
        return
    await state.update_data(tenant_invite_display_name=name)
    await state.set_state(TenantInviteWizard.role)
    await message.answer(
        "نقش را برای شخص انتخاب کنید:",
        reply_markup=_tenant_invite_roles_keyboard(data.get("tenant_invite_roles", []), token=token),
    )


@router.callback_query(F.data.startswith("tenant:invite:rolepage:"))
async def tenant_invite_role_page_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[3] if len(parts) == 5 else ""
    if await state.get_state() != TenantInviteWizard.role.state or token != data.get("tenant_invite_token"):
        await callback.answer("فرم دعوت منقضی شده است.", show_alert=True)
        return
    try:
        page = int(parts[4])
    except ValueError:
        await callback.answer("شماره صفحه معتبر نیست.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=_tenant_invite_roles_keyboard(data.get("tenant_invite_roles", []), token=token, page=page)
        )


@router.callback_query(F.data.startswith("tenant:invite:role:"))
async def tenant_invite_role_select_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[3] if len(parts) == 5 else ""
    if await state.get_state() != TenantInviteWizard.role.state or token != data.get("tenant_invite_token"):
        await callback.answer("فرم دعوت منقضی شده است.", show_alert=True)
        return
    try:
        index = int(parts[4])
        role = data.get("tenant_invite_roles", [])[index]
    except (ValueError, IndexError):
        await callback.answer("نقش انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    await state.update_data(tenant_invite_role_key=role["key"])
    await state.set_state(TenantInviteWizard.confirm)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "پیش‌نمایش دعوت‌نامه\n\n"
            f"نام: {data.get('tenant_invite_display_name')}\n"
            f"Telegram ID: {data.get('tenant_invite_telegram_id')}\n"
            f"نقش: {role['name']} ({role['key']})\n\n"
            "دعوت یک‌بارمصرف و دارای اعتبار ۱۵ دقیقه‌ای است. دسترسی‌ها فقط پس از پذیرش لینک توسط همان حساب فعال می‌شوند.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ صدور دعوت", callback_data=f"tenant:invite:confirm:{token}")],
                    [InlineKeyboardButton(text="⬅️ تغییر نقش", callback_data=f"tenant:invite:change:{token}")],
                    [InlineKeyboardButton(text="لغو", callback_data=f"tenant:invite:cancel:{token}")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("tenant:invite:change:"))
async def tenant_invite_change_callback(callback: CallbackQuery, state: FSMContext) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if await state.get_state() != TenantInviteWizard.confirm.state or token != data.get("tenant_invite_token"):
        await callback.answer("فرم دعوت منقضی شده است.", show_alert=True)
        return
    await state.set_state(TenantInviteWizard.role)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "نقش دیگری انتخاب کنید:",
            reply_markup=_tenant_invite_roles_keyboard(data.get("tenant_invite_roles", []), token=token),
        )


@router.callback_query(F.data.startswith("tenant:invite:confirm:"))
async def tenant_invite_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if await state.get_state() != TenantInviteWizard.confirm.state or token != data.get("tenant_invite_token"):
        await callback.answer("این تأیید متعلق به فرم قبلی است.", show_alert=True)
        return
    try:
        tenant_id = UUID(data["tenant_invite_tenant_id"])
        bot_info = await callback.bot.get_me()
        if not bot_info.username:
            raise ValueError("نام کاربری ربات تنظیم نشده است؛ دعوت صادر نشد.")
        invitation = await create_admin_pairing(
            db,
            actor_id=principal.user.id,
            target_telegram_user_id=int(data["tenant_invite_telegram_id"]),
            display_name=data["tenant_invite_display_name"],
            role_key=data["tenant_invite_role_key"],
            scope_type="tenant",
            scope_id=tenant_id,
        )
    except PairingNotAllowed:
        await callback.answer("مجوز واگذاری این نقش دیگر در اختیار شما نیست.", show_alert=True)
        await state.set_state(None)
        return
    except (ValueError, KeyError, TypeError, ApplicationError) as exc:
        await callback.answer(str(exc) or "صدور دعوت انجام نشد.", show_alert=True)
        return
    link = f"https://t.me/{bot_info.username}?start={invitation.raw_token}"
    await state.update_data(**_tenant_invite_clear_data())
    await state.set_state(None)
    await callback.answer("دعوت صادر شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "✅ دعوت‌نامه صادر شد.\n\n"
            f"نام: {data.get('tenant_invite_display_name')}\n"
            f"Telegram ID: {invitation.telegram_user_id}\n"
            f"نقش: {invitation.role_key}\n"
            f"انقضا: {invitation.expires_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            "لینک را فقط برای همان شخص بفرستید؛ این توکن یک‌بار نمایش داده می‌شود:\n"
            f"{link}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📨 فهرست دعوت‌ها", callback_data="tenant:invite:list")],
                    [InlineKeyboardButton(text="⬅️ بازگشت به مجموعه", callback_data="tenant:menu")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("tenant:invite:cancel:"))
async def tenant_invite_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if not token or token != data.get("tenant_invite_token"):
        await callback.answer("این فرم دیگر فعال نیست.", show_alert=True)
        return
    await state.update_data(**_tenant_invite_clear_data())
    await state.set_state(None)
    await callback.answer("دعوت لغو شد.")
    if callback.message is not None:
        await _render_panel(
            callback.message,
            "فرآیند دعوت لغو شد؛ تغییری ثبت نشد.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت به مجموعه", callback_data="tenant:menu")]]),
        )


@router.callback_query(F.data == "tenant:invite:list")
async def tenant_invitation_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        if callback.message is not None:
            await _show_tenant_invitation_list(
                callback.message,
                db=db,
                principal=principal,
                state=state,
                tenant_id=tenant_id,
            )
        await callback.answer()
    except (ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "مجوز مشاهده دعوت‌ها را ندارید.", show_alert=True)


@router.callback_query(F.data.startswith("tenant:invite:page:"))
async def tenant_invitation_page_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[3] if len(parts) == 5 else ""
    if token != data.get("tenant_invite_list_token"):
        await callback.answer("فهرست دعوت‌ها قدیمی است.", show_alert=True)
        return
    try:
        page = int(parts[4])
        tenant_id, _, _ = await _context_ids(state)
        if callback.message is not None:
            await _show_tenant_invitation_list(
                callback.message,
                db=db,
                principal=principal,
                state=state,
                tenant_id=tenant_id,
                page=page,
            )
        await callback.answer()
    except (ValueError, ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "نمایش صفحه ممکن نیست.", show_alert=True)


@router.callback_query(F.data.startswith("tenant:invite:revoke:prompt:"))
async def tenant_invitation_revoke_prompt_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[4] if len(parts) == 6 else ""
    try:
        index = int(parts[5])
        invitation_id = data.get("tenant_invite_list_ids", [])[index]
    except (ValueError, IndexError):
        await callback.answer("دعوت انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    if token != data.get("tenant_invite_list_token"):
        await callback.answer("فهرست دعوت‌ها قدیمی است.", show_alert=True)
        return
    await state.update_data(tenant_invite_selected_id=invitation_id)
    await state.set_state(TenantInviteWizard.revoke_confirm)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "لغو دعوت‌نامه\n\n"
            f"شناسه دعوت: {invitation_id}\n"
            "پس از تأیید، لینک قبلی دیگر قابل استفاده نیست.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ لغو نهایی دعوت", callback_data=f"tenant:invite:revoke:confirm:{token}")],
                    [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"tenant:invite:revoke:cancel:{token}")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("tenant:invite:revoke:confirm:"))
async def tenant_invitation_revoke_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != TenantInviteWizard.revoke_confirm.state
        or token != data.get("tenant_invite_list_token")
        or not data.get("tenant_invite_selected_id")
    ):
        await callback.answer("این تأیید متعلق به فهرست قبلی است.", show_alert=True)
        return
    try:
        await revoke_admin_pairing(
            db,
            actor_id=principal.user.id,
            pairing_id=UUID(data["tenant_invite_selected_id"]),
        )
        tenant_id, _, _ = await _context_ids(state)
    except (PairingNotAllowed, PairingRejected, ValueError, ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "لغو دعوت انجام نشد.", show_alert=True)
        return
    await callback.answer("دعوت لغو شد.")
    if callback.message is not None:
        await _show_tenant_invitation_list(
            callback.message,
            db=db,
            principal=principal,
            state=state,
            tenant_id=tenant_id,
            notice="✅ دعوت‌نامه لغو شد.",
        )


@router.callback_query(F.data.startswith("tenant:invite:revoke:cancel:"))
async def tenant_invitation_revoke_cancel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if token != data.get("tenant_invite_list_token"):
        await callback.answer("فهرست دعوت‌ها قدیمی است.", show_alert=True)
        return
    try:
        tenant_id, _, _ = await _context_ids(state)
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("دعوت فعال باقی ماند.")
    if callback.message is not None:
        await _show_tenant_invitation_list(
            callback.message,
            db=db,
            principal=principal,
            state=state,
            tenant_id=tenant_id,
        )


async def _show_suspended_members(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    state: FSMContext,
    tenant_id: UUID,
    page: int = 0,
    notice: str | None = None,
) -> None:
    members = await list_suspended_tenant_members(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        limit=100,
    )
    token = secrets.token_urlsafe(6)
    ids = [str(member.user_id) for member in members]
    page_size = 8
    page_count = max(1, (len(members) + page_size - 1) // page_size)
    page = min(max(page, 0), page_count - 1)
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["♻️ اعضای تعلیق‌شده", f"تعداد: {len(members)}", f"صفحه {page + 1} از {page_count}", ""]
    if notice:
        lines = [notice, "", *lines]
    start = page * page_size
    for index in range(start, min(start + page_size, len(members))):
        member = members[index]
        if member.telegram_user_id is None:
            lines.append(f"• {member.display_name} — حساب تلگرام فعال ندارد")
            continue
        lines.append(f"• {member.display_name} · Telegram ID: {member.telegram_user_id}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"فعال‌سازی · {member.display_name[:24]}",
                    callback_data=f"tenant:member:restore:choose:{token}:{index}",
                )
            ]
        )
    if not members:
        lines.append("عضو تعلیق‌شده‌ای برای این مجموعه وجود ندارد.")
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"tenant:member:restore:page:{token}:{page - 1}")
        )
    if page < page_count - 1:
        navigation.append(
            InlineKeyboardButton(text="بعدی ➡️", callback_data=f"tenant:member:restore:page:{token}:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت به مجموعه", callback_data="tenant:menu")])
    await state.update_data(
        member_restore_list_token=token,
        member_restore_list_ids=ids,
        context_tenant_id=str(tenant_id),
    )
    await state.set_state(None)
    await _render_panel(message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "tenant:member:restore:list")
async def suspended_members_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        if callback.message is not None:
            await _show_suspended_members(
                callback.message,
                db=db,
                principal=principal,
                state=state,
                tenant_id=tenant_id,
            )
        await callback.answer()
    except (ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "مجوز مدیریت اعضا را ندارید.", show_alert=True)


@router.callback_query(F.data.startswith("tenant:member:restore:page:"))
async def suspended_members_page_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[4] if len(parts) == 6 else ""
    if token != data.get("member_restore_list_token"):
        await callback.answer("فهرست اعضا قدیمی است.", show_alert=True)
        return
    try:
        page = int(parts[5])
        tenant_id, _, _ = await _context_ids(state)
        if callback.message is not None:
            await _show_suspended_members(
                callback.message,
                db=db,
                principal=principal,
                state=state,
                tenant_id=tenant_id,
                page=page,
            )
        await callback.answer()
    except (ValueError, ResourceNotFound, AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "نمایش صفحه ممکن نیست.", show_alert=True)


@router.callback_query(F.data.startswith("tenant:member:restore:choose:"))
async def suspended_member_choose_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[4] if len(parts) == 6 else ""
    try:
        index = int(parts[5])
        member_id = data.get("member_restore_list_ids", [])[index]
        tenant_id, _, _ = await _context_ids(state)
    except (ValueError, IndexError, ResourceNotFound) as exc:
        await callback.answer(str(exc) or "عضو انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    if token != data.get("member_restore_list_token"):
        await callback.answer("فهرست اعضا قدیمی است.", show_alert=True)
        return
    try:
        roles = await list_assignable_tenant_roles(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=100,
        )
    except (AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "مجوز واگذاری نقش را ندارید.", show_alert=True)
        return
    if not roles:
        await callback.answer("هیچ نقش قابل واگذاری برای این مجموعه وجود ندارد.", show_alert=True)
        return
    next_token = secrets.token_urlsafe(6)
    role_items = [{"key": role.key, "name": role.name} for role in roles]
    await state.update_data(
        member_restore_token=next_token,
        member_restore_member_id=member_id,
        member_restore_roles=role_items,
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{role['name'][:18]} · {role['key'][:18]}",
                callback_data=f"tenant:member:restore:role:{next_token}:{index}",
            )
        ]
        for index, role in enumerate(role_items[:20])
    ]
    rows.append(
        [InlineKeyboardButton(text="⬅️ بازگشت به فهرست", callback_data=f"tenant:member:restore:cancel:{next_token}")]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "انتخاب نقش جدید برای فعال‌سازی مجدد\n\n"
            f"شناسه عضو: {member_id}\n"
            "نقش قبلی خودکار بازنمی‌گردد؛ یک نقش از همین مجموعه انتخاب کنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("tenant:member:restore:role:"))
async def suspended_member_role_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    token = parts[4] if len(parts) == 6 else ""
    try:
        index = int(parts[5])
        role = data.get("member_restore_roles", [])[index]
        tenant_id, _, _ = await _context_ids(state)
        member_id = UUID(data["member_restore_member_id"])
    except (ValueError, KeyError, IndexError, ResourceNotFound) as exc:
        await callback.answer(str(exc) or "نقش انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    if token != data.get("member_restore_token"):
        await callback.answer("فهرست نقش‌ها قدیمی است.", show_alert=True)
        return
    try:
        preview = await preview_tenant_member_reactivation(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=member_id,
            role_key=role["key"],
        )
    except (AccessDenied, ApplicationError) as exc:
        await callback.answer(str(exc) or "این نقش قابل واگذاری نیست.", show_alert=True)
        return
    await state.update_data(member_restore_role_key=preview.role_key)
    await state.set_state(TenantMemberRestoreWizard.reason)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "دلیل فعال‌سازی مجدد را وارد کنید.\n\n"
            f"عضو: {preview.display_name}\n"
            f"نقش جدید: {preview.role_key}\n"
            "دلیل باید بین ۱۰ تا ۵۰۰ نویسه باشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="لغو", callback_data=f"tenant:member:restore:cancel:{token}")]
                ]
            ),
        )


@router.message(TenantMemberRestoreWizard.reason, F.text)
async def suspended_member_reason_input(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = data.get("member_restore_token")
    reason = (message.text or "").strip()
    if not token or not data.get("member_restore_member_id"):
        await state.set_state(None)
        await message.answer("فرم فعال‌سازی منقضی شده است.")
        return
    if not 10 <= len(reason) <= 500:
        await message.answer("دلیل باید بین ۱۰ تا ۵۰۰ نویسه باشد.")
        return
    try:
        tenant_id, _, _ = await _context_ids(state)
        preview = await preview_tenant_member_reactivation(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=UUID(data["member_restore_member_id"]),
            role_key=data.get("member_restore_role_key", ""),
        )
    except (ResourceNotFound, AccessDenied, ApplicationError, ValueError) as exc:
        await state.set_state(None)
        await message.answer(str(exc) or "عضویت یا نقش دیگر قابل فعال‌سازی نیست.")
        return
    await state.update_data(member_restore_reason=reason)
    await state.set_state(TenantMemberRestoreWizard.confirm)
    permissions = "\n".join(f"• {key}" for key in preview.permission_keys)
    await message.answer(
        "پیش‌نمایش فعال‌سازی مجدد\n\n"
        f"نام: {preview.display_name}\n"
        f"نقش جدید: {preview.role_key}\n"
        f"مجوزها:\n{permissions}\n"
        f"دلیل: {reason}\n\n"
        "تأیید، عضویت را فعال و RoleBinding جدید می‌سازد؛ نقش‌های قبلی احیا نمی‌شوند.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ تأیید فعال‌سازی", callback_data=f"tenant:member:restore:confirm:{token}")],
                [InlineKeyboardButton(text="لغو", callback_data=f"tenant:member:restore:cancel:{token}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("tenant:member:restore:confirm:"))
async def suspended_member_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != TenantMemberRestoreWizard.confirm.state
        or token != data.get("member_restore_token")
    ):
        await callback.answer("این تأیید متعلق به فرم قبلی است.", show_alert=True)
        return
    try:
        tenant_id, _, _ = await _context_ids(state)
        result = await reactivate_tenant_member(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            member_user_id=UUID(data["member_restore_member_id"]),
            role_key=data["member_restore_role_key"],
            reason=data["member_restore_reason"],
        )
    except (ResourceNotFound, AccessDenied, ApplicationError, ValueError) as exc:
        await state.set_state(None)
        await callback.answer(str(exc) or "فعال‌سازی انجام نشد.", show_alert=True)
        return
    await callback.answer("عضویت دوباره فعال شد.")
    if callback.message is not None:
        await _show_suspended_members(
            callback.message,
            db=db,
            principal=principal,
            state=state,
            tenant_id=tenant_id,
            notice=f"✅ عضویت {result.display_name} با نقش {result.role_key} فعال شد.",
        )


@router.callback_query(F.data.startswith("tenant:member:restore:cancel:"))
async def suspended_member_cancel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    data = await state.get_data()
    if token not in {
        data.get("member_restore_token"),
        data.get("member_restore_list_token"),
    }:
        await callback.answer("این فرم دیگر فعال نیست.", show_alert=True)
        return
    try:
        tenant_id, _, _ = await _context_ids(state)
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await _show_suspended_members(
            callback.message,
            db=db,
            principal=principal,
            state=state,
            tenant_id=tenant_id,
            notice="عملیات لغو شد؛ تغییری ثبت نشد.",
        )



async def _show_school_menu(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
    school_id: UUID,
    notice: str | None = None,
) -> None:
    school = await db.get(School, school_id)
    if school is None or school.tenant_id != tenant_id or school.status == "archived":
        await message.answer("مدرسه در مجموعه انتخاب‌شده پیدا نشد.")
        return

    can_read = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="school:read",
    )
    can_year = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="academic_year:create",
    )
    can_class = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="class:create",
    )
    can_subject = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="subject:create",
    )
    can_manage_school = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="school:manage",
    )

    rows: list[list[InlineKeyboardButton]] = []
    if can_read:
        rows.append([
            InlineKeyboardButton(text="📅 سال‌های تحصیلی", callback_data="year:list"),
            InlineKeyboardButton(text="🏫 کلاس‌ها", callback_data="class:list"),
        ])
        rows.append([InlineKeyboardButton(text="📚 درس‌ها", callback_data="subject:list")])
        rows.append([InlineKeyboardButton(text="📡 گروه‌های کلاس‌ها", callback_data="school:groups:1")])
    if can_year:
        rows.append([InlineKeyboardButton(text="➕ ایجاد سال تحصیلی", callback_data="year:create")])
    if can_class:
        rows.append([InlineKeyboardButton(text="➕ ایجاد کلاس", callback_data="class:choose-year")])
    if can_subject:
        rows.append([InlineKeyboardButton(text="➕ افزودن درس", callback_data="subject:create")])
    if can_manage_school:
        rows.append([InlineKeyboardButton(text="🗄 صندوق بازیابی", callback_data=f"life:bin:o:{school.id.hex}:1")])
        rows.append([InlineKeyboardButton(text="✏️ ویرایش نام مدرسه", callback_data=f"life:e:school:{school.id}:name")])
        rows.append([InlineKeyboardButton(text="🗄 بایگانی مدرسه", callback_data=f"life:a:s:{school.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="school:list")])

    body = (
        f"🏫 مدرسه: {school.name}\n"
        f"کد سیستمی: {school.code}\n"
        f"وضعیت: {school.status}\n\n"
        "عملیات موردنظر را انتخاب کنید."
    )
    if notice:
        body = f"{notice}\n\n{body}"
    await _render_panel(message, body, InlineKeyboardMarkup(inline_keyboard=rows))

async def _show_year_menu(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
    school_id: UUID,
    year_id: UUID,
    notice: str | None = None,
) -> None:
    year = await db.get(AcademicYear, year_id)
    if year is None or year.tenant_id != tenant_id or year.school_id != school_id:
        await message.answer("سال تحصیلی در مدرسه انتخاب‌شده پیدا نشد.")
        return
    can_read = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="school:read",
    )
    can_create_class = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="class:create",
    )
    can_manage_year = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="academic_year:manage",
    )
    rows: list[list[InlineKeyboardButton]] = []
    if can_read:
        rows.append([InlineKeyboardButton(text="👥 کلاس‌های این سال", callback_data="class:list")])
    if can_create_class:
        rows.append([InlineKeyboardButton(text="➕ ایجاد کلاس در این سال", callback_data="class:create-selected")])
    if can_manage_year:
        rows.append([InlineKeyboardButton(text="✏️ ویرایش نام سال", callback_data=f"life:e:year:{year.id}:name")])
        rows.append([InlineKeyboardButton(text="🗄 بایگانی سال تحصیلی", callback_data=f"life:a:y:{year.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="year:list")])
    body = (
        f"📅 سال تحصیلی: {year.name}\n"
        f"بازهٔ پیش‌فرض: {year.starts_on.isoformat()} تا {year.ends_on.isoformat()}\n"
        f"وضعیت: {year.status}"
    )
    if notice:
        body = f"{notice}\n\n{body}"
    await _render_panel(message, body, InlineKeyboardMarkup(inline_keyboard=rows))

async def _start_flow(
    state: FSMContext,
    *,
    flow_name: str,
    first_state: State,
) -> str:
    await _clear_flow(state)
    flow_token = secrets.token_urlsafe(9)
    await state.update_data(
        flow_name=flow_name,
        flow_token=flow_token,
    )
    await state.set_state(first_state)
    return flow_token


def _flow_keyboard(flow_token: str, *, confirm: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if confirm:
        rows.append([InlineKeyboardButton(
            text="✅ تأیید و ثبت",
            callback_data=f"structure:confirm:{flow_token}",
        )])
    rows.append([InlineKeyboardButton(
        text="⬅️ بازگشت",
        callback_data=f"structure:back:{flow_token}",
    )])
    rows.append([InlineKeyboardButton(
        text="❌ لغو عملیات",
        callback_data=f"structure:cancel:{flow_token}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _summary(flow_name: str, data: dict) -> str:
    if flow_name == "edit":
        return (
            "پیش‌نمایش ویرایش اطلاعات\n\n"
            f"{data.get('edit_label', 'مورد')}: "
            f"{data.get('edit_original', '-')} ← {data.get('edit_value', '-')}\n\n"
            "کد سیستمی تغییر نمی‌کند. برای ثبت تغییر تأیید کنید."
        )
    summaries = {
        "school": [("نام مدرسه", "school_name")],
        "year": [("نام سال تحصیلی", "year_name")],
        "subject": [("نام درس", "subject_name")],
        "class": [("نام کلاس", "class_name"), ("پایه/مقطع", "class_grade")],
    }
    lines = ["پیش‌نمایش ثبت اطلاعات\n"]
    for label, key in summaries[flow_name]:
        lines.append(f"{label}: {data.get(key, '-')}")
    if flow_name == "year":
        starts_on, ends_on = _default_academic_year_range(data.get("year_name", ""))
        lines.append(f"بازهٔ پیش‌فرض: {starts_on.isoformat()} تا {ends_on.isoformat()}")
    lines.append("\nکد سیستمی به‌صورت خودکار ساخته می‌شود.")
    lines.append("برای ثبت نهایی تأیید کنید.")
    return "\n".join(lines)

async def _render_step(
    message: Message,
    *,
    state: FSMContext,
    step_name: str,
) -> None:
    data = await state.get_data()
    flow_token = data.get("flow_token")
    if not flow_token:
        await _clear_flow(state)
        await message.answer("فرم منقضی شده است. از منو دوباره شروع کنید.")
        return
    if step_name.endswith("_confirm"):
        body = _summary(data.get("flow_name", ""), data)
        keyboard = _flow_keyboard(flow_token, confirm=True)
    else:
        body = (
            data.get("edit_prompt")
            if step_name == StructureWizard.edit_value.state
            else _FIELD_PROMPTS.get(step_name)
        )
        if body is None:
            await message.answer("مرحلهٔ فرم شناخته نشد؛ عملیات را لغو کنید.")
            return
        keyboard = _flow_keyboard(flow_token)

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
    if message.from_user is not None and message.from_user.is_bot:
        try:
            sent = await message.edit_text(body, reply_markup=keyboard)
        except TelegramBadRequest:
            sent = await message.answer(body, reply_markup=keyboard)
    else:
        sent = await message.answer(body, reply_markup=keyboard)
    await state.update_data(
        flow_prompt_message_id=sent.message_id,
        flow_chat_id=sent.chat.id,
    )

async def _show_context_after_flow(
    message: Message,
    *,
    state: FSMContext,
    db: AsyncSession,
    principal: TelegramPrincipal,
) -> None:
    await _clear_flow(state)
    tenant_id, school_id, _ = await _context_ids(state)
    if school_id is not None:
        await _show_school_menu(
            message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
        )
    else:
        await _show_tenant_menu(
            message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
        )



async def _show_tenant_directory(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    page: int = 1,
) -> None:
    if page < 1:
        await message.answer("شماره صفحه معتبر نیست.")
        return
    try:
        tenants = await list_tenants(
            db,
            actor_id=principal.user.id,
            limit=9,
            offset=(page - 1) * 8,
        )
    except AccessDenied:
        tenants = await list_accessible_tenants(
            db,
            actor_id=principal.user.id,
            limit=9,
            offset=(page - 1) * 8,
        )

    has_next = len(tenants) > 8
    visible = tenants[:8]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{tenant.name} · {tenant.slug}",
                callback_data=f"tenant:open:{tenant.id}",
            )
        ]
        for tenant in visible
    ]
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"tenant:directory:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"tenant:directory:{page + 1}",
                )
            )
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="structure:root")])
    body = (
        f"🏫 مدیریت مدارس و کلاس‌ها\n\nصفحه: {page}\nمجموعه را انتخاب کنید:"
        if visible
        else (
            "هنوز مجموعه‌ای ثبت نشده است."
            if page == 1
            else "مجموعه‌ای در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    )
    await _render_panel(
        message,
        body,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^tenant:directory(?::[1-9][0-9]*)?$"))
async def tenant_directory_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _clear_flow(state)
        await state.set_data({})
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        await _show_tenant_directory(
            callback.message,
            principal=principal,
            db=db,
            page=page,
        )

@router.callback_query(F.data.startswith("tenant:open:"))
async def tenant_open_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    raw_id = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    try:
        tenant_id = UUID(raw_id)
    except ValueError:
        await callback.answer("شناسه مجموعه معتبر نیست.", show_alert=True)
        return
    if not await _has_any_school_access(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
    ):
        await callback.answer("به این مجموعه دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=None,
        context_academic_year_id=None,
    )
    if callback.message is not None:
        await _show_tenant_menu(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
        )


@router.callback_query(F.data == "tenant:switch")
async def tenant_switch_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _show_tenant_directory(
            callback.message,
            principal=principal,
            db=db,
        )

@router.callback_query(F.data == "tenant:menu")
async def tenant_menu_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_tenant_menu(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
        )


@router.callback_query(F.data == "structure:root")
async def structure_root_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await _clear_flow(state)
    await state.set_data({})
    await callback.answer()
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        from app.bot.router import main_menu

        await callback.message.answer(
            "به منوی اصلی برگشتید.",
            reply_markup=await main_menu(db, principal),
        )


@router.callback_query(F.data.regexp(r"^school:list(?::[1-9][0-9]*)?$"))
async def school_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        if page < 1:
            raise ValueError
        tenant_id, _, _ = await _context_ids(state)
        fetched = await list_schools(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=9,
            offset=(page - 1) * 8,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return

    has_next = len(fetched) > 8
    schools = fetched[:8]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{school.name} · {school.code}",
                callback_data=f"school:open:{school.id}",
            )
        ]
        for school in schools
    ]
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"school:list:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"school:list:{page + 1}",
                )
            )
        rows.append(navigation)
    if await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="school:create",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    ):
        rows.append([InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="school:create")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="tenant:menu")])

    await callback.answer()
    body = (
        f"🏫 مدارس مجموعه\n\nصفحه: {page} · تعداد در این صفحه: {len(schools)}"
        if schools
        else (
            "هنوز مدرسه‌ای در این مجموعه ثبت نشده است."
            if page == 1
            else "مدرسه‌ای در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    )
    if callback.message is not None:
        await _render_panel(
            callback.message,
            body,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("school:open:"))
async def school_open_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        school_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except (ValueError, ResourceNotFound) as exc:
        await callback.answer(str(exc) or "شناسه مدرسه معتبر نیست.", show_alert=True)
        return
    if not await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="school:read",
    ):
        await callback.answer("به این مدرسه دسترسی ندارید.", show_alert=True)
        return
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(school_id),
        context_academic_year_id=None,
    )
    await callback.answer()
    if callback.message is not None:
        await _show_school_menu(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
        )


@router.callback_query(F.data == "school:menu")
async def school_menu_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_school_menu(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
        )


@router.callback_query(F.data == "school:create")
async def school_create_start(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    allowed = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="school:create",
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    )
    if not allowed:
        await callback.answer("مجوز ایجاد مدرسه را ندارید.", show_alert=True)
        return
    await _start_flow(
        state,
        flow_name="school",
        first_state=StructureWizard.school_name,
    )
    await callback.answer()
    if callback.message is not None:
        await _render_step(
            callback.message,
            state=state,
            step_name=StructureWizard.school_name.state,
        )


@router.callback_query(F.data.regexp(r"^year:list(?::[1-9][0-9]*)?$"))
async def academic_year_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        if page < 1:
            raise ValueError
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        fetched = await list_academic_years(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=9,
            offset=(page - 1) * 8,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return

    has_next = len(fetched) > 8
    years = fetched[:8]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{year.name} · {year.status}",
                callback_data=f"year:open:{year.id}",
            )
        ]
        for year in years
    ]
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"year:list:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"year:list:{page + 1}",
                )
            )
        rows.append(navigation)
    if await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="academic_year:create",
    ):
        rows.append([InlineKeyboardButton(text="➕ ایجاد سال تحصیلی", callback_data="year:create")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="school:menu")])

    await callback.answer()
    body = (
        f"📅 سال‌های تحصیلی\n\nصفحه: {page} · تعداد در این صفحه: {len(years)}"
        if years
        else (
            "هنوز سال تحصیلی ثبت نشده است. برای ساخت کلاس ابتدا یک سال تحصیلی بسازید."
            if page == 1
            else "سالی در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    )
    if callback.message is not None:
        await _render_panel(
            callback.message,
            body,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("year:open:"))
async def academic_year_open_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        year_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
        await db.get(AcademicYear, year_id)
        await list_academic_years(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=100,
        )
        year = await db.get(AcademicYear, year_id)
        if year is None or year.tenant_id != tenant_id or year.school_id != school_id:
            raise ResourceNotFound("سال تحصیلی در مدرسه انتخاب‌شده پیدا نشد.")
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "سال تحصیلی معتبر نیست.", show_alert=True)
        return
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(school_id),
        context_academic_year_id=str(year_id),
    )
    await callback.answer()
    if callback.message is not None:
        await _show_year_menu(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
            year_id=year_id,
        )



@router.callback_query(F.data == "year:menu")
async def academic_year_menu_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        if school_id is None or year_id is None:
            raise ResourceNotFound("ابتدا سال تحصیلی را انتخاب کنید.")
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_year_menu(
            callback.message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
            year_id=year_id,
        )

@router.callback_query(F.data == "year:create")
async def academic_year_create_start(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="academic_year:create",
    ):
        await callback.answer("مجوز ایجاد سال تحصیلی را ندارید.", show_alert=True)
        return
    await _start_flow(
        state,
        flow_name="year",
        first_state=StructureWizard.year_name,
    )
    await callback.answer()
    if callback.message is not None:
        await _render_step(
            callback.message,
            state=state,
            step_name=StructureWizard.year_name.state,
        )


@router.callback_query(F.data.regexp(r"^subject:list(?::[1-9][0-9]*)?$"))
async def subject_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        if page < 1:
            raise ValueError
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        fetched = await list_subjects(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=9,
            offset=(page - 1) * 8,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return

    has_next = len(fetched) > 8
    subjects = fetched[:8]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{subject.name} · {subject.code}",
                callback_data=f"subject:open:{subject.id}",
            )
        ]
        for subject in subjects
    ]
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"subject:list:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"subject:list:{page + 1}",
                )
            )
        rows.append(navigation)
    if await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="subject:create",
    ):
        rows.append([InlineKeyboardButton(text="➕ افزودن درس", callback_data="subject:create")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="school:menu")])

    await callback.answer()
    body = (
        f"📚 درس‌های مدرسه\n\nصفحه: {page} · تعداد در این صفحه: {len(subjects)}"
        if subjects
        else (
            "هنوز درسی ثبت نشده است."
            if page == 1
            else "درسی در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    )
    if callback.message is not None:
        await _render_panel(
            callback.message,
            body,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("subject:open:"))
async def subject_open_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        subject_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
        subjects = await list_subjects(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=100,
        )
        subject = next((item for item in subjects if item.id == subject_id), None)
        if subject is None:
            raise ResourceNotFound("درس موردنظر در این مدرسه پیدا نشد.")
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    can_manage_subject = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="subject:manage",
    )
    buttons = []
    if can_manage_subject:
        buttons.append([InlineKeyboardButton(text="✏️ ویرایش نام درس", callback_data=f"life:e:subject:{subject.id}:name")])
        buttons.append([InlineKeyboardButton(text="🗄 بایگانی درس", callback_data=f"life:a:u:{subject.id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="subject:list")])
    await callback.answer()
    if callback.message is not None:
        await _render_panel(
            callback.message,
            f"📚 درس: {subject.name}\nکد سیستمی: {subject.code}\nوضعیت: {subject.status}",
            InlineKeyboardMarkup(inline_keyboard=buttons),
        )

@router.callback_query(F.data == "subject:create")
async def subject_create_start(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
    except ResourceNotFound as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="subject:create",
    ):
        await callback.answer("مجوز ایجاد درس را ندارید.", show_alert=True)
        return
    await _start_flow(
        state,
        flow_name="subject",
        first_state=StructureWizard.subject_name,
    )
    await callback.answer()
    if callback.message is not None:
        await _render_step(
            callback.message,
            state=state,
            step_name=StructureWizard.subject_name.state,
        )


@router.callback_query(F.data.regexp(r"^class:list(?::[1-9][0-9]*)?$"))
async def class_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        if page < 1:
            raise ValueError
        tenant_id, school_id, year_id = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        fetched = await list_classes(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            academic_year_id=year_id,
            limit=9,
            offset=(page - 1) * 8,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return

    has_next = len(fetched) > 8
    classes = fetched[:8]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{item.name} · پایه {item.grade}",
                callback_data=f"class:open:{item.id}",
            )
        ]
        for item in classes
    ]
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"class:list:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"class:list:{page + 1}",
                )
            )
        rows.append(navigation)
    if await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="class:create",
    ):
        rows.append([InlineKeyboardButton(text="➕ ایجاد کلاس", callback_data="class:choose-year")])

    parent_callback = "year:menu" if year_id is not None else "school:menu"
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data=parent_callback)])

    await callback.answer()
    heading = "کلاس‌های سال انتخاب‌شده" if year_id is not None else "کلاس‌های مدرسه"
    body = (
        f"🏫 {heading}\n\nصفحه: {page} · تعداد در این صفحه: {len(classes)}"
        if classes
        else (
            "در این محدوده کلاسی ثبت نشده است."
            if page == 1
            else "کلاسی در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    )
    if callback.message is not None:
        await _render_panel(
            callback.message,
            body,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("class:open:"))
async def class_open_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        class_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
        classes = await list_classes(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            academic_year_id=year_id,
            limit=100,
        )
        class_item = next((item for item in classes if item.id == class_id), None)
        if class_item is None:
            raise ResourceNotFound("کلاس در محدوده انتخاب‌شده پیدا نشد.")
        academic_year = await db.get(AcademicYear, class_item.academic_year_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    parent_callback = "year:menu" if year_id is not None else "class:list"
    can_manage_group = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="class:group:manage",
        tenant_id=class_item.tenant_id,
        scope_type="class",
        scope_id=class_item.id,
    )
    buttons = []
    if can_manage_group:
        buttons.append([
            InlineKeyboardButton(
                text="📡 مدیریت گروه تلگرام",
                callback_data=f"class:group:{class_item.id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="📚 مدیریت درس‌های کلاس",
            callback_data=f"cs:o:{class_item.id.hex}",
        )
    ])
    can_manage_class = await _has_school_permission(
        db, actor_id=principal.user.id, tenant_id=tenant_id,
        school_id=school_id, permission="class:manage",
    )
    if can_manage_class:
        buttons.append([InlineKeyboardButton(text="✏️ ویرایش نام کلاس", callback_data=f"life:e:class:{class_item.id}:name")])
        buttons.append([InlineKeyboardButton(text="✏️ ویرایش پایه/مقطع", callback_data=f"life:e:class:{class_item.id}:grade")])
        buttons.append([InlineKeyboardButton(text="🗄 بایگانی کلاس", callback_data=f"life:a:c:{class_item.id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data=parent_callback)])
    await callback.answer()
    if callback.message is not None:
        await _render_panel(
            callback.message,
            (
                f"🏫 کلاس: {class_item.name}\n"
                f"پایه: {class_item.grade}\n"
                f"کد سیستمی: {class_item.code}\n"
                f"سال تحصیلی: {academic_year.name if academic_year else '-'}\n"
                f"وضعیت: {class_item.status}"
            ),
            InlineKeyboardMarkup(inline_keyboard=buttons),
        )

@router.callback_query(F.data == "class:create-selected")
async def class_create_selected_year_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        if school_id is None or year_id is None:
            raise ResourceNotFound("ابتدا سال تحصیلی را انتخاب کنید.")
        if not await _has_school_permission(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            permission="class:create",
        ):
            raise AccessDenied
        years = await list_academic_years(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=100,
        )
        if not any(item.id == year_id for item in years):
            raise ResourceNotFound("سال تحصیلی در مدرسه انتخاب‌شده پیدا نشد.")
    except ApplicationError as exc:
        await callback.answer(str(exc) or "دسترسی ندارید.", show_alert=True)
        return
    await _start_flow(
        state,
        flow_name="class",
        first_state=StructureWizard.class_name,
    )
    await callback.answer()
    if callback.message is not None:
        await _render_step(
            callback.message,
            state=state,
            step_name=StructureWizard.class_name.state,
        )


@router.callback_query(F.data == "class:choose-year")
async def class_choose_year_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        if not await _has_school_permission(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            permission="class:create",
        ):
            raise AccessDenied
        years = await list_academic_years(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=50,
        )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if len(years) == 1:
        await _clear_flow(state)
        await state.update_data(
            context_tenant_id=str(tenant_id),
            context_school_id=str(school_id),
            context_academic_year_id=str(years[0].id),
        )
        await _start_flow(
            state,
            flow_name="class",
            first_state=StructureWizard.class_name,
        )
        await callback.answer()
        if callback.message is not None:
            await _render_step(
                callback.message,
                state=state,
                step_name=StructureWizard.class_name.state,
            )
        return

    rows = [
        [InlineKeyboardButton(text=year.name, callback_data=f"class:year:{year.id}")]
        for year in years
    ]
    if not years and await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="academic_year:create",
    ):
        rows.append([InlineKeyboardButton(text="➕ ایجاد سال تحصیلی", callback_data="year:create")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="school:menu")])

    body = (
        "برای ایجاد کلاس، سال تحصیلی را انتخاب کنید:"
        if years
        else "برای این مدرسه هنوز سال تحصیلی ثبت نشده است. ابتدا یک سال تحصیلی بسازید."
    )
    await callback.answer()
    if callback.message is not None:
        await _render_panel(
            callback.message,
            body,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )

@router.callback_query(F.data.startswith("class:year:"))
async def class_year_selected_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        year_id = UUID((callback.data or "").rsplit(":", maxsplit=1)[-1])
        years = await list_academic_years(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=100,
        )
        year = next((item for item in years if item.id == year_id), None)
        if year is None:
            raise ResourceNotFound("سال تحصیلی در مدرسه انتخاب‌شده پیدا نشد.")
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(school_id),
        context_academic_year_id=str(year_id),
    )
    await _start_flow(
        state,
        flow_name="class",
        first_state=StructureWizard.class_name,
    )
    await callback.answer()
    if callback.message is not None:
        await _render_step(
            callback.message,
            state=state,
            step_name=StructureWizard.class_name.state,
        )


async def _accept_field(
    message: Message,
    state: FSMContext,
    *,
    key: str,
    next_state: State,
    validator,
    error_message: str,
) -> None:
    value = (message.text or "").strip()
    normalized = validator(value)
    if normalized is None:
        await message.answer(error_message)
        return
    await state.update_data(**{key: normalized})
    await state.set_state(next_state)
    await _render_step(
        message,
        state=state,
        step_name=next_state.state,
    )


@router.message(StructureWizard.school_name)
async def school_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="school_name",
        next_state=StructureWizard.school_confirm,
        validator=lambda value: value if 2 <= len(value) <= 180 else None,
        error_message="نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.",
    )


@router.message(StructureWizard.year_name)
async def year_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="year_name",
        next_state=StructureWizard.year_confirm,
        validator=lambda value: value if 1 <= len(value) <= 64 else None,
        error_message="نام سال تحصیلی باید بین ۱ تا ۶۴ نویسه باشد.",
    )


@router.message(StructureWizard.subject_name)
async def subject_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="subject_name",
        next_state=StructureWizard.subject_confirm,
        validator=lambda value: value if 1 <= len(value) <= 120 else None,
        error_message="نام درس باید بین ۱ تا ۱۲۰ نویسه باشد.",
    )


@router.message(StructureWizard.class_name)
async def class_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="class_name",
        next_state=StructureWizard.class_grade,
        validator=lambda value: value if 1 <= len(value) <= 120 else None,
        error_message="نام کلاس باید بین ۱ تا ۱۲۰ نویسه باشد.",
    )



@router.callback_query(F.data.startswith("life:e:"))
async def structure_edit_start_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("درخواست ویرایش معتبر نیست.", show_alert=True)
        return
    resource, raw_id, field = parts[2], parts[3], parts[4]
    permissions = {
        "school": "school:manage",
        "year": "academic_year:manage",
        "class": "class:manage",
        "subject": "subject:manage",
    }
    fields = {
        "school": {"name": "نام مدرسه"},
        "year": {"name": "نام سال تحصیلی"},
        "class": {"name": "نام کلاس", "grade": "پایه/مقطع"},
        "subject": {"name": "نام درس"},
    }
    if resource not in permissions or field not in fields[resource]:
        await callback.answer("این نوع ویرایش پشتیبانی نمی‌شود.", show_alert=True)
        return

    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        resource_id = UUID(raw_id)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        if not await _has_school_permission(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            permission=permissions[resource],
        ):
            raise AccessDenied("مجوز ویرایش این مورد را ندارید.")

        entity_models = {
            "school": School,
            "year": AcademicYear,
            "class": ClassGroup,
            "subject": Subject,
        }
        entity = await db.get(entity_models[resource], resource_id)
        if entity is None or entity.tenant_id != tenant_id or entity.status == "archived":
            raise ResourceNotFound("مورد فعال پیدا نشد.")
        if resource == "school":
            if entity.id != school_id:
                raise ResourceNotFound("مدرسه با زمینهٔ انتخاب‌شده تطابق ندارد.")
        elif entity.school_id != school_id:
            raise ResourceNotFound("این مورد به مدرسهٔ انتخاب‌شده تعلق ندارد.")
        if resource == "year" and year_id is not None and entity.id != year_id:
            raise ResourceNotFound("سال تحصیلی با محدودهٔ انتخاب‌شده تطابق ندارد.")
        current_value = getattr(entity, field)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شناسه معتبر نیست.", show_alert=True)
        return

    await _start_flow(
        state,
        flow_name="edit",
        first_state=StructureWizard.edit_value,
    )
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(school_id),
        context_academic_year_id=str(year_id) if year_id else None,
        edit_resource=resource,
        edit_resource_id=str(resource_id),
        edit_field=field,
        edit_label=fields[resource][field],
        edit_original=str(current_value),
        edit_prompt=(
            f"{fields[resource][field]} فعلی: {current_value}\n"
            "مقدار جدید را وارد کنید:"
        ),
    )
    await callback.answer()
    if callback.message is not None:
        await _render_step(
            callback.message,
            state=state,
            step_name=StructureWizard.edit_value.state,
        )


@router.message(StructureWizard.edit_value)
async def structure_edit_value_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    resource = data.get("edit_resource")
    field = data.get("edit_field")
    limits = {
        ("school", "name"): (2, 180),
        ("year", "name"): (1, 64),
        ("class", "name"): (1, 120),
        ("class", "grade"): (1, 48),
        ("subject", "name"): (1, 120),
    }
    minimum, maximum = limits.get((resource, field), (1, 0))
    await _accept_field(
        message,
        state,
        key="edit_value",
        next_state=StructureWizard.edit_confirm,
        validator=lambda value: value if minimum <= len(value) <= maximum else None,
        error_message=(
            f"{data.get('edit_label', 'مقدار')} باید بین "
            f"{minimum} تا {maximum} نویسه باشد."
        ),
    )


@router.callback_query(F.data.startswith("structure:confirm:"))
async def structure_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    flow_name = data.get("flow_name")
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        flow_name not in _CONFIRM_STATES
        or await state.get_state() != _CONFIRM_STATES[flow_name]
        or not token
        or token != data.get("flow_token")
    ):
        await callback.answer(
            "این دکمه متعلق به فرم قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return

    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        if flow_name in {"year", "subject", "class"} and school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")

        if flow_name == "school":
            entity = await create_school(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                name=data.get("school_name", ""),
                code=_generated_code("SCH"),
            )
            result_text = f"مدرسه ایجاد شد: {entity.name} · کد سیستمی {entity.code}"
            success_school_id = entity.id
            success_year_id = None
            next_message = "اکنون می‌توانید سال تحصیلی این مدرسه را ثبت کنید."
        elif flow_name == "year":
            starts_on, ends_on = _default_academic_year_range(data.get("year_name", ""))
            entity = await create_academic_year(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                name=data.get("year_name", ""),
                starts_on=starts_on,
                ends_on=ends_on,
            )
            result_text = (
                f"سال تحصیلی {entity.name} ایجاد شد. "
                f"بازهٔ پیش‌فرض {entity.starts_on.isoformat()} تا {entity.ends_on.isoformat()}"
            )
            success_school_id = school_id
            success_year_id = entity.id
            next_message = "اکنون می‌توانید کلاس‌های این سال را ایجاد کنید."
        elif flow_name == "subject":
            entity = await create_subject(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                name=data.get("subject_name", ""),
                code=_generated_code("SUB"),
            )
            result_text = f"درس {entity.name} افزوده شد."
            success_school_id = school_id
            success_year_id = year_id
            next_message = "درس به فهرست درس‌های مدرسه اضافه شد."
        elif flow_name == "edit":
            resource = data.get("edit_resource")
            field = data.get("edit_field")
            resource_id = UUID(data.get("edit_resource_id", ""))
            value = data.get("edit_value", "")
            if resource == "school" and field == "name":
                entity = await update_school(
                    db,
                    actor_id=principal.user.id,
                    tenant_id=tenant_id,
                    school_id=resource_id,
                    name=value,
                )
                result_text = f"نام مدرسه به «{entity.name}» تغییر کرد."
                success_school_id = entity.id
                success_year_id = None
            elif resource == "year" and field == "name":
                entity = await update_academic_year(
                    db,
                    actor_id=principal.user.id,
                    tenant_id=tenant_id,
                    school_id=school_id,
                    academic_year_id=resource_id,
                    name=value,
                )
                result_text = f"نام سال تحصیلی به «{entity.name}» تغییر کرد."
                success_school_id = school_id
                success_year_id = entity.id
            elif resource == "class" and field in {"name", "grade"}:
                entity = await update_class(
                    db,
                    actor_id=principal.user.id,
                    tenant_id=tenant_id,
                    school_id=school_id,
                    class_group_id=resource_id,
                    name=value if field == "name" else None,
                    grade=value if field == "grade" else None,
                )
                result_text = f"{data.get('edit_label')} کلاس به «{value}» تغییر کرد."
                success_school_id = school_id
                success_year_id = year_id
            elif resource == "subject" and field == "name":
                entity = await update_subject(
                    db,
                    actor_id=principal.user.id,
                    tenant_id=tenant_id,
                    school_id=school_id,
                    subject_id=resource_id,
                    name=value,
                )
                result_text = f"نام درس به «{entity.name}» تغییر کرد."
                success_school_id = school_id
                success_year_id = None
            else:
                raise ResourceNotFound("عملیات ویرایش معتبر نیست.")
            next_message = "تغییرات ذخیره شدند."
        else:
            if year_id is None:
                raise ResourceNotFound("سال تحصیلی انتخاب نشده است.")
            entity = await create_class(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                academic_year_id=year_id,
                name=data.get("class_name", ""),
                code=_generated_code("CLS"),
                grade=data.get("class_grade", ""),
            )
            result_text = f"کلاس {entity.name} ایجاد شد."
            success_school_id = school_id
            success_year_id = year_id
            next_message = "کلاس به فهرست کلاس‌های این سال اضافه شد."
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except ValueError:
        await callback.answer(
            "نام سال تحصیلی یا اطلاعات واردشده معتبر نیست.",
            show_alert=True,
        )
        return

    await callback.answer("ثبت انجام شد.")
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(success_school_id) if success_school_id else None,
        context_academic_year_id=str(success_year_id) if success_year_id else None,
    )
    if callback.message is not None:
        notice = f"✅ {result_text}\n{next_message}"
        if success_school_id is not None and success_year_id is not None:
            await _show_year_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                school_id=success_school_id,
                year_id=success_year_id,
                notice=notice,
            )
        elif success_school_id is not None:
            await _show_school_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                school_id=success_school_id,
                notice=notice,
            )
        else:
            await _show_tenant_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                notice=notice,
            )


@router.message(StructureWizard.class_grade)
async def class_grade_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="class_grade",
        next_state=StructureWizard.class_confirm,
        validator=lambda value: value if 1 <= len(value) <= 48 else None,
        error_message="پایه/مقطع باید بین ۱ تا ۴۸ نویسه باشد.",
    )

async def _return_to_context_menu(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    principal: TelegramPrincipal,
    notice: str | None = None,
) -> None:
    await _clear_flow(state)
    try:
        tenant_id, school_id, year_id = await _context_ids(state)
    except ResourceNotFound:
        return

    if school_id is not None and year_id is not None:
        await _show_year_menu(
            message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
            year_id=year_id,
            notice=notice,
        )
    elif school_id is not None:
        await _show_school_menu(
            message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            school_id=school_id,
            notice=notice,
        )
    else:
        await _show_tenant_menu(
            message,
            db=db,
            principal=principal,
            tenant_id=tenant_id,
            notice=notice,
        )

@router.callback_query(F.data.startswith("structure:back:"))
async def structure_back_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    current = await state.get_state()
    if not token or token != data.get("flow_token"):
        await callback.answer("این دکمه متعلق به فرم قبلی است یا منقضی شده است.", show_alert=True)
        return
    if current in _PREVIOUS:
        await state.set_state(_PREVIOUS[current])
        await callback.answer()
        if callback.message is not None:
            await _render_step(
                callback.message,
                state=state,
                step_name=_PREVIOUS[current].state,
            )
        return
    await callback.answer()
    if callback.message is not None:
        await _return_to_context_menu(callback.message, state, db, principal)

@router.callback_query(F.data.startswith("structure:cancel:"))
async def structure_cancel_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if not token or token != data.get("flow_token"):
        await callback.answer(
            "این دکمه متعلق به فرم قبلی است یا منقضی شده است.",
            show_alert=True,
        )
        return
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await _return_to_context_menu(
            callback.message,
            state,
            db,
            principal,
            notice="❌ عملیات لغو شد؛ هیچ تغییری ثبت نشد.",
        )
    else:
        await _clear_flow(state)

@router.message(Command("back"))
async def structure_back_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
):
    current = await state.get_state()
    if not current or not current.startswith("StructureWizard:"):
        return UNHANDLED
    if current in _PREVIOUS:
        await state.set_state(_PREVIOUS[current])
        await _render_step(
            message,
            state=state,
            step_name=_PREVIOUS[current].state,
        )
    else:
        await _return_to_context_menu(message, state, db, principal)
    return None


@router.message(Command("cancel"))
async def structure_cancel_command(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
):
    current = await state.get_state()
    if not current or not current.startswith("StructureWizard:"):
        return UNHANDLED
    await message.answer("عملیات لغو شد؛ هیچ تغییری ثبت نشد.")
    await _return_to_context_menu(message, state, db, principal)
    return None



@router.callback_query(F.data.startswith("life:ac:"))
async def structure_archive_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("درخواست بایگانی معتبر نیست.", show_alert=True)
        return
    code, raw_id = parts[2], parts[3]
    resource_map = {"s": "school", "y": "year", "c": "class", "u": "subject"}
    resource = resource_map.get(code)
    if resource is None:
        await callback.answer("نوع مورد برای بایگانی شناخته نشد.", show_alert=True)
        return
    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        resource_id = UUID(raw_id)
        if resource == "school":
            if school_id != resource_id:
                raise ResourceNotFound("مدرسه با زمینهٔ انتخاب‌شده تطابق ندارد.")
            changed = await archive_school(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=resource_id,
            )
            next_school_id = None
            next_year_id = None
        elif resource == "year":
            if school_id is None or (year_id is not None and year_id != resource_id):
                raise ResourceNotFound("سال تحصیلی با زمینهٔ انتخاب‌شده تطابق ندارد.")
            changed = await archive_academic_year(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                academic_year_id=resource_id,
            )
            next_school_id = school_id
            next_year_id = None
        elif resource == "class":
            if school_id is None:
                raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
            changed = await archive_class(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                class_group_id=resource_id,
            )
            next_school_id = school_id
            next_year_id = year_id
        else:
            if school_id is None:
                raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
            changed = await archive_subject(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                subject_id=resource_id,
            )
            next_school_id = school_id
            next_year_id = None
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شناسه معتبر نیست.", show_alert=True)
        return

    await callback.answer(
        "بایگانی انجام شد." if changed else "این مورد قبلاً بایگانی شده بود."
    )
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(next_school_id) if next_school_id else None,
        context_academic_year_id=str(next_year_id) if next_year_id else None,
    )
    if callback.message is not None:
        notice = "✅ بایگانی انجام شد؛ سابقه و کد سیستمی حذف نشده‌اند."
        if resource == "school":
            await _show_tenant_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                notice=notice,
            )
        elif resource == "class" and year_id is not None:
            await _show_year_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                school_id=school_id,
                year_id=year_id,
                notice=notice,
            )
        else:
            await _show_school_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                school_id=school_id,
                notice=notice,
            )


@router.callback_query(F.data.startswith("life:a:"))
async def structure_archive_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("درخواست بایگانی معتبر نیست.", show_alert=True)
        return
    code, raw_id = parts[2], parts[3]
    labels = {
        "s": (
            "مدرسه",
            "بایگانی مدرسه فقط زمانی انجام می‌شود که سال تحصیلی، کلاس یا درس فعالی نداشته باشد.",
            "school:menu",
            "school:manage",
        ),
        "y": (
            "سال تحصیلی",
            "این سال فقط زمانی بایگانی می‌شود که کلاس فعال نداشته باشد.",
            "year:menu",
            "academic_year:manage",
        ),
        "c": (
            "کلاس",
            "اتصال گروه تلگرام لغو و تخصیص درس‌ها غیرفعال می‌شوند؛ تاریخچه باقی می‌ماند.",
            "class:list",
            "class:manage",
        ),
        "u": (
            "درس",
            "تخصیص درس از کلاس‌ها غیرفعال می‌شود؛ سابقه و اصل رکورد حذف نمی‌شود.",
            "subject:list",
            "subject:manage",
        ),
    }
    item = labels.get(code)
    if item is None:
        await callback.answer("نوع مورد برای بایگانی شناخته نشد.", show_alert=True)
        return
    title, explanation, parent_callback, permission = item
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        resource_id = UUID(raw_id)
        if school_id is None or not await _has_school_permission(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            permission=permission,
        ):
            raise AccessDenied("مجوز بایگانی این مورد را ندارید.")
        if code == "s" and school_id != resource_id:
            raise ResourceNotFound("مدرسه با زمینهٔ انتخاب‌شده تطابق ندارد.")
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شناسه معتبر نیست.", show_alert=True)
        return

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا {title} بایگانی شود؟\n\n{explanation}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="تأیید بایگانی",
                            callback_data=f"life:ac:{code}:{raw_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="لغو",
                            callback_data=parent_callback,
                        )
                    ],
                ]
            ),
        )




@router.callback_query(F.data.regexp(r"^life:bin:s:[0-9]+$"))
async def archived_schools_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    try:
        tenant_id, _, _ = await _context_ids(state)
        page = int(parts[3])
        if page < 1:
            raise ValueError
        result = await list_archived_schools(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=8,
            offset=(page - 1) * 8,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if result["pages"] and page > result["pages"]:
        page = result["pages"]
        result = await list_archived_schools(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=8,
            offset=(page - 1) * 8,
        )
    await callback.answer()
    lines = [
        "╔  🗄 مدارس بایگانی‌شده",
        "║",
        f"╣  تعداد: {result['total']:,}",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for item in result["items"]:
        lines.append(f"╣  🏫 {item.name[:72]} · {item.code}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"♻️ بازیابی · {item.name[:38]}",
                    callback_data=f"life:rp:s:{item.id.hex}:{page}",
                )
            ]
        )
    if not result["items"]:
        lines.append("╣  مدرسهٔ بایگانی‌شده‌ای وجود ندارد.")
    lines.extend(["║", f"╚  صفحهٔ {page} از {max(1, result['pages'])}"])
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"life:bin:s:{page - 1}",
            )
        ] if page > 1 else [
            InlineKeyboardButton(text="⬅️ بازگشت به مجموعه", callback_data="tenant:menu")
        ]
    )
    if page < result["pages"]:
        rows[-1].append(
            InlineKeyboardButton(
                text="صفحه بعد ➡️",
                callback_data=f"life:bin:s:{page + 1}",
            )
        )
    if callback.message is not None:
        await callback.message.edit_text(
            "\\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^life:bin:o:[0-9a-fA-F]{32}:[0-9]+$"))
async def archived_school_structure_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    try:
        tenant_id, current_school_id, _ = await _context_ids(state)
        school_id = UUID(hex=parts[3])
        page = int(parts[4])
        if current_school_id != school_id or page < 1:
            raise ValueError
        result = await list_archived_school_structure(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=8,
            offset=(page - 1) * 8,
        )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if result["pages"] and page > result["pages"]:
        page = result["pages"]
        result = await list_archived_school_structure(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=8,
            offset=(page - 1) * 8,
        )
    await callback.answer()
    labels = {
        "year": ("📅", "سال تحصیلی", "y"),
        "class": ("🏫", "کلاس", "c"),
        "subject": ("📚", "درس", "u"),
    }
    lines = [
        "╔  🗄 صندوق بازیابی مدرسه",
        "║",
        f"╣  مدرسه: {result['school_name']}",
        f"╣  موارد بایگانی‌شده: {result['total']:,}",
        "║",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for item in result["items"]:
        icon, label, code = labels[item["kind"]]
        lines.append(f"╣  {icon} {label}: {item['name'][:72]}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"♻️ بازیابی {label} · {item['name'][:28]}",
                    callback_data=f"life:rp:{code}:{item['id'].hex}:{page}",
                )
            ]
        )
    if not result["items"]:
        lines.append("╣  مورد بایگانی‌شده‌ای برای این مدرسه وجود ندارد.")
    lines.extend(["║", f"╚  صفحهٔ {page} از {max(1, result['pages'])}"])
    navigation = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"life:bin:o:{school_id.hex}:{page - 1}",
            )
        )
    if page < result["pages"]:
        navigation.append(
            InlineKeyboardButton(
                text="صفحه بعد ➡️",
                callback_data=f"life:bin:o:{school_id.hex}:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [InlineKeyboardButton(text="⬅️ بازگشت به مدرسه", callback_data="school:menu")]
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "\\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^life:rp:[sycu]:[0-9a-fA-F]{32}:[0-9]+$"))
async def restore_structure_prompt_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    code, raw_id, page = parts[2], parts[3], parts[4]
    labels = {
        "s": ("مدرسه", "وضعیت مدرسه به «آماده‌سازی» برمی‌گردد؛ موارد داخلی همچنان جداگانه بایگانی می‌مانند."),
        "y": ("سال تحصیلی", "سال به وضعیت «برنامه‌ریزی‌شده» برمی‌گردد؛ کلاس‌های بایگانی‌شده خودکار فعال نمی‌شوند."),
        "c": ("کلاس", "تخصیص‌های درسِ تعلیق‌شده فقط در صورت فعال بودن درس بازمی‌گردند؛ اتصال گروه تلگرام باید مجدداً انجام شود."),
        "u": ("درس", "تخصیص‌های تعلیق‌شده فقط برای کلاس‌های فعال بازمی‌گردند؛ تخصیص‌های دستیِ برداشته‌شده برنمی‌گردند."),
    }
    title, explanation = labels[code]
    if code == "s":
        back_callback = f"life:bin:s:{page}"
    else:
        data = await state.get_data()
        school_value = data.get("context_school_id")
        if not school_value:
            await callback.answer("مدرسهٔ انتخاب‌شده منقضی شده است.", show_alert=True)
            return
        back_callback = f"life:bin:o:{UUID(school_value).hex}:{page}"
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا {title} بازیابی شود؟\\n\\n{explanation}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="تأیید بازیابی",
                            callback_data=f"life:rc:{code}:{raw_id}:{page}",
                        )
                    ],
                    [InlineKeyboardButton(text="لغو", callback_data=back_callback)],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^life:rc:[sycu]:[0-9a-fA-F]{32}:[0-9]+$"))
async def restore_structure_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    code, raw_id, page = parts[2], parts[3], int(parts[4])
    resource_id = UUID(hex=raw_id)
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if code == "s":
            restored = await restore_school(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=resource_id,
            )
            await callback.answer(
                "مدرسه بازیابی شد." if restored else "این مدرسه قبلاً بازیابی شده بود."
            )
            await _clear_flow(state)
            await state.update_data(
                context_tenant_id=str(tenant_id),
                context_school_id=None,
                context_academic_year_id=None,
            )
            if callback.message is not None:
                await _show_tenant_menu(
                    callback.message,
                    db=db,
                    principal=principal,
                    tenant_id=tenant_id,
                    notice="✅ مدرسه بازیابی شد؛ موارد داخلی آن همچنان جداگانه بایگانی هستند.",
                )
            return

        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        operations = {
            "y": restore_academic_year,
            "c": restore_class,
            "u": restore_subject,
        }
        operation = operations[code]
        kwargs = {
            "db": db,
            "actor_id": principal.user.id,
            "tenant_id": tenant_id,
            "school_id": school_id,
        }
        if code == "y":
            restored = await operation(**kwargs, academic_year_id=resource_id)
        elif code == "c":
            restored = await operation(**kwargs, class_group_id=resource_id)
        else:
            restored = await operation(**kwargs, subject_id=resource_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "بازیابی ممکن نیست.", show_alert=True)
        return

    await callback.answer(
        "بازیابی انجام شد." if restored else "این مورد قبلاً بازیابی شده بود."
    )
    await _clear_flow(state)
    await state.update_data(
        context_tenant_id=str(tenant_id),
        context_school_id=str(school_id),
        context_academic_year_id=None,
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "✅ بازیابی انجام شد. برای مشاهدهٔ فهرست به‌روز صندوق بایگانی برگردید.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به صندوق بازیابی",
                            callback_data=f"life:bin:o:{school_id.hex}:{page}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="بازگشت به مدرسه",
                            callback_data="school:menu",
                        )
                    ],
                ]
            ),
        )



@router.callback_query()
async def structure_unhandled_callback(callback: CallbackQuery):
    return UNHANDLED
