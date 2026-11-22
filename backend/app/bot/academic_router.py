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
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    AccessDenied,
    ApplicationError,
    ResourceNotFound,
)
from app.application.tenants import (
    create_academic_year,
    create_class,
    create_school,
    create_subject,
    list_academic_years,
    list_accessible_tenants,
    list_classes,
    list_schools,
    list_subjects,
    list_tenants,
)
from app.db.models import AcademicYear, School, Tenant
from app.modules.access.policy import has_permission
from app.modules.identity.principal import TelegramPrincipal

router = Router(name="academic-structure")


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
}
_CONFIRM_STATES = {
    "school": StructureWizard.school_confirm.state,
    "year": StructureWizard.year_confirm.state,
    "subject": StructureWizard.subject_confirm.state,
    "class": StructureWizard.class_confirm.state,
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
    can_list: bool,
    can_create: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_list:
        rows.append([InlineKeyboardButton(text="🏫 مدارس مجموعه", callback_data="school:list")])
    if can_create:
        rows.append([InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="school:create")])
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
    body = (
        f"🏢 مدیریت مجموعه: {tenant.name}\n"
        f"شناسه: {tenant.slug}\n\n"
        "مدیریت مدارس، سال‌های تحصیلی، کلاس‌ها و درس‌ها از این بخش انجام می‌شود."
    )
    if notice:
        body = f"{notice}\n\n{body}"
    await _render_panel(message, body, _tenant_keyboard(can_list=can_list, can_create=can_create))

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

    rows: list[list[InlineKeyboardButton]] = []
    if can_read:
        rows.append([
            InlineKeyboardButton(text="📅 سال‌های تحصیلی", callback_data="year:list"),
            InlineKeyboardButton(text="🏫 کلاس‌ها", callback_data="class:list"),
        ])
        rows.append([InlineKeyboardButton(text="📚 درس‌ها", callback_data="subject:list")])
    if can_year:
        rows.append([InlineKeyboardButton(text="➕ ایجاد سال تحصیلی", callback_data="year:create")])
    if can_class:
        rows.append([InlineKeyboardButton(text="➕ ایجاد کلاس", callback_data="class:choose-year")])
    if can_subject:
        rows.append([InlineKeyboardButton(text="➕ افزودن درس", callback_data="subject:create")])
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
    rows: list[list[InlineKeyboardButton]] = []
    if can_read:
        rows.append([InlineKeyboardButton(text="👥 کلاس‌های این سال", callback_data="class:list")])
    if can_create_class:
        rows.append([InlineKeyboardButton(text="➕ ایجاد کلاس در این سال", callback_data="class:create-selected")])
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
        body = _FIELD_PROMPTS.get(step_name)
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
) -> None:
    try:
        tenants = await list_tenants(db, actor_id=principal.user.id, limit=50)
    except AccessDenied:
        tenants = await list_accessible_tenants(
            db,
            actor_id=principal.user.id,
            limit=50,
        )

    rows = [
        [InlineKeyboardButton(
            text=f"{tenant.name} · {tenant.slug}",
            callback_data=f"tenant:open:{tenant.id}",
        )]
        for tenant in tenants
    ]
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="structure:root")])
    body = (
        "🏫 مدیریت مدارس و کلاس‌ها\n\nمجموعه را انتخاب کنید:"
        if tenants
        else "هنوز مجموعه‌ای ثبت نشده است."
    )
    await _render_panel(
        message,
        body,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "tenant:directory")
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
        await _show_tenant_directory(
            callback.message,
            principal=principal,
            db=db,
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


@router.callback_query(F.data == "school:list")
async def school_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, _, _ = await _context_ids(state)
        schools = await list_schools(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            limit=50,
        )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(
            text=f"{school.name} · {school.code}",
            callback_data=f"school:open:{school.id}",
        )]
        for school in schools
    ]
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
        f"🏫 مدارس مجموعه\n\nتعداد مدارس: {len(schools)}"
        if schools
        else "هنوز مدرسه‌ای در این مجموعه ثبت نشده است."
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


@router.callback_query(F.data == "year:list")
async def academic_year_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
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

    rows = [
        [InlineKeyboardButton(
            text=f"{year.name} · {year.status}",
            callback_data=f"year:open:{year.id}",
        )]
        for year in years
    ]
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
        f"📅 سال‌های تحصیلی\n\nتعداد: {len(years)}"
        if years
        else "هنوز سال تحصیلی ثبت نشده است. برای ساخت کلاس ابتدا یک سال تحصیلی بسازید."
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


@router.callback_query(F.data == "subject:list")
async def subject_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, _ = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        subjects = await list_subjects(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            limit=100,
        )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(text=f"{subject.name} · {subject.code}", callback_data=f"subject:open:{subject.id}")]
        for subject in subjects
    ]
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
    body = f"📚 درس‌های مدرسه\n\nتعداد: {len(subjects)}" if subjects else "هنوز درسی ثبت نشده است."
    if callback.message is not None:
        await _render_panel(callback.message, body, InlineKeyboardMarkup(inline_keyboard=rows))


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
    await callback.answer()
    if callback.message is not None:
        await _render_panel(
            callback.message,
            f"📚 درس: {subject.name}\nکد سیستمی: {subject.code}\nوضعیت: {subject.status}",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="subject:list")]
            ]),
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


@router.callback_query(F.data == "class:list")
async def class_list_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        tenant_id, school_id, year_id = await _context_ids(state)
        if school_id is None:
            raise ResourceNotFound("ابتدا مدرسه را انتخاب کنید.")
        classes = await list_classes(
            db,
            actor_id=principal.user.id,
            tenant_id=tenant_id,
            school_id=school_id,
            academic_year_id=year_id,
            limit=100,
        )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(
            text=f"{item.name} · پایه {item.grade}",
            callback_data=f"class:open:{item.id}",
        )]
        for item in classes
    ]
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
    body = f"🏫 {heading}\n\nتعداد کلاس‌ها: {len(classes)}" if classes else "در این محدوده کلاسی ثبت نشده است."
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


@router.callback_query()
async def structure_unhandled_callback(callback: CallbackQuery):
    return UNHANDLED
