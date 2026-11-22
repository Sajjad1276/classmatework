from __future__ import annotations

import re
import secrets
from datetime import date
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.event.bases import UNHANDLED
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
    school_code = State()
    school_confirm = State()

    year_name = State()
    year_start = State()
    year_end = State()
    year_confirm = State()

    subject_name = State()
    subject_code = State()
    subject_confirm = State()

    class_name = State()
    class_code = State()
    class_grade = State()
    class_confirm = State()


_CONTEXT_KEYS = {
    "context_tenant_id",
    "context_school_id",
    "context_academic_year_id",
}
_FIELD_PROMPTS = {
    StructureWizard.school_name.state: "نام مدرسه را وارد کنید (۲ تا ۱۸۰ نویسه):",
    StructureWizard.school_code.state: "کد مدرسه را وارد کنید (حروف انگلیسی، عدد، خط تیره یا زیرخط):",
    StructureWizard.year_name.state: "نام سال تحصیلی را وارد کنید؛ نمونه: ۱۴۰۵-۱۴۰۶",
    StructureWizard.year_start.state: "تاریخ شروع را به شکل میلادی YYYY-MM-DD وارد کنید؛ نمونه: 2026-03-21",
    StructureWizard.year_end.state: "تاریخ پایان را به شکل میلادی YYYY-MM-DD وارد کنید؛ نمونه: 2027-03-20",
    StructureWizard.subject_name.state: "نام درس را وارد کنید:",
    StructureWizard.subject_code.state: "کد درس را وارد کنید:",
    StructureWizard.class_name.state: "نام کلاس را وارد کنید:",
    StructureWizard.class_code.state: "کد کلاس را وارد کنید:",
    StructureWizard.class_grade.state: "پایه/مقطع کلاس را وارد کنید؛ نمونه: دهم",
}
_FIELD_KEYS = {
    StructureWizard.school_name.state: "school_name",
    StructureWizard.school_code.state: "school_code",
    StructureWizard.year_name.state: "year_name",
    StructureWizard.year_start.state: "year_start",
    StructureWizard.year_end.state: "year_end",
    StructureWizard.subject_name.state: "subject_name",
    StructureWizard.subject_code.state: "subject_code",
    StructureWizard.class_name.state: "class_name",
    StructureWizard.class_code.state: "class_code",
    StructureWizard.class_grade.state: "class_grade",
}
_PREVIOUS = {
    StructureWizard.school_code.state: StructureWizard.school_name,
    StructureWizard.school_confirm.state: StructureWizard.school_code,
    StructureWizard.year_start.state: StructureWizard.year_name,
    StructureWizard.year_end.state: StructureWizard.year_start,
    StructureWizard.year_confirm.state: StructureWizard.year_end,
    StructureWizard.subject_code.state: StructureWizard.subject_name,
    StructureWizard.subject_confirm.state: StructureWizard.subject_code,
    StructureWizard.class_code.state: StructureWizard.class_name,
    StructureWizard.class_grade.state: StructureWizard.class_code,
    StructureWizard.class_confirm.state: StructureWizard.class_grade,
}
_CONFIRM_STATES = {
    "school": StructureWizard.school_confirm.state,
    "year": StructureWizard.year_confirm.state,
    "subject": StructureWizard.subject_confirm.state,
    "class": StructureWizard.class_confirm.state,
}


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
        rows.append(
            [InlineKeyboardButton(text="فهرست مدارس", callback_data="school:list")]
        )
    if can_create:
        rows.append(
            [InlineKeyboardButton(text="ایجاد مدرسه", callback_data="school:create")]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="انتخاب مجموعه دیگر",
                callback_data="tenant:switch",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="منوی اصلی", callback_data="structure:root")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_tenant_menu(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
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
    await message.answer(
        f"مدیریت مجموعه: {tenant.name}\nشناسه: {tenant.slug}\n\nبخش موردنظر را انتخاب کنید:",
        reply_markup=_tenant_keyboard(
            can_list=can_list,
            can_create=can_create,
        ),
    )


async def _show_school_menu(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
    school_id: UUID,
) -> None:
    school = await db.get(School, school_id)
    if (
        school is None
        or school.tenant_id != tenant_id
        or school.status == "archived"
    ):
        await message.answer("مدرسه در مجموعه انتخاب‌شده پیدا نشد.")
        return

    can_read = await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="school:read",
    )
    can_year = await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="academic_year:create",
    )
    can_class = await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="class:create",
    )
    can_subject = await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="subject:create",
    )

    rows: list[list[InlineKeyboardButton]] = []
    if can_read:
        rows.append(
            [InlineKeyboardButton(text="فهرست سال‌های تحصیلی", callback_data="year:list")]
        )
        rows.append(
            [InlineKeyboardButton(text="فهرست کلاس‌ها", callback_data="class:list")]
        )
        rows.append(
            [InlineKeyboardButton(text="فهرست درس‌ها", callback_data="subject:list")]
        )
    if can_year:
        rows.append(
            [InlineKeyboardButton(text="ایجاد سال تحصیلی", callback_data="year:create")]
        )
    if can_class:
        rows.append(
            [InlineKeyboardButton(text="ایجاد کلاس", callback_data="class:choose-year")]
        )
    if can_subject:
        rows.append(
            [InlineKeyboardButton(text="ایجاد درس", callback_data="subject:create")]
        )
    rows.append(
        [InlineKeyboardButton(text="بازگشت به فهرست مدارس", callback_data="school:list")]
    )
    rows.append(
        [InlineKeyboardButton(text="بازگشت به مجموعه", callback_data="tenant:menu")]
    )
    await message.answer(
        f"مدرسه: {school.name}\nکد: {school.code}\n\nعملیات آموزشی را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _show_year_menu(
    message: Message,
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    tenant_id: UUID,
    school_id: UUID,
    year_id: UUID,
) -> None:
    year = await db.get(AcademicYear, year_id)
    if (
        year is None
        or year.tenant_id != tenant_id
        or year.school_id != school_id
    ):
        await message.answer("سال تحصیلی در مدرسه انتخاب‌شده پیدا نشد.")
        return
    can_read = await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="school:read",
    )
    can_create_class = await _has_school_permission(
        db,
        actor_id=principal.user.id,
        tenant_id=tenant_id,
        school_id=school_id,
        permission="class:create",
    )
    rows: list[list[InlineKeyboardButton]] = []
    if can_read:
        rows.append(
            [
                InlineKeyboardButton(
                    text="فهرست کلاس‌های این سال",
                    callback_data="class:list",
                )
            ]
        )
    if can_create_class:
        rows.append(
            [
                InlineKeyboardButton(
                    text="ایجاد کلاس در این سال",
                    callback_data="class:create-selected",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="بازگشت به مدرسه",
                callback_data="school:menu",
            )
        ]
    )
    await message.answer(
        f"سال تحصیلی: {year.name}\n"
        f"شروع: {year.starts_on.isoformat()}\n"
        f"پایان: {year.ends_on.isoformat()}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


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
        rows.append(
            [
                InlineKeyboardButton(
                    text="تأیید و ثبت",
                    callback_data=f"structure:confirm:{flow_token}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="بازگشت",
                callback_data=f"structure:back:{flow_token}",
            ),
            InlineKeyboardButton(
                text="لغو عملیات",
                callback_data=f"structure:cancel:{flow_token}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _summary(flow_name: str, data: dict) -> str:
    summaries = {
        "school": [
            ("نام مدرسه", "school_name"),
            ("کد مدرسه", "school_code"),
        ],
        "year": [
            ("نام سال تحصیلی", "year_name"),
            ("تاریخ شروع", "year_start"),
            ("تاریخ پایان", "year_end"),
        ],
        "subject": [
            ("نام درس", "subject_name"),
            ("کد درس", "subject_code"),
        ],
        "class": [
            ("نام کلاس", "class_name"),
            ("کد کلاس", "class_code"),
            ("پایه/مقطع", "class_grade"),
            ("شناسه سال تحصیلی", "context_academic_year_id"),
        ],
    }
    lines = ["پیش‌نمایش عملیات\n"]
    for label, key in summaries[flow_name]:
        lines.append(f"{label}: {data.get(key, '-')}")
    lines.append("\nبرای ثبت نهایی تأیید کنید.")
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
        flow_name = data.get("flow_name")
        await message.answer(
            _summary(flow_name, data),
            reply_markup=_flow_keyboard(flow_token, confirm=True),
        )
        return
    prompt = _FIELD_PROMPTS.get(step_name)
    if prompt is None:
        await message.answer("مرحله فرم شناخته نشد؛ عملیات را لغو کنید.")
        return
    await message.answer(prompt, reply_markup=_flow_keyboard(flow_token))


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
    try:
        tenants = await list_tenants(db, actor_id=principal.user.id, limit=50)
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{tenant.name} · {tenant.slug}",
                callback_data=f"tenant:open:{tenant.id}",
            )
        ]
        for tenant in tenants
    ]
    rows.append(
        [InlineKeyboardButton(text="منوی اصلی", callback_data="structure:root")]
    )
    if callback.message is not None:
        await callback.message.answer(
            "مجموعه موردنظر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
    except (AccessDenied, ResourceNotFound) as exc:
        await callback.answer(str(exc) or "دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{school.name} · {school.code}",
                callback_data=f"school:open:{school.id}",
            )
        ]
        for school in schools
    ]
    rows.append(
        [InlineKeyboardButton(text="ایجاد مدرسه", callback_data="school:create")]
    )
    rows.append(
        [InlineKeyboardButton(text="بازگشت به مجموعه", callback_data="tenant:menu")]
    )
    if callback.message is not None:
        await callback.message.answer(
            "فهرست مدارس" if schools else "هنوز مدرسه‌ای ایجاد نشده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
    await callback.answer()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{year.name} · {year.status}",
                callback_data=f"year:open:{year.id}",
            )
        ]
        for year in years
    ]
    rows.append(
        [InlineKeyboardButton(text="ایجاد سال تحصیلی", callback_data="year:create")]
    )
    rows.append(
        [InlineKeyboardButton(text="بازگشت به مدرسه", callback_data="school:menu")]
    )
    if callback.message is not None:
        await callback.message.answer(
            "سال‌های تحصیلی" if years else "هنوز سال تحصیلی ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
    await callback.answer()
    lines = [
        f"{index}. {subject.name} · {subject.code}"
        for index, subject in enumerate(subjects, start=1)
    ]
    body = "\n".join(lines) if lines else "هنوز درسی ثبت نشده است."
    if callback.message is not None:
        await callback.message.answer(
            f"فهرست درس‌ها\n\n{body}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="ایجاد درس", callback_data="subject:create")],
                    [InlineKeyboardButton(text="بازگشت به مدرسه", callback_data="school:menu")],
                ]
            ),
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
    await callback.answer()
    lines = [
        f"{index}. {item.name} · {item.code} · پایه {item.grade}"
        for index, item in enumerate(classes, start=1)
    ]
    body = "\n".join(lines) if lines else "در این محدوده کلاسی ثبت نشده است."
    if callback.message is not None:
        await callback.message.answer(
            f"فهرست کلاس‌ها\n\n{body}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="ایجاد کلاس", callback_data="class:choose-year")],
                    [InlineKeyboardButton(text="بازگشت به مدرسه", callback_data="school:menu")],
                ]
            ),
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
        await callback.answer(str(exc) or "دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    rows = [
        [
            InlineKeyboardButton(
                text=year.name,
                callback_data=f"class:year:{year.id}",
            )
        ]
        for year in years
    ]
    rows.append(
        [InlineKeyboardButton(text="ایجاد سال تحصیلی", callback_data="year:create")]
    )
    rows.append(
        [InlineKeyboardButton(text="بازگشت به مدرسه", callback_data="school:menu")]
    )
    if callback.message is not None:
        await callback.message.answer(
            "سال تحصیلی موردنظر برای کلاس جدید را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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


def _nonempty(value: str, maximum: int) -> str | None:
    return value if 1 <= len(value) <= maximum else None


def _school_code(value: str) -> str | None:
    normalized = value.strip().upper()
    return normalized if re.fullmatch(r"[A-Z0-9_-]{1,48}", normalized) else None


def _iso_date(value: str) -> str | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat()


@router.message(StructureWizard.school_name)
async def school_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="school_name",
        next_state=StructureWizard.school_code,
        validator=lambda value: value if 2 <= len(value) <= 180 else None,
        error_message="نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.",
    )


@router.message(StructureWizard.school_code)
async def school_code_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="school_code",
        next_state=StructureWizard.school_confirm,
        validator=_school_code,
        error_message="کد مدرسه فقط می‌تواند شامل حروف انگلیسی، عدد، خط تیره و زیرخط باشد.",
    )


@router.message(StructureWizard.year_name)
async def year_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="year_name",
        next_state=StructureWizard.year_start,
        validator=lambda value: value if 1 <= len(value) <= 64 else None,
        error_message="نام سال تحصیلی باید بین ۱ تا ۶۴ نویسه باشد.",
    )


@router.message(StructureWizard.year_start)
async def year_start_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="year_start",
        next_state=StructureWizard.year_end,
        validator=_iso_date,
        error_message="تاریخ شروع معتبر نیست. قالب ورودی YYYY-MM-DD است.",
    )


@router.message(StructureWizard.year_end)
async def year_end_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="year_end",
        next_state=StructureWizard.year_confirm,
        validator=_iso_date,
        error_message="تاریخ پایان معتبر نیست. قالب ورودی YYYY-MM-DD است.",
    )


@router.message(StructureWizard.subject_name)
async def subject_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="subject_name",
        next_state=StructureWizard.subject_code,
        validator=lambda value: value if 1 <= len(value) <= 120 else None,
        error_message="نام درس باید بین ۱ تا ۱۲۰ نویسه باشد.",
    )


@router.message(StructureWizard.subject_code)
async def subject_code_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="subject_code",
        next_state=StructureWizard.subject_confirm,
        validator=_school_code,
        error_message="کد درس فقط می‌تواند شامل حروف انگلیسی، عدد، خط تیره و زیرخط باشد.",
    )


@router.message(StructureWizard.class_name)
async def class_name_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="class_name",
        next_state=StructureWizard.class_code,
        validator=lambda value: value if 1 <= len(value) <= 120 else None,
        error_message="نام کلاس باید بین ۱ تا ۱۲۰ نویسه باشد.",
    )


@router.message(StructureWizard.class_code)
async def class_code_step(message: Message, state: FSMContext) -> None:
    await _accept_field(
        message,
        state,
        key="class_code",
        next_state=StructureWizard.class_grade,
        validator=_school_code,
        error_message="کد کلاس فقط می‌تواند شامل حروف انگلیسی، عدد، خط تیره و زیرخط باشد.",
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
                code=data.get("school_code", ""),
            )
            result_text = f"مدرسه ایجاد شد: {entity.name} · {entity.code}"
            success_school_id = entity.id
            success_year_id = None
        elif flow_name == "year":
            entity = await create_academic_year(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                name=data.get("year_name", ""),
                starts_on=date.fromisoformat(data.get("year_start", "")),
                ends_on=date.fromisoformat(data.get("year_end", "")),
            )
            result_text = f"سال تحصیلی ایجاد شد: {entity.name}"
            success_school_id = school_id
            success_year_id = entity.id
        elif flow_name == "subject":
            entity = await create_subject(
                db,
                actor_id=principal.user.id,
                tenant_id=tenant_id,
                school_id=school_id,
                name=data.get("subject_name", ""),
                code=data.get("subject_code", ""),
            )
            result_text = f"درس ایجاد شد: {entity.name} · {entity.code}"
            success_school_id = school_id
            success_year_id = year_id
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
                code=data.get("class_code", ""),
                grade=data.get("class_grade", ""),
            )
            result_text = f"کلاس ایجاد شد: {entity.name} · {entity.code}"
            success_school_id = school_id
            success_year_id = year_id
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except ValueError:
        await callback.answer(
            "تاریخ یا مقدار ورودی معتبر نیست. فرم را بررسی و دوباره تلاش کنید.",
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
        await callback.message.answer(result_text)
        if success_school_id is not None:
            await _show_school_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
                school_id=success_school_id,
            )
        else:
            await _show_tenant_menu(
                callback.message,
                db=db,
                principal=principal,
                tenant_id=tenant_id,
            )


async def _return_to_context_menu(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    principal: TelegramPrincipal,
) -> None:
    await _clear_flow(state)
    try:
        tenant_id, school_id, _ = await _context_ids(state)
    except ResourceNotFound:
        return
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
        await _return_to_context_menu(
            callback.message,
            state,
            db,
            principal,
        )


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
        await callback.answer("این دکمه متعلق به فرم قبلی است یا منقضی شده است.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("عملیات لغو شد؛ هیچ تغییری ثبت نشد.")
        await _return_to_context_menu(
            callback.message,
            state,
            db,
            principal,
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
