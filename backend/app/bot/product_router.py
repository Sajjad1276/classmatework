from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
import unicodedata

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.types import (
    InlineKeyboardMarkup as TelegramInlineKeyboardMarkup,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.class_join_codes import (
    get_class_join_code_secret,
    store_class_join_code_secret,
)
from app.application.errors import ApplicationError, ResourceConflict
from app.application.product_management import (
    add_product_class_member,
    count_missing_product_class_join_codes,
    create_and_assign_product_subject,
    create_product_announcement,
    create_product_class,
    create_product_school,
    create_product_timetable_entry,
    get_product_class,
    get_product_class_record,
    get_product_school,
    issue_missing_product_class_join_codes,
    issue_product_class_join_code,
    sync_product_class_join_code_token,
    list_class_subjects,
    list_class_timetable,
    list_product_class_announcements,
    list_product_class_members,
    list_product_started_users,
    list_product_class_records,
    list_product_class_shortcut_candidates,
    list_product_classes,
    list_product_schools,
    review_product_class_record,
    set_product_announcement_active,
    set_product_class_active,
    set_product_class_member_active,
    set_product_class_member_role,
    set_product_school_active,
    update_product_class,
    update_product_school,
)
from app.bot.ux import add_wizard_cancel, ensure_back_button
from app.bot.ux import wizard_controls as shared_wizard_controls
from app.core.config import get_settings
from app.modules.access.policy import has_permission
from app.modules.audit.service import record_event
from app.modules.identity.principal import TelegramPrincipal

logger = logging.getLogger(__name__)
router = Router(name="product-management")

def InlineKeyboardMarkup(*args, **kwargs) -> TelegramInlineKeyboardMarkup:
    """Create an inline keyboard with a safe back path by default."""
    return ensure_back_button(TelegramInlineKeyboardMarkup(*args, **kwargs))


SCHOOLS_BUTTON = "🏫 مدیریت مدارس و کلاس‌ها"
OWNER_CLASS_BUTTON = "🎓 کلاس من · دهم ۱۰۴"
CREATE_SCHOOL_BUTTON = "➕ ایجاد مدرسه"


def _normalize_class_shortcut_label(value: object) -> str:
    digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    normalized = unicodedata.normalize("NFKC", str(value or "")).translate(digits)
    normalized = normalized.replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", "", normalized).casefold()


def _is_owner_class_shortcut_candidate(item: dict) -> bool:
    grade = _normalize_class_shortcut_label(item.get("grade"))
    if grade not in {"دهم", "10", "پایهدهم", "پایه10"}:
        return False

    name = _normalize_class_shortcut_label(item.get("name"))
    section = _normalize_class_shortcut_label(item.get("section"))
    return name.endswith("104") or section == "104"


def _class_join_start_url(code: str) -> str | None:
    username = (get_settings().classmate_bot_username or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        return None
    return f"https://t.me/{username}?start=join_{code}"


def _current_product_join_code(class_info: dict) -> str | None:
    """Trust the product DB token only when it still matches the active hash."""
    code = str(class_info.get("member_join_code_token") or "").strip().upper()
    stored_hash = str(class_info.get("member_join_code_hash") or "")
    if not code or not stored_hash:
        return None
    actual_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return code if secrets.compare_digest(actual_hash, stored_hash) else None


def _class_join_code_message(
    class_name: str,
    code: str,
    *,
    replaced_legacy_code: bool = False,
) -> str:
    start_url = _class_join_start_url(code)
    link_line = (
        f'🔗 <a href="{html.escape(start_url, quote=True)}">باز کردن لینک استارت عضویت در کلاس</a>'
        if start_url
        else (
            "🔗 لینک استارت پس از تنظیم نام کاربری ربات اصلی ClassMate در "
            "<code>CLASSMATE_BOT_USERNAME</code> فعال می‌شود."
        )
    )
    legacy_notice = (
        "⚠️ کد قبلی فقط به‌صورت هش ذخیره شده بود و قابل بازیابی نبود؛ "
        "این کد جدید جایگزین آن شده است.\n\n"
        if replaced_legacy_code
        else ""
    )
    return (
        "🔑 <b>کد ورود به کلاس</b>\n\n"
        f"کلاس: {html.escape(str(class_name))}\n\n"
        f"{legacy_notice}"
        f"{link_line}\n\n"
        "برای عضویت دستی، در ClassMate وارد بخش «عضویت با کد» شوید و کد زیر را ثبت کنید:\n"
        f"<code>{html.escape(str(code))}</code>\n\n"
        "این کد اختصاصی همین کلاس است و تا زمانی که دکمهٔ «تغییر کد ورود» را نزنید، "
        "ثابت می‌ماند."
    )


def _class_join_code_summary(class_name: str, code: str) -> str:
    start_url = _class_join_start_url(code)
    link_line = (
        f'🔗 <a href="{html.escape(start_url, quote=True)}">لینک استارت عضویت</a>'
        if start_url
        else "لینک استارت: با تنظیم CLASSMATE_BOT_USERNAME فعال می‌شود."
    )
    return (
        f"🏫 <b>{html.escape(str(class_name))}</b>\n"
        f"{link_line}\n"
        f"کد عضویت دستی: <code>{html.escape(str(code))}</code>"
    )


class SchoolCreation(StatesGroup):
    name = State()
    confirm = State()


class SchoolEditing(StatesGroup):
    name = State()
    confirm = State()


class ClassCreation(StatesGroup):
    name = State()
    grade = State()
    section = State()
    confirm = State()


class ClassEditing(StatesGroup):
    name = State()
    grade = State()
    section = State()
    confirm = State()


class SubjectCreation(StatesGroup):
    name = State()
    confirm = State()


class JoinCodeIssue(StatesGroup):
    confirm = State()


class JoinCodeRotation(StatesGroup):
    confirm = State()


class BulkJoinCodeWizard(StatesGroup):
    confirm = State()


class MemberRoleChangeWizard(StatesGroup):
    confirm = State()


class TimetableCreation(StatesGroup):
    weekday = State()
    subject = State()
    period = State()
    time_range = State()
    room = State()
    confirm = State()


class MemberCreation(StatesGroup):
    telegram_id = State()
    full_name = State()
    confirm = State()


class AnnouncementCreation(StatesGroup):
    title = State()
    body = State()
    confirm = State()




async def _hide_reply_keyboard(message: Message) -> None:
    """Remove the persistent root keyboard before showing any inline panel."""
    removal = await message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
    try:
        await removal.delete()
    except Exception:
        logger.debug("Could not delete reply-keyboard cleanup message.", exc_info=True)


async def _close_inline_panel(message: Message | None) -> None:
    """Remove inline controls while a text-input wizard is active."""
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("Could not close inline panel before input.", exc_info=True)

async def _render_product_panel(
    message: Message,
    body: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    """Edit an existing bot panel when possible; otherwise send a new one."""
    if message.from_user is not None and message.from_user.is_bot:
        try:
            await message.edit_text(body, reply_markup=keyboard)
            return
        except Exception:
            logger.debug("Could not edit active product panel.", exc_info=True)
    await message.answer(body, reply_markup=keyboard)





async def _require_private_callback(
    callback: CallbackQuery,
    operation: str,
) -> bool:
    """Keep personal invitation codes and membership controls out of group chats."""
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(
            f"{operation} فقط در گفت‌وگوی خصوصی ربات مجاز است.",
            show_alert=True,
        )
        return False
    return True

async def _has_permission(
    db: AsyncSession,
    principal: TelegramPrincipal,
    permission_key: str,
) -> bool:
    return await has_permission(
        db,
        user_id=principal.user.id,
        permission_key=permission_key,
        tenant_id=None,
        scope_type="platform",
        scope_id=None,
    )


def _school_directory_keyboard(
    schools: list[dict],
    *,
    can_create: bool,
    can_manage_classes: bool = False,
    page: int = 1,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if school['is_active'] else '⚪'} {school['name'][:35]} · {school['class_count']} کلاس",
                callback_data=f"product:school:{school['id']}",
            )
        ]
        for school in schools[:8]
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"product:schools:page:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text="صفحه بعد ➡️",
                callback_data=f"product:schools:page:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    if can_create:
        rows.append(
            [InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="product:school:create")]
        )
    if can_manage_classes:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔑 صدور کد برای کلاس‌های بدون کد",
                    callback_data="product:classes:join-codes:missing",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_school_directory(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None,
    page: int = 1,
) -> None:
    if page < 1:
        await message.answer("شماره صفحه معتبر نیست.")
        return
    if not await _has_permission(db, principal, "platform:schools:read"):
        await message.answer("مجوز مشاهدهٔ مدارس را ندارید.")
        return
    if product_session_factory is None:
        await message.answer(
            "اتصال دیتابیس اصلی ClassMate فعال نیست؛ فهرست مدارس در دسترس نیست."
        )
        return

    can_create = await _has_permission(db, principal, "platform:schools:create")
    can_manage_classes = await _has_permission(db, principal, "platform:classes:manage")
    try:
        async with product_session_factory() as product_db:
            fetched = await list_product_schools(
                product_db,
                limit=9,
                offset=(page - 1) * 8,
            )
    except Exception:
        logger.exception("Could not load ClassMate product schools.")
        await message.answer("دریافت فهرست مدارس از دیتابیس ClassMate ناموفق بود.")
        return

    has_next = len(fetched) > 8
    schools = fetched[:8]
    body = (
        f"🏫 مدیریت مدارس ClassMate\n\n"
        f"صفحه: {page}\n"
        "مدرسه را برای مشاهدهٔ کلاس‌ها انتخاب کنید."
        if schools
        else (
            "هنوز مدرسه‌ای در ClassMate ثبت نشده است."
            if page == 1
            else "در این صفحه مدرسه‌ای وجود ندارد؛ به صفحه قبل برگردید."
        )
    )
    await _render_product_panel(
        message,
        body,
        _school_directory_keyboard(
            schools,
            can_create=can_create,
            can_manage_classes=can_manage_classes,
            page=page,
            has_next=has_next,
        ),
    )


@router.message(F.text == SCHOOLS_BUTTON)
async def schools_button_handler(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await _hide_reply_keyboard(message)
    await _show_school_directory(
        message,
        principal=principal,
        db=db,
        product_session_factory=product_session_factory,
    )


@router.callback_query(F.data.regexp(r"^product:schools(?::page:[1-9][0-9]*)?$"))
async def schools_refresh_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await callback.answer()
    if callback.message is not None:
        raw_page = (callback.data or "").rsplit(":", maxsplit=1)[-1]
        page = int(raw_page) if raw_page.isdigit() else 1
        await _show_school_directory(
            callback.message,
            principal=principal,
            db=db,
            product_session_factory=product_session_factory,
            page=page,
        )


async def _begin_school_creation(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:create"):
        await message.answer("مجوز ایجاد مدرسه را ندارید.")
        return
    await state.clear()
    token = secrets.token_urlsafe(8)
    await state.update_data(product_school_token=token)
    await state.set_state(SchoolCreation.name)
    await message.answer(
        "نام مدرسه را وارد کنید (۲ تا ۱۸۰ نویسه):",
        reply_markup=shared_wizard_controls(),
    )


@router.message(F.text == CREATE_SCHOOL_BUTTON)
async def school_create_button_handler(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await _hide_reply_keyboard(message)
    await _begin_school_creation(
        message,
        principal=principal,
        db=db,
        state=state,
    )


@router.callback_query(F.data == "product:school:create")
async def school_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await _close_inline_panel(callback.message)
        await _begin_school_creation(
            callback.message,
            principal=principal,
            db=db,
            state=state,
        )


@router.message(SchoolCreation.name, F.text)
async def school_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 180:
        await message.answer(
            "خطا: نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.\n\nنام مدرسه را دوباره وارد کنید:",
            reply_markup=shared_wizard_controls(),
        )
        return

    data = await state.get_data()
    token = data.get("product_school_token")
    if not token:
        await state.clear()
        await message.answer("فرم ایجاد مدرسه منقضی شده است؛ دوباره شروع کنید.")
        return

    await state.update_data(product_school_name=name)
    await state.set_state(SchoolCreation.confirm)
    await message.answer(
        f"پیش‌نمایش ایجاد مدرسه\n\nنام: {name}\nمنطقهٔ زمانی: Asia/Tehran\n\n"
        "ثبت نهایی شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ایجاد مدرسه",
                        callback_data=f"product:school:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:school:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:school:confirm:"))
async def school_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolCreation.confirm.state
        or not token
        or token != data.get("product_school_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:schools:create"):
        await state.clear()
        await callback.answer("مجوز ایجاد مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school = await create_product_school(
                    product_db,
                    name=data.get("product_school_name", ""),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create ClassMate product school.")
        await state.clear()
        await callback.answer("ایجاد مدرسه در دیتابیس ClassMate ناموفق بود.", show_alert=True)
        return

    audit_warning = False
    try:
        await record_event(
            db,
            event_type="product.school.created",
            actor_id=principal.user.id,
            aggregate_type="school",
            event_data={"school_id": school["id"], "name": school["name"]},
        )
        await db.commit()
    except Exception:
        audit_warning = True
        await db.rollback()
        logger.exception("School %s was created but Work audit recording failed.", school["id"])

    await state.clear()
    await callback.answer("مدرسه ایجاد شد.")
    if callback.message is not None:
        notice = (
            "⚠️ مدرسه ثبت شد، اما ثبت رویداد ممیزی نیازمند بررسی است. "
            if audit_warning
            else ""
        )
        await callback.message.edit_text(
            f"✅ {notice}مدرسه ایجاد شد.\n\nشناسه: {school['id']}\nنام: {school['name']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به فهرست مدارس",
                            callback_data="product:schools",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:school:cancel:"))
async def school_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolCreation.confirm.state
        or not token
        or token != data.get("product_school_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ایجاد مدرسه لغو شد؛ تغییری ثبت نشد.")





async def _render_bulk_join_code_result(
    callback: CallbackQuery,
    issued: list[dict],
) -> None:
    if callback.message is None:
        return
    if not issued:
        await callback.message.edit_text(
            "✅ در زمان تأیید، کدِ فاقد کد دیگری باقی نمانده بود؛ هیچ تغییری اعمال نشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ بازگشت به مدارس", callback_data="product:schools")]
                ]
            ),
        )
        return

    chunks = [issued[index : index + 20] for index in range(0, len(issued), 20)]
    first_lines = [
        "🔑 <b>کدهای ورود کلاس‌های فاقد کد صادر شدند</b>",
        f"تعداد کدهای جدید: {len(issued)}",
        "هر کد فقط برای همان کلاس است و تا زمان تغییر کد معتبر می‌ماند.",
        "کدهای قبلی دست‌نخورده باقی ماندند.",
        "",
    ]
    first_lines.extend(
        f"🏫 {html.escape(str(item['school_name']))}\n"
        f"{_class_join_code_summary(item['class_name'], item['join_code'])}"
        for item in chunks[0]
    )
    await callback.message.edit_text(
        "\n\n".join(first_lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ بازگشت به مدارس", callback_data="product:schools")]
            ]
        ),
    )
    for chunk in chunks[1:]:
        body = "\n\n".join(
            f"🏫 {html.escape(str(item['school_name']))}\n"
            f"{_class_join_code_summary(item['class_name'], item['join_code'])}"
            for item in chunk
        )
        await callback.message.answer(body, parse_mode="HTML")


@router.callback_query(F.data == "product:classes:join-codes:missing")
async def product_missing_class_join_codes_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "صدور کد ورود کلاس‌ها"):
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز صدور کد ورود کلاس‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    try:
        async with product_session_factory() as product_db:
            missing_count = await count_missing_product_class_join_codes(product_db)
    except Exception:
        logger.exception("Could not count missing ClassMate class join codes.")
        await callback.answer("بررسی تعداد کدهای فاقد مقدار ناموفق بود.", show_alert=True)
        return

    if missing_count == 0:
        await callback.answer("همهٔ کلاس‌های فعال کد ورود دارند.")
        if callback.message is not None:
            await callback.message.edit_text(
                "✅ همهٔ کلاس‌های فعال از قبل کد ورود دارند؛ کدی تغییر نکرد.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ بازگشت به مدارس", callback_data="product:schools")]
                    ]
                ),
            )
        return

    token = secrets.token_urlsafe(8)
    await state.clear()
    await state.update_data(
        bulk_join_code_token=token,
        bulk_join_code_preview_count=missing_count,
    )
    await state.set_state(BulkJoinCodeWizard.confirm)
    await callback.answer()
    if callback.message is not None:
        capped_count = min(missing_count, 2000)
        cap_note = (
            "\nبه‌دلیل سقف هر عملیات، فقط ۲۰۰۰ کلاس اول بررسی و پردازش می‌شوند."
            if missing_count > 2000
            else ""
        )
        await callback.message.edit_text(
            "پیش‌نمایش صدور گروهی کد ورود\n\n"
            f"کلاس‌های فعال فاقد کد: {missing_count}\n"
            f"حداکثر صدور در این عملیات: {capped_count}\n\n"
            "کدهای موجود تغییر نمی‌کنند. برای هر کلاس فاقد کد یک کد مستقل ساخته می‌شود."
            f"{cap_note}\n\n"
            "با تأیید صریح، صدور گروهی آغاز می‌شود. ادامه می‌دهید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ تأیید صدور گروهی",
                            callback_data=f"product:classes:join-codes:confirm:{token}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو عملیات",
                            callback_data=f"product:classes:join-codes:cancel:{token}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:classes:join-codes:confirm:"))
async def product_missing_class_join_codes_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != BulkJoinCodeWizard.confirm.state
        or not token
        or token != data.get("bulk_join_code_token")
    ):
        await callback.answer("این پیش‌نمایش منقضی شده است.", show_alert=True)
        return
    if not await _require_private_callback(callback, "صدور کد ورود کلاس‌ها"):
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await state.clear()
        await callback.answer("مجوز صدور کد ورود کلاس‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                issued = await issue_missing_product_class_join_codes(
                    product_db,
                    limit=2000,
                )
        if issued:
            for item in issued:
                await store_class_join_code_secret(
                    db,
                    class_id=item["class_id"],
                    code=item["join_code"],
                )
            await record_event(
                db,
                event_type="product.class.member_join_codes_backfilled",
                actor_id=principal.user.id,
                aggregate_type="platform",
                event_data={
                    "issued_count": len(issued),
                    "class_ids": [item["class_id"] for item in issued],
                    "previewed_count": data.get("bulk_join_code_preview_count"),
                },
            )
            await db.commit()
    except ApplicationError as exc:
        await db.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        await db.rollback()
        logger.exception("Could not issue missing ClassMate class join codes.")
        await callback.answer("صدور گروهی کدهای ورود ناموفق بود؛ فرم برای تلاش دوباره حفظ شد.", show_alert=True)
        return

    await state.clear()
    await callback.answer(f"برای {len(issued)} کلاس کد صادر شد." if issued else "کدی کم نبود.")
    await _render_bulk_join_code_result(callback, issued)


@router.callback_query(F.data.startswith("product:classes:join-codes:cancel:"))
async def product_missing_class_join_codes_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != BulkJoinCodeWizard.confirm.state
        or not token
        or token != data.get("bulk_join_code_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("صدور گروهی لغو شد؛ هیچ کدی صادر نشد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "صدور گروهی کدها لغو شد؛ هیچ تغییری ثبت نشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ بازگشت به مدارس", callback_data="product:schools")]
                ]
            ),
        )




@router.callback_query(F.data.regexp(r"^product:school:[0-9]+(?::page:[1-9][0-9]*)?$"))
async def product_school_detail_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:read"):
        await callback.answer("مجوز مشاهدهٔ مدارس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    try:
        parts = (callback.data or "").split(":")
        school_id = int(parts[2])
        page = int(parts[4]) if len(parts) == 5 and parts[3] == "page" else 1
        if page < 1:
            raise ValueError
        async with product_session_factory() as product_db:
            school = await get_product_school(product_db, school_id=school_id)
            if school is None:
                await callback.answer("مدرسه پیدا نشد.", show_alert=True)
                return
            fetched_classes = await list_product_classes(
                product_db,
                school_id=school_id,
                limit=9,
                offset=(page - 1) * 8,
            )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شناسه یا صفحه معتبر نیست.", show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate school %s.", callback.data)
        await callback.answer("دریافت جزئیات مدرسه ناموفق بود.", show_alert=True)
        return

    has_next = len(fetched_classes) > 8
    classes = fetched_classes[:8]
    status = "فعال" if school["is_active"] else "غیرفعال"
    lines = [
        f"🏫 مدرسه: {school['name']}",
        f"شناسه: {school['id']}",
        f"وضعیت: {status}",
        f"منطقه زمانی: {school['timezone']}",
        f"تعداد کلاس‌ها: {school['class_count']}",
        f"صفحهٔ کلاس‌ها: {page}",
        "",
        "کلاس‌های این صفحه:",
    ]
    can_read_classes = await _has_permission(db, principal, "platform:classes:read")
    if classes and can_read_classes:
        lines.append("برای ورود به مدیریت هر کلاس، دکمهٔ آن را انتخاب کنید.")
    elif classes:
        lines.append("کلاس ثبت شده است؛ برای مشاهدهٔ جزئیات کلاس مجوز لازم را ندارید.")
    else:
        lines.append(
            "هنوز کلاسی برای این مدرسه ثبت نشده است."
            if page == 1
            else "کلاسی در این صفحه نیست؛ به صفحه قبل برگردید."
        )

    rows = (
        [
            [
                InlineKeyboardButton(
                    text=(
                        f"👥 {item['name'][:21]} · {item['grade'][:10]} "
                        f"{'🔑' if item['has_member_join_code'] else '⚠️'}"
                    ),
                    callback_data=f"product:class:{item['id']}",
                )
            ]
            for item in classes
        ]
        if can_read_classes
        else []
    )
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ کلاس‌های قبلی",
                    callback_data=f"product:school:{school_id}:page:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="کلاس‌های بعدی ➡️",
                    callback_data=f"product:school:{school_id}:page:{page + 1}",
                )
            )
        rows.append(navigation)

    can_create_class = (
        school["is_active"]
        and await _has_permission(db, principal, "platform:classes:create")
    )
    if can_create_class:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ افزودن کلاس",
                    callback_data=f"product:class:create:{school_id}",
                )
            ]
        )
    if await _has_permission(db, principal, "platform:schools:manage"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ ویرایش نام مدرسه",
                    callback_data=f"product:school:edit:{school_id}",
                ),
                InlineKeyboardButton(
                    text=("⛔ غیرفعال‌سازی مدرسه" if school["is_active"] else "♻️ فعال‌سازی مدرسه"),
                    callback_data=f"product:school:status:{school_id}:{0 if school['is_active'] else 1}",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ بازگشت به مدارس",
                callback_data="product:schools",
            )
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


async def _begin_class_creation(
    message: Message,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    school_id: int,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:create"):
        await message.answer("مجوز ایجاد کلاس را ندارید.")
        return
    await state.clear()
    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_class_token=token,
        product_class_school_id=school_id,
    )
    await state.set_state(ClassCreation.name)
    await message.answer(
        "نام کلاس را وارد کنید (۲ تا ۸۰ نویسه):",
        reply_markup=shared_wizard_controls(),
    )


@router.callback_query(F.data.regexp(r"^product:class:create:[0-9]+$"))
async def product_class_create_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            school = await get_product_school(product_db, school_id=school_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not validate school before class creation.")
        await callback.answer("بررسی مدرسه ناموفق بود.", show_alert=True)
        return
    if school is None or not school["is_active"]:
        await callback.answer("مدرسه فعال پیدا نشد.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _close_inline_panel(callback.message)
        await _begin_class_creation(
            callback.message,
            principal=principal,
            db=db,
            state=state,
            school_id=school_id,
        )


@router.message(ClassCreation.name, F.text)
async def class_name_input(message: Message, state: FSMContext) -> None:
    value = " ".join((message.text or "").split())
    if not 2 <= len(value) <= 80:
        await message.answer("خطا: نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.\n\nنام کلاس را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_class_name=value)
    await state.set_state(ClassCreation.grade)
    await message.answer("پایه یا مقطع کلاس را وارد کنید (مثلاً دهم):", reply_markup=shared_wizard_controls())


@router.message(ClassCreation.grade, F.text)
async def class_grade_input(message: Message, state: FSMContext) -> None:
    value = " ".join((message.text or "").split())
    if not 1 <= len(value) <= 32:
        await message.answer("خطا: پایه باید بین ۱ تا ۳۲ نویسه باشد.\n\nپایه یا مقطع را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_class_grade=value)
    await state.set_state(ClassCreation.section)
    await message.answer("نام بخش/شعبه را وارد کنید؛ اگر ندارد، فقط «-» بفرستید:", reply_markup=shared_wizard_controls())


@router.message(ClassCreation.section, F.text)
async def class_section_input(message: Message, state: FSMContext) -> None:
    value = " ".join((message.text or "").split())
    section = None if value == "-" else value
    if section is not None and len(section) > 32:
        await message.answer("خطا: عنوان بخش کلاس حداکثر ۳۲ نویسه است.\n\nبخش/شعبه را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    data = await state.get_data()
    token = data.get("product_class_token")
    if not token:
        await state.clear()
        await message.answer("فرم ایجاد کلاس منقضی شده است؛ دوباره شروع کنید.")
        return
    await state.update_data(product_class_section=section)
    await state.set_state(ClassCreation.confirm)
    await message.answer(
        "پیش‌نمایش ایجاد کلاس\n\n"
        f"نام: {data.get('product_class_name')}\n"
        f"پایه: {data.get('product_class_grade')}\n"
        f"بخش: {section or 'بدون بخش'}\n\n"
        "ثبت نهایی شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ایجاد کلاس",
                        callback_data=f"product:class:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:class:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:class:confirm:"))
async def product_class_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassCreation.confirm.state
        or not token
        or token != data.get("product_class_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:classes:create"):
        await state.clear()
        await callback.answer("مجوز ایجاد کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    school_id = int(data.get("product_class_school_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school_class = await create_product_class(
                    product_db,
                    school_id=school_id,
                    name=data.get("product_class_name", ""),
                    grade=data.get("product_class_grade", ""),
                    section=data.get("product_class_section"),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create ClassMate product class.")
        await state.clear()
        await callback.answer("ایجاد کلاس در دیتابیس ClassMate ناموفق بود.", show_alert=True)
        return

    audit_warning = False
    try:
        await store_class_join_code_secret(
            db,
            class_id=school_class["id"],
            code=school_class["member_join_code"],
        )
        await record_event(
            db,
            event_type="product.class.created",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": school_class["id"],
                "school_id": school_id,
                "name": school_class["name"],
                "grade": school_class["grade"],
            },
        )
        await db.commit()
    except Exception:
        audit_warning = True
        await db.rollback()
        logger.exception(
            "Class %s was created but Work audit recording failed.",
            school_class["id"],
        )

    await state.clear()
    await callback.answer("کلاس ایجاد شد.")
    if callback.message is not None:
        notice = (
            "⚠️ کلاس ثبت شد، اما ثبت رویداد ممیزی نیازمند بررسی است. "
            if audit_warning
            else ""
        )
        await callback.message.edit_text(
            f"✅ {notice}کلاس ایجاد شد.\n\n"
            f"شناسه: {school_class['id']}\n"
            f"نام: {html.escape(str(school_class['name']))}\n"
            f"پایه: {html.escape(str(school_class['grade']))}\n\n"
            f"{_class_join_code_message(school_class['name'], school_class['member_join_code'])}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="♻️ تغییر کد ورود",
                            callback_data=f"product:class:join-code:rotate:{school_class['id']}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👥 مدیریت کلاس",
                            callback_data=f"product:class:{school_class['id']}",
                        ),
                        InlineKeyboardButton(
                            text="🏫 بازگشت به مدرسه",
                            callback_data=f"product:school:{school_id}",
                        ),
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:cancel:"))
async def product_class_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassCreation.confirm.state
        or not token
        or token != data.get("product_class_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ایجاد کلاس لغو شد؛ تغییری ثبت نشد.")




@router.callback_query(F.data.regexp(r"^product:school:edit:[0-9]+$"))
async def product_school_edit_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await callback.answer("مجوز ویرایش مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    async with product_session_factory() as product_db:
        school = await get_product_school(product_db, school_id=school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    await _close_inline_panel(callback.message)
    await state.clear()
    await state.update_data(product_school_edit_id=school_id)
    await state.set_state(SchoolEditing.name)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"نام فعلی مدرسه: {school['name']}\nنام جدید را وارد کنید:",
            reply_markup=shared_wizard_controls(),
        )


@router.message(SchoolEditing.name, F.text)
async def product_school_edit_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 180:
        await message.answer("خطا: نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.\n\nنام جدید را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    data = await state.get_data()
    school_id = int(data.get("product_school_edit_id") or 0)
    if school_id <= 0:
        await state.clear()
        await message.answer("فرم ویرایش مدرسه منقضی شده است؛ دوباره شروع کنید.")
        return
    token = secrets.token_urlsafe(8)
    await state.update_data(product_school_edit_name=name, product_school_edit_token=token)
    await state.set_state(SchoolEditing.confirm)
    await message.answer(
        f"پیش‌نمایش ویرایش مدرسه\n\nشناسه: {school_id}\nنام جدید: {name}\n\nثبت شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ویرایش",
                        callback_data=f"product:school:edit:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:school:edit:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:school:edit:confirm:"))
async def product_school_edit_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolEditing.confirm.state
        or not token
        or token != data.get("product_school_edit_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await state.clear()
        await callback.answer("مجوز ویرایش مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    school_id = int(data.get("product_school_edit_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school = await update_product_school(
                    product_db,
                    school_id=school_id,
                    name=data.get("product_school_edit_name", ""),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate school %s.", school_id)
        await state.clear()
        await callback.answer("ویرایش مدرسه ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.school.updated",
            actor_id=principal.user.id,
            aggregate_type="school",
            event_data={"school_id": school_id, "name": school["name"]},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("School update audit recording failed for %s.", school_id)
    await state.clear()
    await callback.answer("مدرسه ویرایش شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ نام مدرسه به «{school['name']}» تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به مدرسه",
                            callback_data=f"product:school:{school_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:school:edit:cancel:"))
async def product_school_edit_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SchoolEditing.confirm.state
        or not token
        or token != data.get("product_school_edit_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ویرایش مدرسه لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.regexp(r"^product:school:status:[0-9]+:[01]$"))
async def product_school_status_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await callback.answer("مجوز تغییر وضعیت مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    school_id, desired = int(parts[3]), parts[4] == "1"
    async with product_session_factory() as product_db:
        school = await get_product_school(product_db, school_id=school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    action = "فعال‌سازی" if desired else "غیرفعال‌سازی"
    warning = (
        "\n\nبا غیرفعال‌سازی مدرسه، دسترسی آموزشی کلاس‌های آن نیز متوقف می‌شود؛ "
        "هیچ داده‌ای حذف نخواهد شد."
        if not desired
        else ""
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید مدرسهٔ «{school['name']}» را {action} کنید؟{warning}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=f"product:school:status:confirm:{school_id}:{1 if desired else 0}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:school:{school_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:school:status:confirm:[0-9]+:[01]$"))
async def product_school_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:schools:manage"):
        await callback.answer("مجوز تغییر وضعیت مدرسه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    school_id, desired = int(parts[4]), parts[5] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                school = await set_product_school_active(
                    product_db,
                    school_id=school_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not change ClassMate school status.")
        await callback.answer("تغییر وضعیت مدرسه ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.school.status_changed",
            actor_id=principal.user.id,
            aggregate_type="school",
            event_data={"school_id": school_id, "is_active": desired},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("School status audit recording failed for %s.", school_id)
    await callback.answer("وضعیت مدرسه تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"وضعیت مدرسه «{school['name']}» به {'فعال' if desired else 'غیرفعال'} تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏫 بازگشت به مدرسه",
                            callback_data=f"product:school:{school_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:class:edit:[0-9]+$"))
async def product_class_edit_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز ویرایش کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    async with product_session_factory() as product_db:
        class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    await _close_inline_panel(callback.message)
    await state.clear()
    await state.update_data(
        product_class_edit_id=class_id,
        product_class_edit_name=class_info["name"],
        product_class_edit_grade=class_info["grade"],
        product_class_edit_section=class_info["section"],
    )
    await state.set_state(ClassEditing.name)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"نام فعلی کلاس: {class_info['name']}\nنام جدید را وارد کنید:",
            reply_markup=shared_wizard_controls(),
        )


@router.message(ClassEditing.name, F.text)
async def product_class_edit_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 80:
        await message.answer("خطا: نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.\n\nنام کلاس را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_class_edit_name=name)
    await state.set_state(ClassEditing.grade)
    await message.answer("پایهٔ جدید کلاس را وارد کنید:", reply_markup=shared_wizard_controls())


@router.message(ClassEditing.grade, F.text)
async def product_class_edit_grade_input(message: Message, state: FSMContext) -> None:
    grade = " ".join((message.text or "").split())
    if not 1 <= len(grade) <= 32:
        await message.answer("خطا: پایه باید بین ۱ تا ۳۲ نویسه باشد.\n\nپایه یا مقطع را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_class_edit_grade=grade)
    await state.set_state(ClassEditing.section)
    await message.answer("بخش جدید را وارد کنید؛ برای پاک‌کردن بخش فعلی «-» بفرستید:", reply_markup=shared_wizard_controls())


@router.message(ClassEditing.section, F.text)
async def product_class_edit_section_input(message: Message, state: FSMContext) -> None:
    section = " ".join((message.text or "").split())
    if section == "-":
        section = None
    if section is not None and len(section) > 32:
        await message.answer("خطا: عنوان بخش کلاس حداکثر ۳۲ نویسه است.\n\nبخش/شعبه را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    data = await state.get_data()
    class_id = int(data.get("product_class_edit_id") or 0)
    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_class_edit_section=section,
        product_class_edit_token=token,
    )
    await state.set_state(ClassEditing.confirm)
    await message.answer(
        "پیش‌نمایش ویرایش کلاس\n\n"
        f"شناسه: {class_id}\n"
        f"نام: {data.get('product_class_edit_name')}\n"
        f"پایه: {data.get('product_class_edit_grade')}\n"
        f"بخش: {section or 'بدون بخش'}\n\n"
        "ثبت تغییرات انجام شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ویرایش",
                        callback_data=f"product:class:edit:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:class:edit:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:class:edit:confirm:"))
async def product_class_edit_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassEditing.confirm.state
        or not token
        or token != data.get("product_class_edit_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await state.clear()
        await callback.answer("مجوز ویرایش کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int(data.get("product_class_edit_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                class_info = await update_product_class(
                    product_db,
                    class_id=class_id,
                    name=data.get("product_class_edit_name", ""),
                    grade=data.get("product_class_edit_grade", ""),
                    section=data.get("product_class_edit_section"),
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate class %s.", class_id)
        await state.clear()
        await callback.answer("ویرایش کلاس ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class.updated",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "name": class_info["name"],
                "grade": class_info["grade"],
                "section": class_info["section"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class update audit recording failed for %s.", class_id)
    await state.clear()
    await callback.answer("کلاس ویرایش شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ کلاس «{class_info['name']}» ویرایش شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="بازگشت به مدیریت کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:edit:cancel:"))
async def product_class_edit_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != ClassEditing.confirm.state
        or not token
        or token != data.get("product_class_edit_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ویرایش کلاس لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.regexp(r"^product:class:status:[0-9]+:[01]$"))
async def product_class_status_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز تغییر وضعیت کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, desired = int(parts[3]), parts[4] == "1"
    async with product_session_factory() as product_db:
        class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    action = "فعال‌سازی" if desired else "غیرفعال‌سازی"
    warning = (
        "\n\nبا غیرفعال‌سازی کلاس، دسترسی آموزشی و ارسال‌های زمان‌بندی‌شدهٔ آن متوقف می‌شود؛ "
        "هیچ داده‌ای حذف نخواهد شد."
        if not desired
        else ""
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید کلاس «{class_info['name']}» را {action} کنید؟{warning}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=f"product:class:status:confirm:{class_id}:{1 if desired else 0}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:class:status:confirm:[0-9]+:[01]$"))
async def product_class_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز تغییر وضعیت کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, desired = int(parts[4]), parts[5] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                class_info = await set_product_class_active(
                    product_db,
                    class_id=class_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not change ClassMate class status.")
        await callback.answer("تغییر وضعیت کلاس ناموفق بود.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class.status_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={"class_id": class_id, "is_active": desired},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class status audit recording failed for %s.", class_id)
    await callback.answer("وضعیت کلاس تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"وضعیت کلاس «{class_info['name']}» به {'فعال' if desired else 'غیرفعال'} تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="بازگشت به مدیریت کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


async def _build_product_class_detail_panel(
    db: AsyncSession,
    principal: TelegramPrincipal,
    product_db: AsyncSession,
    class_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    if not await _has_permission(db, principal, "platform:classes:read"):
        raise ApplicationError("مجوز مشاهدهٔ کلاس‌ها را ندارید.")

    class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None:
        raise ApplicationError("کلاس پیدا نشد.")

    subjects = await list_class_subjects(product_db, class_id=class_id)
    timetable = await list_class_timetable(product_db, class_id=class_id)
    lines = [
        f"👥 کلاس: {class_info['name']}",
        f"مدرسه: {class_info['school_name']}",
        f"پایه: {class_info['grade']}",
        f"بخش: {class_info['section'] or '—'}",
        f"وضعیت: {'فعال' if class_info['is_active'] else 'غیرفعال'}",
        f"کد ورود دانش‌آموزان: {'فعال' if class_info['has_member_join_code'] else 'صادر نشده'}",
        "",
        f"درس‌های تخصیص‌یافته: {len(subjects)}",
    ]
    lines.extend(f"• {item['name']}" for item in subjects[:20])
    lines.extend(["", f"زنگ‌های برنامه هفتگی: {len(timetable)}"])

    rows: list[list[InlineKeyboardButton]] = []
    can_manage_subjects = await _has_permission(db, principal, "platform:subjects:manage")
    can_manage_timetable = await _has_permission(db, principal, "platform:timetable:manage")
    can_manage_class = await _has_permission(db, principal, "platform:classes:manage")
    can_manage_members = await _has_permission(db, principal, "platform:members:manage")
    can_manage_announcements = await _has_permission(db, principal, "platform:announcements:manage")
    can_read_records = await _has_permission(db, principal, "platform:records:read")
    can_review_records = await _has_permission(db, principal, "platform:records:review")

    if can_manage_subjects:
        rows.append([
            InlineKeyboardButton(
                text="➕ افزودن درس",
                callback_data=f"product:subject:add:{class_id}",
            )
        ])
    if can_manage_timetable:
        rows.extend([
            [
                InlineKeyboardButton(
                    text="📅 مشاهده برنامه هفتگی",
                    callback_data=f"product:timetable:view:{class_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ افزودن زنگ",
                    callback_data=f"product:timetable:add:{class_id}",
                )
            ],
        ])
    if class_info["is_active"] and can_manage_class:
        rows.append([
            InlineKeyboardButton(
                text="🔑 کد ورود به کلاس",
                callback_data=f"product:class:join-code:{class_id}",
            )
        ])
    if can_manage_members:
        rows.extend([
            [
                InlineKeyboardButton(
                    text="👥 اعضای کلاس",
                    callback_data=f"product:members:{class_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤖 کاربران استارت‌کردهٔ ربات",
                    callback_data="product:started-users:1",
                )
            ],
        ])
    if can_manage_announcements:
        rows.append([
            InlineKeyboardButton(
                text="📢 اطلاعیه‌ها",
                callback_data=f"product:announcements:{class_id}",
            )
        ])
    if can_read_records or can_review_records:
        rows.append([
            InlineKeyboardButton(
                text="📚 محتوا و صف بررسی",
                callback_data=f"product:records:{class_id}",
            )
        ])
    if can_manage_class:
        rows.append([
            InlineKeyboardButton(
                text="✏️ ویرایش کلاس",
                callback_data=f"product:class:edit:{class_id}",
            ),
            InlineKeyboardButton(
                text=("⛔ غیرفعال‌سازی کلاس" if class_info["is_active"] else "♻️ فعال‌سازی کلاس"),
                callback_data=f"product:class:status:{class_id}:{0 if class_info['is_active'] else 1}",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ بازگشت به مدرسه",
            callback_data=f"product:school:{class_info['school_id']}",
        )
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == OWNER_CLASS_BUTTON)
async def owner_class_shortcut_handler(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await _hide_reply_keyboard(message)
    if not await _has_permission(db, principal, "platform:classes:read"):
        await message.answer("مجوز مشاهدهٔ کلاس‌ها را ندارید.")
        return
    if product_session_factory is None:
        await message.answer("اتصال دیتابیس اصلی ClassMate فعال نیست.")
        return

    try:
        async with product_session_factory() as product_db:
            candidates = await list_product_class_shortcut_candidates(product_db)
            matches = [item for item in candidates if _is_owner_class_shortcut_candidate(item)]
            current_year_matches = [
                item for item in matches if item.get("is_current_academic_year")
            ]
            if current_year_matches:
                matches = current_year_matches
            if not matches:
                await message.answer(
                    "کلاس فعال «دهم ۱۰۴» در دیتابیس ClassMate پیدا نشد. "
                    "نام پایه، شماره کلاس و فعال بودن مدرسه را بررسی کنید."
                )
                return
            if len(matches) > 1:
                rows = [
                    [
                        InlineKeyboardButton(
                            text=(
                                f"{item['school_name']} · {item['name']} · "
                                f"{item['grade']} · {item.get('academic_year_name') or 'سال نامشخص'}"
                            )[:60],
                            callback_data=f"product:class:{item['id']}",
                        )
                    ]
                    for item in matches[:20]
                ]
                rows.append([
                    InlineKeyboardButton(text="⬅️ بازگشت", callback_data="root:menu")
                ])
                await message.answer(
                    "چند کلاس فعال با مشخصات دهم ۱۰۴ پیدا شد. مدرسهٔ درست را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                )
                return

            class_id = int(matches[0]["id"])
            body, keyboard = await _build_product_class_detail_panel(
                db,
                principal,
                product_db,
                class_id,
            )
    except ApplicationError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        logger.exception("Could not open the owner class shortcut.")
        await message.answer("باز کردن پنل کلاس دهم ۱۰۴ ناموفق بود؛ اتصال دیتابیس را بررسی کنید.")
        return

    await message.answer(body, reply_markup=keyboard)


@router.callback_query(F.data.regexp(r"^product:class:[0-9]+$"))
async def product_class_detail_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            body, keyboard = await _build_product_class_detail_panel(
                db,
                principal,
                product_db,
                class_id,
            )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate class %s.", callback.data)
        await callback.answer("دریافت جزئیات کلاس ناموفق بود.", show_alert=True)
        return

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(body, reply_markup=keyboard)


def _new_member_join_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


async def _save_new_member_join_code(
    *,
    db: AsyncSession,
    principal: TelegramPrincipal,
    product_session_factory: async_sessionmaker[AsyncSession],
    class_id: int,
    event_type: str,
) -> tuple[dict, str]:
    raw_code = _new_member_join_code()
    async with product_session_factory() as product_db:
        async with product_db.begin():
            class_info = await issue_product_class_join_code(
                product_db,
                class_id=class_id,
                code=raw_code,
            )
            await store_class_join_code_secret(
                db,
                class_id=class_id,
                code=raw_code,
            )
            await record_event(
                db,
                event_type=event_type,
                actor_id=principal.user.id,
                aggregate_type="class",
                event_data={"class_id": class_id},
            )
    await db.commit()
    return class_info, raw_code


@router.callback_query(F.data.regexp(r"^product:class:join-code:[0-9]+$"))
async def product_class_join_code_issue_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Read an existing code, or ask for approval before issuing the first/replacement code."""
    if not await _require_private_callback(callback, "نمایش کد ورود کلاس"):
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز مدیریت کد ورود کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    try:
        saved_secret = await get_class_join_code_secret(db, class_id=class_id)
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
        if class_info is None:
            await callback.answer("کلاس پیدا نشد.", show_alert=True)
            return
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not read ClassMate class join code %s.", class_id)
        await callback.answer("دریافت اطلاعات کد ورود کلاس ناموفق بود.", show_alert=True)
        return

    product_code = _current_product_join_code(class_info)
    saved_code_is_current = (
        saved_secret is not None
        and class_info["member_join_code_hash"] is not None
        and secrets.compare_digest(
            str(saved_secret["join_code_hash"]),
            str(class_info["member_join_code_hash"]),
        )
    )
    if product_code or saved_code_is_current:
        raw_code = product_code or str(saved_secret["join_code"])
        if product_code and (
            not saved_code_is_current
            or str(saved_secret["join_code"]).strip().upper() != product_code
        ):
            await store_class_join_code_secret(db, class_id=class_id, code=product_code)
            await db.commit()
        elif not product_code:
            async with product_session_factory() as product_db:
                async with product_db.begin():
                    await sync_product_class_join_code_token(
                        product_db,
                        class_id=class_id,
                        code=raw_code,
                    )
        await callback.answer("کد و لینک فعال کلاس از دیتابیس اصلی خوانده شد؛ تغییری در کد انجام نشد.")
        if callback.message is not None:
            await callback.message.edit_text(
                _class_join_code_message(class_info["name"], raw_code),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="♻️ تغییر کد ورود",
                                callback_data=f"product:class:join-code:rotate:{class_id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="⬅️ بازگشت به کلاس",
                                callback_data=f"product:class:{class_id}",
                            )
                        ],
                    ]
                ),
            )
        return

    token = secrets.token_urlsafe(8)
    await state.clear()
    await state.update_data(
        product_join_code_issue_token=token,
        product_join_code_issue_class_id=class_id,
        product_join_code_issue_legacy=class_info["member_join_code_hash"] is not None,
    )
    await state.set_state(JoinCodeIssue.confirm)
    legacy_note = (
        "کد قبلی فقط به‌صورت هش ذخیره شده و قابل بازیابی نیست؛ با صدور کد تازه جایگزین می‌شود."
        if class_info["member_join_code_hash"] is not None
        else "برای این کلاس هنوز کد ورود صادر نشده است."
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "پیش‌نمایش صدور کد ورود کلاس\n\n"
            f"کلاس: {class_info['name']}\n"
            f"وضعیت: {legacy_note}\n\n"
            "با تأیید، یک کد جدید ساخته و ذخیره می‌شود. هیچ کدی تا پیش از تأیید صادر نخواهد شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ تأیید صدور کد",
                            callback_data=f"product:class:join-code:issue-confirm:{token}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو عملیات",
                            callback_data=f"product:class:join-code:issue-cancel:{token}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:join-code:issue-confirm:"))
async def product_class_join_code_issue_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != JoinCodeIssue.confirm.state
        or not token
        or token != data.get("product_join_code_issue_token")
    ):
        await callback.answer("این پیش‌نمایش منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await state.clear()
        await callback.answer("مجوز مدیریت کد ورود کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_join_code_issue_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
        if class_info is None:
            raise ApplicationError("کلاس پیدا نشد.")
        saved_secret = await get_class_join_code_secret(db, class_id=class_id)
        product_code = _current_product_join_code(class_info)
        saved_code_is_current = (
            saved_secret is not None
            and class_info["member_join_code_hash"] is not None
            and secrets.compare_digest(
                str(saved_secret["join_code_hash"]),
                str(class_info["member_join_code_hash"]),
            )
        )
        if product_code or saved_code_is_current:
            raw_code = product_code or str(saved_secret["join_code"])
            if product_code and (
                not saved_code_is_current
                or str(saved_secret["join_code"]).strip().upper() != product_code
            ):
                await store_class_join_code_secret(db, class_id=class_id, code=product_code)
                await db.commit()
            elif not product_code:
                async with product_session_factory() as product_db:
                    async with product_db.begin():
                        await sync_product_class_join_code_token(
                            product_db,
                            class_id=class_id,
                            code=raw_code,
                        )
            await state.clear()
            await callback.answer("کد فعال کلاس از دیتابیس اصلی خوانده شد؛ صدور تکراری انجام نشد.")
            if callback.message is not None:
                await callback.message.edit_text(
                    _class_join_code_message(class_info["name"], raw_code),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="♻️ تغییر کد ورود",
                                    callback_data=f"product:class:join-code:rotate:{class_id}",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="⬅️ بازگشت به کلاس",
                                    callback_data=f"product:class:{class_id}",
                                )
                            ],
                        ]
                    ),
                )
            return

        event_type = (
            "product.class.member_join_code_issued"
            if not data.get("product_join_code_issue_legacy")
            else "product.class.member_join_code_legacy_replaced"
        )
        code_info, raw_code = await _save_new_member_join_code(
            db=db,
            principal=principal,
            product_session_factory=product_session_factory,
            class_id=class_id,
            event_type=event_type,
        )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        await db.rollback()
        logger.exception("Could not issue ClassMate class join code %s.", class_id)
        await callback.answer("صدور کد ورود ناموفق بود؛ فرم برای تلاش دوباره حفظ شد.", show_alert=True)
        return

    await state.clear()
    await callback.answer("کد ورود پس از تأیید صادر شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            _class_join_code_message(
                code_info["name"],
                raw_code,
                replaced_legacy_code=bool(data.get("product_join_code_issue_legacy")),
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="♻️ تغییر کد ورود",
                            callback_data=f"product:class:join-code:rotate:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:join-code:issue-cancel:"))
async def product_class_join_code_issue_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != JoinCodeIssue.confirm.state
        or not token
        or token != data.get("product_join_code_issue_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    class_id = int(data.get("product_join_code_issue_class_id") or 0)
    await state.clear()
    await callback.answer("صدور کد لغو شد؛ تغییری ثبت نشد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "صدور کد ورود لغو شد؛ تغییری ثبت نشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:class:join-code:rotate:[0-9]+$"))
async def product_class_join_code_rotate_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "تغییر کد ورود کلاس"):
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await callback.answer("مجوز مدیریت کد ورود کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    try:
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
        if class_info is None:
            raise ApplicationError("کلاس پیدا نشد.")
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not prepare class join code rotation %s.", class_id)
        await callback.answer("آماده‌سازی تغییر کد ناموفق بود.", show_alert=True)
        return

    token = secrets.token_urlsafe(8)
    await state.clear()
    await state.update_data(
        product_join_code_rotation_token=token,
        product_join_code_rotation_class_id=class_id,
    )
    await state.set_state(JoinCodeRotation.confirm)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "پیش‌نمایش تغییر کد ورود کلاس\n\n"
            f"کلاس: {class_info['name']}\n\n"
            "⚠️ با تأیید، کد فعلی و لینک‌های عضویت قبلی فوراً نامعتبر می‌شوند. "
            "دانش‌آموزان جدید باید کد تازه را دریافت کنند.\n\n"
            "تغییر کد انجام شود؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ تأیید تغییر کد",
                            callback_data=f"product:class:join-code:rotate-confirm:{token}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو عملیات",
                            callback_data=f"product:class:join-code:rotate-cancel:{token}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:join-code:rotate-confirm:"))
async def product_class_join_code_rotate_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != JoinCodeRotation.confirm.state
        or not token
        or token != data.get("product_join_code_rotation_token")
    ):
        await callback.answer("این پیش‌نمایش منقضی شده است.", show_alert=True)
        return
    if not await _require_private_callback(callback, "تغییر کد ورود کلاس"):
        return
    if not await _has_permission(db, principal, "platform:classes:manage"):
        await state.clear()
        await callback.answer("مجوز مدیریت کد ورود کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_join_code_rotation_class_id") or 0)
    try:
        code_info, raw_code = await _save_new_member_join_code(
            db=db,
            principal=principal,
            product_session_factory=product_session_factory,
            class_id=class_id,
            event_type="product.class.member_join_code_rotated",
        )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        await db.rollback()
        logger.exception("Could not rotate ClassMate class join code %s.", class_id)
        await callback.answer("تغییر کد ورود ناموفق بود؛ فرم برای تلاش دوباره حفظ شد.", show_alert=True)
        return

    await state.clear()
    await callback.answer("کد قبلی پس از تأیید باطل و کد جدید صادر شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            _class_join_code_message(code_info["name"], raw_code),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="♻️ تغییر کد ورود",
                            callback_data=f"product:class:join-code:rotate:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:class:join-code:rotate-cancel:"))
async def product_class_join_code_rotate_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != JoinCodeRotation.confirm.state
        or not token
        or token != data.get("product_join_code_rotation_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    class_id = int(data.get("product_join_code_rotation_class_id") or 0)
    await state.clear()
    await callback.answer("تغییر کد لغو شد؛ کد فعلی معتبر ماند.")
    if callback.message is not None:
        await callback.message.edit_text(
            "تغییر کد لغو شد؛ کد قبلی همچنان معتبر است.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔑 نمایش کد فعلی",
                            callback_data=f"product:class:join-code:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )




@router.callback_query(F.data.regexp(r"^product:subject:add:[0-9]+$"))
async def product_subject_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:subjects:manage"):
        await callback.answer("مجوز مدیریت درس‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not validate class for subject creation.")
        await callback.answer("بررسی کلاس ناموفق بود.", show_alert=True)
        return
    if class_info is None or not class_info["is_active"]:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await _close_inline_panel(callback.message)
    await state.update_data(product_subject_class_id=class_id)
    await state.set_state(SubjectCreation.name)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("نام درس را وارد کنید (۲ تا ۱۰۰ نویسه):", reply_markup=shared_wizard_controls())


@router.message(SubjectCreation.name, F.text)
async def product_subject_name_input(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 100:
        await message.answer(
            "خطا: نام درس باید بین ۲ تا ۱۰۰ نویسه باشد.\n\n"
            "نام درس را دوباره وارد کنید:",
            reply_markup=shared_wizard_controls(),
        )
        return
    if not await _has_permission(db, principal, "platform:subjects:manage"):
        await state.clear()
        await message.answer("مجوز مدیریت درس‌ها را ندارید.")
        return

    data = await state.get_data()
    class_id = int(data.get("product_subject_class_id") or 0)
    if class_id <= 0:
        await state.clear()
        await message.answer("فرم افزودن درس منقضی شده است؛ دوباره شروع کنید.")
        return

    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_subject_name=name,
        product_subject_token=token,
    )
    await state.set_state(SubjectCreation.confirm)
    await message.answer(
        "پیش‌نمایش ایجاد و تخصیص درس\n\n"
        f"نام درس: {name}\n"
        f"شناسه کلاس مقصد: {class_id}\n\n"
        "با تأیید، درس ایجاد و به همین کلاس تخصیص داده می‌شود.\n"
        "هیچ تغییری تا انتخاب «تأیید» ثبت نمی‌شود.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ تأیید ایجاد و تخصیص",
                        callback_data=f"product:subject:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو عملیات",
                        callback_data=f"product:subject:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:subject:confirm:"))
async def product_subject_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SubjectCreation.confirm.state
        or not token
        or token != data.get("product_subject_token")
    ):
        await callback.answer("این پیش‌نمایش منقضی شده است؛ عملیات اجرا نشد.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:subjects:manage"):
        await state.clear()
        await callback.answer("مجوز مدیریت درس‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_subject_class_id") or 0)
    name = str(data.get("product_subject_name") or "")
    if class_id <= 0 or not name:
        await state.clear()
        await callback.answer("اطلاعات فرم معتبر نیست؛ دوباره شروع کنید.", show_alert=True)
        return

    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                subject = await create_and_assign_product_subject(
                    product_db,
                    class_id=class_id,
                    name=name,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create or assign ClassMate subject.")
        await callback.answer("ثبت درس ناموفق بود؛ فرم برای تلاش دوباره حفظ شد.", show_alert=True)
        return

    audit_warning = False
    try:
        await record_event(
            db,
            event_type="product.class_subject.assigned",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "school_id": subject["school_id"],
                "subject_id": subject["id"],
                "subject_name": subject["name"],
                "created": subject["created"],
                "assigned": subject["assigned"],
            },
        )
        await db.commit()
    except Exception:
        audit_warning = True
        await db.rollback()
        logger.exception("Subject assignment audit failed for class %s.", class_id)

    await state.clear()
    result_label = (
        "درس ایجاد و به کلاس تخصیص داده شد."
        if subject["created"]
        else "درس موجود به کلاس تخصیص داده شد."
    )
    if not subject["assigned"]:
        result_label = "این درس از قبل به کلاس تخصیص داشت."
    notice = "\n⚠️ ثبت رویداد ممیزی نیازمند بررسی است." if audit_warning else ""
    await callback.answer("تغییر پس از تأیید ثبت شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ {result_label}{notice}\nدرس: {subject['name']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="بازگشت به مدیریت کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:subject:cancel:"))
async def product_subject_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != SubjectCreation.confirm.state
        or not token
        or token != data.get("product_subject_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    class_id = int(data.get("product_subject_class_id") or 0)
    await state.clear()
    await callback.answer("عملیات لغو شد؛ تغییری ثبت نشد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "افزودن درس لغو شد؛ تغییری ثبت نشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به مدیریت کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:view:[0-9]+$"))
async def product_timetable_view_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:timetable:manage"):
        await callback.answer("مجوز مشاهده برنامه هفتگی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            if class_info is None:
                await callback.answer("کلاس پیدا نشد.", show_alert=True)
                return
            entries = await list_class_timetable(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load timetable for class %s.", callback.data)
        await callback.answer("دریافت برنامه هفتگی ناموفق بود.", show_alert=True)
        return

    day_names = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
    lines = [f"📅 برنامه هفتگی کلاس {class_info['name']}", ""]
    if not entries:
        lines.append("هنوز زنگی برای این کلاس ثبت نشده است.")
    else:
        for weekday in range(7):
            day_entries = [item for item in entries if item["weekday"] == weekday]
            if not day_entries:
                continue
            lines.append(f"**{day_names[weekday]}**")
            for item in day_entries:
                start = item["starts_at"].strftime("%H:%M") if item["starts_at"] else "—"
                end = item["ends_at"].strftime("%H:%M") if item["ends_at"] else "—"
                room = f" · {item['room']}" if item["room"] else ""
                lines.append(
                    f"زنگ {item['period']}: {item['subject_name']} · {start} تا {end}{room}"
                )
            lines.append("")
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="➕ افزودن زنگ",
                            callback_data=f"product:timetable:add:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:add:[0-9]+$"))
async def product_timetable_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:timetable:manage"):
        await callback.answer("مجوز مدیریت برنامه هفتگی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            subjects = await list_class_subjects(product_db, class_id=class_id)
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not prepare timetable creation.")
        await callback.answer("آماده‌سازی برنامه هفتگی ناموفق بود.", show_alert=True)
        return
    if class_info is None or not class_info["is_active"]:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    if not subjects:
        await callback.answer(
            "ابتدا حداقل یک درس به کلاس تخصیص دهید.",
            show_alert=True,
        )
        return

    await _close_inline_panel(callback.message)
    await state.clear()
    token = secrets.token_urlsafe(8)
    await state.update_data(
        product_timetable_token=token,
        product_timetable_class_id=class_id,
        product_timetable_subjects=subjects,
    )
    await state.set_state(TimetableCreation.weekday)
    day_names = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
    rows = [
        [
            InlineKeyboardButton(
                text=day_names[index],
                callback_data=f"product:timetable:weekday:{class_id}:{index}",
            )
        ]
        for index in range(7)
    ]
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"روز هفته برای کلاس {class_info['name']} را انتخاب کنید:",
            reply_markup=add_wizard_cancel(InlineKeyboardMarkup(inline_keyboard=rows)),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:weekday:[0-9]+:[0-6]$"))
async def timetable_weekday_select_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    parts = (callback.data or "").split(":")
    if (
        await state.get_state() != TimetableCreation.weekday.state
        or len(parts) != 5
        or int(parts[3]) != int(data.get("product_timetable_class_id") or 0)
    ):
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    weekday = int(parts[4])
    await state.update_data(product_timetable_weekday=weekday)
    await state.set_state(TimetableCreation.subject)
    subjects = data.get("product_timetable_subjects", [])
    rows = [
        [
            InlineKeyboardButton(
                text=subject["name"][:45],
                callback_data=f"product:timetable:subject:{subject['id']}",
            )
        ]
        for subject in subjects[:50]
    ]
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "درس این زنگ را انتخاب کنید:",
            reply_markup=add_wizard_cancel(InlineKeyboardMarkup(inline_keyboard=rows)),
        )


@router.callback_query(F.data.regexp(r"^product:timetable:subject:[0-9]+$"))
async def timetable_subject_select_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if await state.get_state() != TimetableCreation.subject:
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    subject_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    subjects = data.get("product_timetable_subjects", [])
    subject = next((item for item in subjects if int(item["id"]) == subject_id), None)
    if subject is None:
        await callback.answer("درس انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    await _close_inline_panel(callback.message)
    await state.update_data(
        product_timetable_subject_id=subject_id,
        product_timetable_subject_name=subject["name"],
    )
    await state.set_state(TimetableCreation.period)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("شماره زنگ را از ۱ تا ۱۶ وارد کنید:", reply_markup=shared_wizard_controls())


@router.message(TimetableCreation.period, F.text)
async def timetable_period_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 16:
        await message.answer("خطا: شماره زنگ باید عددی بین ۱ تا ۱۶ باشد.\n\nشماره زنگ را از ۱ تا ۱۶ دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_timetable_period=int(value))
    await state.set_state(TimetableCreation.time_range)
    await message.answer("بازه زمانی را با قالب 08:00-08:45 وارد کنید؛ اگر زمان مشخص نیست، «-» بفرستید:", reply_markup=shared_wizard_controls())


@router.message(TimetableCreation.time_range, F.text)
async def timetable_time_range_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value == "-":
        start_time = end_time = None
    else:
        parts = value.split("-")
        if len(parts) != 2:
            await message.answer("خطا: قالب زمان نامعتبر است.\n\nبازه زمانی را با قالب 08:00-08:45 دوباره وارد کنید:", reply_markup=shared_wizard_controls())
            return
        try:
            from datetime import time as time_type

            start = time_type.fromisoformat(parts[0].strip())
            end = time_type.fromisoformat(parts[1].strip())
        except ValueError:
            await message.answer("خطا: زمان باید با قالب HH:MM باشد.\n\nبازه زمانی را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
            return
        if end <= start:
            await message.answer("خطا: زمان پایان باید بعد از زمان شروع باشد.\n\nبازه زمانی را اصلاح کنید:", reply_markup=shared_wizard_controls())
            return
        start_time, end_time = start.strftime("%H:%M"), end.strftime("%H:%M")
    await state.update_data(
        product_timetable_start=start_time,
        product_timetable_end=end_time,
    )
    await state.set_state(TimetableCreation.room)
    await message.answer("نام کلاس/اتاق را وارد کنید؛ اگر ندارد، «-» بفرستید:", reply_markup=shared_wizard_controls())


@router.message(TimetableCreation.room, F.text)
async def timetable_room_input(message: Message, state: FSMContext) -> None:
    room = " ".join((message.text or "").split())
    if room == "-":
        room = None
    if room is not None and len(room) > 80:
        await message.answer("خطا: نام کلاس/اتاق حداکثر ۸۰ نویسه است.\n\nنام اتاق را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_timetable_room=room)
    data = await state.get_data()
    await state.set_state(TimetableCreation.confirm)
    day_names = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
    time_label = (
        f"{data.get('product_timetable_start')} تا {data.get('product_timetable_end')}"
        if data.get("product_timetable_start")
        else "زمان مشخص نشده"
    )
    await message.answer(
        "پیش‌نمایش ثبت زنگ\n\n"
        f"روز: {day_names[data['product_timetable_weekday']]}\n"
        f"درس: {data['product_timetable_subject_name']}\n"
        f"زنگ: {data['product_timetable_period']}\n"
        f"زمان: {time_label}\n"
        f"کلاس/اتاق: {room or '—'}\n\n"
        "ثبت نهایی شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ ثبت زنگ",
                        callback_data=f"product:timetable:confirm:{data['product_timetable_token']}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:timetable:cancel:{data['product_timetable_token']}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:timetable:confirm:"))
async def timetable_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != TimetableCreation.confirm.state
        or not token
        or token != data.get("product_timetable_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:timetable:manage"):
        await state.clear()
        await callback.answer("مجوز مدیریت برنامه هفتگی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_timetable_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                entry = await create_product_timetable_entry(
                    product_db,
                    class_id=class_id,
                    subject_id=int(data.get("product_timetable_subject_id") or 0),
                    weekday=int(data.get("product_timetable_weekday") or 0),
                    period=int(data.get("product_timetable_period") or 0),
                    starts_at=data.get("product_timetable_start"),
                    ends_at=data.get("product_timetable_end"),
                    room=data.get("product_timetable_room"),
                )
    except ResourceConflict as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not create ClassMate timetable entry.")
        await state.clear()
        await callback.answer("ثبت زنگ در دیتابیس ClassMate ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type="product.timetable_entry.created",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "entry_id": entry["id"],
                "weekday": entry["weekday"],
                "period": entry["period"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Timetable audit recording failed for class %s.", class_id)

    await state.clear()
    await callback.answer("زنگ ثبت شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "✅ زنگ برنامه هفتگی ثبت شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📅 مشاهده برنامه هفتگی",
                            callback_data=f"product:timetable:view:{class_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ بازگشت به کلاس",
                            callback_data=f"product:class:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:timetable:cancel:"))
async def timetable_create_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != TimetableCreation.confirm.state
        or not token
        or token != data.get("product_timetable_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ثبت زنگ لغو شد؛ تغییری ثبت نشد.")




@router.callback_query(F.data.regexp(r"^product:member:role:[0-9]+:[0-9]+$"))
async def product_class_member_role_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    if not await _require_private_callback(callback, "تغییر نقش عضو"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت نقش اعضا را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, user_id = int(parts[3]), int(parts[4])
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "نقش جدید عضو را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👨‍🎓 دانش‌آموز",
                            callback_data=f"pmr:{class_id}:{user_id}:student",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👨‍🏫 مدیر کلاس",
                            callback_data=f"pmr:{class_id}:{user_id}:class_manager",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🛡 تأییدکننده",
                            callback_data=f"pmr:{class_id}:{user_id}:trusted_approver",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:members:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^pmr:[0-9]+:[0-9]+:(student|class_manager|trusted_approver)$"))
async def product_class_member_role_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    if not await _require_private_callback(callback, "تغییر نقش عضو"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت نقش اعضا را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, user_id, role = int(parts[1]), int(parts[2]), parts[3]
    role_labels = {
        "student": "دانش‌آموز",
        "class_manager": "مدیر کلاس",
        "trusted_approver": "تأییدکننده",
    }
    if role not in role_labels:
        await callback.answer("نقش انتخاب‌شده معتبر نیست.", show_alert=True)
        return

    token = secrets.token_urlsafe(8)
    await state.clear()
    await state.update_data(
        member_role_change_token=token,
        member_role_change_class_id=class_id,
        member_role_change_user_id=user_id,
        member_role_change_role=role,
    )
    await state.set_state(MemberRoleChangeWizard.confirm)
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "پیش‌نمایش تغییر نقش عضو\n\n"
            f"شناسه کلاس: {class_id}\n"
            f"شناسه عضو: {user_id}\n"
            f"نقش جدید: {role_labels[role]}\n\n"
            "با تأیید، نقش عضویت این کاربر در همین کلاس تغییر می‌کند. ادامه می‌دهید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ تأیید تغییر نقش",
                            callback_data=f"pmrc:{token}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو عملیات",
                            callback_data=f"pmrx:{token}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.startswith("pmrc:"))
async def product_class_member_role_apply_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberRoleChangeWizard.confirm.state
        or not token
        or token != data.get("member_role_change_token")
    ):
        await callback.answer("این پیش‌نمایش منقضی شده است.", show_alert=True)
        return
    if not await _require_private_callback(callback, "تغییر نقش عضو"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await state.clear()
        await callback.answer("مجوز مدیریت نقش اعضا را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("member_role_change_class_id") or 0)
    user_id = int(data.get("member_role_change_user_id") or 0)
    role = str(data.get("member_role_change_role") or "")
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                changed = await set_product_class_member_role(
                    product_db,
                    class_id=class_id,
                    user_id=user_id,
                    role=role,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not set ClassMate class member role.")
        await callback.answer("تغییر نقش عضو ناموفق بود؛ فرم برای تلاش دوباره حفظ شد.", show_alert=True)
        return
    if not changed:
        await callback.answer("عضویت فعال پیدا نشد.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class_member.role_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={"class_id": class_id, "user_id": user_id, "role": role},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class member role audit recording failed.")
    await state.clear()
    await callback.answer("نقش عضو پس از تأیید تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "✅ نقش عضو تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👥 بازگشت به اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("pmrx:"))
async def product_class_member_role_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberRoleChangeWizard.confirm.state
        or not token
        or token != data.get("member_role_change_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    class_id = int(data.get("member_role_change_class_id") or 0)
    await state.clear()
    await callback.answer("تغییر نقش لغو شد؛ نقش فعلی حفظ شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "تغییر نقش لغو شد؛ هیچ تغییری ثبت نشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👥 بازگشت به اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:records:[0-9]+(?::[1-9][0-9]*)?$"))
async def product_class_records_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    can_read = await _has_permission(db, principal, "platform:records:read")
    can_review = await _has_permission(db, principal, "platform:records:review")
    if not can_read and not can_review:
        await callback.answer("مجوز مشاهدهٔ محتوای آموزشی را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        parts = (callback.data or "").split(":")
        class_id = int(parts[2])
        page = int(parts[3]) if len(parts) == 4 else 1
        if page < 1:
            raise ValueError
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            fetched = await list_product_class_records(
                product_db,
                class_id=class_id,
                status="PENDING",
                limit=9,
                offset=(page - 1) * 8,
            )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return
    except Exception:
        logger.exception("Could not load pending records for ClassMate class %s.", class_id)
        await callback.answer("دریافت صف بررسی ناموفق بود.", show_alert=True)
        return
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return

    has_next = len(fetched) > 8
    records = fetched[:8]
    counts = {"homework": 0, "exam": 0, "report": 0}
    for item in records:
        counts[item["entity_type"]] += 1
    lines = [
        f"📚 محتوای آموزشی کلاس {class_info['name']}",
        f"صفحه: {page} · موارد این صفحه: {len(records)}",
        f"تکلیف‌ها: {counts['homework']}",
        f"آزمون‌ها: {counts['exam']}",
        f"گزارش‌های روزانه: {counts['report']}",
        "",
    ]
    rows = []
    for item in records:
        label = {
            "homework": "تکلیف",
            "exam": "آزمون",
            "report": "گزارش",
        }[item["entity_type"]]
        title = str(item["title"])[:30]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🕓 {label}: {title}",
                    callback_data=(
                        f"product:record:detail:{class_id}:"
                        f"{item['entity_type']}:{item['id']}"
                    ),
                )
            ]
        )
    if not records:
        lines.append(
            "محتوای در انتظار بررسی وجود ندارد."
            if page == 1
            else "موردی در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"product:records:{class_id}:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"product:records:{class_id}:{page + 1}",
                )
            )
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ بازگشت به کلاس",
                callback_data=f"product:class:{class_id}",
            )
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(
    F.data.regexp(r"^product:record:detail:[0-9]+:(homework|exam|report):[0-9]+$")
)
async def product_class_record_detail_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:records:read") and not await _has_permission(
        db, principal, "platform:records:review"
    ):
        await callback.answer("مجوز مشاهدهٔ محتوا را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, entity_type, record_id = int(parts[3]), parts[4], int(parts[5])
    try:
        async with product_session_factory() as product_db:
            record = await get_product_class_record(
                product_db,
                class_id=class_id,
                entity_type=entity_type,
                record_id=record_id,
            )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate educational record detail.")
        await callback.answer("دریافت جزئیات محتوا ناموفق بود.", show_alert=True)
        return
    if record is None:
        await callback.answer("محتوا پیدا نشد.", show_alert=True)
        return
    type_label = {"homework": "تکلیف", "exam": "آزمون", "report": "گزارش روزانه"}[entity_type]
    body = str(record["body"] or "")
    text_body = body[:2600] + ("…" if len(body) > 2600 else "")
    lines = [
        f"📄 {type_label}: {record['title']}",
        f"کلاس: {record['class_name']}",
        f"درس: {record['subject_name']}",
        f"زمان/مهلت: {record['schedule_label']}",
        f"وضعیت: {record['status']}",
        "",
        text_body,
    ]
    rows = []
    if record["status"] in {"PENDING", "REVISION_REQUESTED"} and await _has_permission(
        db, principal, "platform:records:review"
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ تأیید و انتشار",
                    callback_data=f"product:record:review:{class_id}:{entity_type}:{record_id}:approve",
                ),
                InlineKeyboardButton(
                    text="⛔ رد محتوا",
                    callback_data=f"product:record:review:{class_id}:{entity_type}:{record_id}:reject",
                ),
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به صف بررسی",
                    callback_data=f"product:records:{class_id}",
                )
            ],
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(
    F.data.regexp(r"^product:record:review:[0-9]+:(homework|exam|report):[0-9]+:(approve|reject)$")
)
async def product_record_review_prompt_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    if not await _has_permission(db, principal, "platform:records:review"):
        await callback.answer("مجوز بررسی محتوا را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, entity_type, record_id, decision = int(parts[3]), parts[4], int(parts[5]), parts[6]
    action = "تأیید و انتشار" if decision == "approve" else "رد"
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا از «{action}» این محتوا اطمینان دارید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=(
                                f"prc:{class_id}:{entity_type}:{record_id}:{decision}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:record:detail:{class_id}:{entity_type}:{record_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(
    F.data.regexp(r"^prc:[0-9]+:(homework|exam|report):[0-9]+:(approve|reject)$")
)
async def product_record_review_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:records:review"):
        await callback.answer("مجوز بررسی محتوا را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, entity_type, record_id, decision = (
        int(parts[1]),
        parts[2],
        int(parts[3]),
        parts[4],
    )
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                record = await review_product_class_record(
                    product_db,
                    class_id=class_id,
                    entity_type=entity_type,
                    record_id=record_id,
                    decision=decision,
                    reviewer_telegram_id=principal.telegram_account.telegram_user_id,
                    reviewer_name=principal.user.display_name,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not review ClassMate educational record.")
        await callback.answer("بررسی محتوا ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type=(
                f"product.{entity_type}.approved"
                if decision == "approve"
                else f"product.{entity_type}.rejected"
            ),
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "record_id": record_id,
                "entity_type": entity_type,
                "published_to_group": record["published_to_group"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Work audit recording failed for record %s.", record_id)

    await callback.answer("محتوا تأیید و منتشر شد." if decision == "approve" else "محتوا رد شد.")
    if callback.message is not None:
        notice = (
            "به صف ارسال گروه اضافه شد."
            if record["published_to_group"]
            else "در گروهی منتشر نشد؛ برای کلاس گروه متصل نشده است."
        ) if decision == "approve" else "هیچ پیامی به گروه ارسال نشد."
        await callback.message.edit_text(
            f"✅ وضعیت محتوا: {record['status']}\n{notice}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📚 بازگشت به محتوای کلاس",
                            callback_data=f"product:records:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:started-users:[1-9][0-9]*$"))
async def product_started_users_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "کاربران استارت‌کردهٔ ربات"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مشاهدهٔ کاربران ربات را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    page = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        async with product_session_factory() as product_db:
            users = await list_product_started_users(
                product_db,
                limit=9,
                offset=(page - 1) * 8,
            )
    except Exception:
        logger.exception("Could not load ClassMate started users.")
        await callback.answer("دریافت کاربران ربات ناموفق بود.", show_alert=True)
        return

    has_next = len(users) > 8
    shown = users[:8]
    lines = [
        "🤖 کاربران شروع‌کنندهٔ ربات ClassMate",
        f"صفحه: {page} · تعداد نمایش‌داده‌شده: {len(shown)}",
        "این فهرست همهٔ افرادی را نشان می‌دهد که ربات را Start کرده‌اند؛ عضویت در کلاس شرط نمایش نیست.",
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for user in shown:
        username = f"@{user['username']}" if user.get("username") else "نام کاربری: ثبت نشده"
        status = "فعال" if user["user_is_active"] else "غیرفعال"
        lines.append(
            f"• {user['full_name']} · {username} · {user['telegram_id']} · {status}"
        )
        if user.get("username"):
            rows.append([
                InlineKeyboardButton(
                    text=f"↗️ @{str(user['username'])[:32]}",
                    url=f"https://t.me/{str(user['username']).lstrip('@')}",
                )
            ])
    if not shown:
        lines.append("هنوز کاربری ربات را Start نکرده است.")
    if page > 1 or has_next:
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"product:started-users:{page - 1}",
            ))
        if has_next:
            nav.append(InlineKeyboardButton(
                text="صفحه بعد ➡️",
                callback_data=f"product:started-users:{page + 1}",
            ))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="⬅️ بازگشت به کلاس", callback_data="root:menu")
    ])
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:members:[0-9]+(?::[1-9][0-9]*)?$"))
async def product_class_members_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "مدیریت اعضای کلاس"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت اعضای کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        parts = (callback.data or "").split(":")
        class_id = int(parts[2])
        page = int(parts[3]) if len(parts) == 4 else 1
        if page < 1:
            raise ValueError
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            fetched = await list_product_class_members(
                product_db,
                class_id=class_id,
                limit=9,
                offset=(page - 1) * 8,
            )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate class members.")
        await callback.answer("دریافت اعضای کلاس ناموفق بود.", show_alert=True)
        return
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return

    has_next = len(fetched) > 8
    members = fetched[:8]
    lines = [
        f"👥 اعضای کلاس {class_info['name']}",
        f"صفحه: {page} · تعداد نمایش‌داده‌شده: {len(members)}",
        "فهرست شامل اعضای ثبت‌شدهٔ همین کلاس است. برای دیدن همهٔ افرادی که ربات را Start کرده‌اند، پنل «کاربران استارت‌کردهٔ ربات» را باز کنید.",
        "نام کاربری تلگرام در صورت عمومی بودن نمایش داده می‌شود؛ برای حساب‌های بدون username فقط نام و شناسه قابل مشاهده است.",
        "",
    ]
    rows = []
    for member in members:
        active = member["membership_is_active"] and member["user_is_active"]
        role_label = {
            "student": "دانش‌آموز",
            "class_manager": "مدیر کلاس",
            "trusted_approver": "تأییدکننده",
        }.get(member["membership_role"], member["membership_role"])
        username = f"@{member['username']}" if member.get("username") else "بدون نام کاربری"
        lines.append(
            f"• {member['full_name']} · {username} · {member['telegram_id']} · "
            f"{role_label} · {'فعال' if active else 'غیرفعال'}"
        )
        if member.get("username"):
            rows.append([
                InlineKeyboardButton(
                    text=f"↗️ @{str(member['username'])[:32]}",
                    url=f"https://t.me/{str(member['username']).lstrip('@')}",
                )
            ])
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"⛔ تعلیق · {member['full_name'][:18]}"
                        if active
                        else f"♻️ فعال‌سازی · {member['full_name'][:18]}"
                    ),
                    callback_data=(
                        f"product:member:status:{class_id}:{member['user_id']}:"
                        f"{0 if active else 1}"
                    ),
                ),
                InlineKeyboardButton(
                    text="🎖 نقش",
                    callback_data=f"product:member:role:{class_id}:{member['user_id']}",
                ),
            ]
        )

    if not members:
        lines.append(
            "هنوز عضوی به این کلاس اضافه نشده است."
            if page == 1
            else "عضوی در این صفحه وجود ندارد؛ به صفحه قبل برگردید."
        )
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"product:members:{class_id}:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"product:members:{class_id}:{page + 1}",
                )
            )
        rows.append(navigation)
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ افزودن عضو با Telegram ID",
                    callback_data=f"product:member:add:{class_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به کلاس",
                    callback_data=f"product:class:{class_id}",
                )
            ],
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:member:add:[0-9]+$"))
async def product_class_member_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "افزودن عضو"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز افزودن عضو را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    async with product_session_factory() as product_db:
        class_info = await get_product_class(product_db, class_id=class_id)
    if class_info is None or not class_info["is_active"]:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await _close_inline_panel(callback.message)
    await state.update_data(product_member_class_id=class_id)
    await state.set_state(MemberCreation.telegram_id)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("Telegram ID عضو را فقط به‌صورت عدد وارد کنید:", reply_markup=shared_wizard_controls())


@router.message(MemberCreation.telegram_id, F.text)
async def product_member_telegram_id_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or not 0 < int(value) <= 9_223_372_036_854_775_807:
        await message.answer("خطا: Telegram ID معتبر نیست؛ فقط عدد مثبت وارد کنید.\n\nTelegram ID عضو را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_member_telegram_id=int(value))
    await state.set_state(MemberCreation.full_name)
    await message.answer("نام و نام خانوادگی عضو را وارد کنید:", reply_markup=shared_wizard_controls())


@router.message(MemberCreation.full_name, F.text)
async def product_member_full_name_input(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not 1 <= len(name) <= 160:
        await message.answer("خطا: نام باید بین ۱ تا ۱۶۰ نویسه باشد.\n\nنام و نام خانوادگی را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    data = await state.get_data()
    token = secrets.token_urlsafe(8)
    await state.update_data(product_member_full_name=name, product_member_token=token)
    await state.set_state(MemberCreation.confirm)
    await message.answer(
        "پیش‌نمایش افزودن عضو\n\n"
        f"Telegram ID: {data['product_member_telegram_id']}\n"
        f"نام: {name}\n\n"
        "عضویت ثبت شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ افزودن عضو",
                        callback_data=f"product:member:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:member:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:member:confirm:"))
async def product_member_add_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "افزودن عضو"):
        return
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberCreation.confirm.state
        or not token
        or token != data.get("product_member_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await state.clear()
        await callback.answer("مجوز افزودن عضو را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    class_id = int(data.get("product_member_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                member = await add_product_class_member(
                    product_db,
                    class_id=class_id,
                    telegram_id=int(data["product_member_telegram_id"]),
                    full_name=data["product_member_full_name"],
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not add ClassMate class member.")
        await state.clear()
        await callback.answer("افزودن عضو ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type="product.class_member.added",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "user_id": member["user_id"],
                "telegram_id": member["telegram_id"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class member audit recording failed.")

    await state.clear()
    await callback.answer("عضویت ثبت شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "✅ عضو به کلاس اضافه شد.\n"
            f"نام: {member['full_name']}\n"
            f"Telegram ID: {member['telegram_id']}\n\n"
            "برای عضویت سایر دانش‌آموزان، از کد ورود مشترک در پنل جزئیات کلاس استفاده کنید.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👥 بازگشت به اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        )
                    ]
                ]
            ),
        )




@router.callback_query(F.data.regexp(r"^product:member:status:[0-9]+:[0-9]+:[01]$"))
async def product_member_status_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    if not await _require_private_callback(callback, "تغییر وضعیت عضو"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت اعضای کلاس را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, user_id, desired = int(parts[3]), int(parts[4]), parts[5] == "1"
    action = "فعال‌سازی مجدد" if desired else "تعلیق"
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید این عضویت را {action} کنید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=(
                                f"product:member:status:confirm:{class_id}:{user_id}:"
                                f"{1 if desired else 0}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:members:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:member:status:confirm:[0-9]+:[0-9]+:[01]$"))
async def product_member_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _require_private_callback(callback, "تغییر وضعیت عضو"):
        return
    if not await _has_permission(db, principal, "platform:members:manage"):
        await callback.answer("مجوز مدیریت اعضای کلاس را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, user_id, desired = int(parts[4]), int(parts[5]), parts[6] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                changed = await set_product_class_member_active(
                    product_db,
                    class_id=class_id,
                    user_id=user_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate membership status.")
        await callback.answer("تغییر وضعیت عضویت ناموفق بود.", show_alert=True)
        return
    if not changed:
        await callback.answer("عضویت پیدا نشد.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.class_member.status_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={"class_id": class_id, "user_id": user_id, "is_active": desired},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Class member status audit recording failed.")
    await callback.answer("وضعیت عضویت تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "وضعیت عضویت تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👥 بازگشت به اعضای کلاس",
                            callback_data=f"product:members:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:announcements:[0-9]+(?::[1-9][0-9]*)?$"))
async def product_class_announcements_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز مدیریت اطلاعیه‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    try:
        parts = (callback.data or "").split(":")
        class_id = int(parts[2])
        page = int(parts[3]) if len(parts) == 4 else 1
        if page < 1:
            raise ValueError
        async with product_session_factory() as product_db:
            class_info = await get_product_class(product_db, class_id=class_id)
            fetched = await list_product_class_announcements(
                product_db,
                class_id=class_id,
                limit=9,
                offset=(page - 1) * 8,
            )
    except (ValueError, ApplicationError) as exc:
        await callback.answer(str(exc) or "شماره صفحه معتبر نیست.", show_alert=True)
        return
    except Exception:
        logger.exception("Could not load ClassMate announcements.")
        await callback.answer("دریافت اطلاعیه‌ها ناموفق بود.", show_alert=True)
        return
    if class_info is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return

    has_next = len(fetched) > 8
    announcements = fetched[:8]
    lines = [
        f"📢 اطلاعیه‌های کلاس {class_info['name']}",
        f"صفحه: {page} · تعداد در این صفحه: {len(announcements)}",
        "",
    ]
    rows = []
    if announcements:
        for item in announcements:
            state_label = "فعال" if item["is_active"] else "غیرفعال"
            lines.append(f"• {item['title']} · {state_label}")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"غیرفعال‌سازی · {item['title'][:22]}"
                            if item["is_active"]
                            else f"فعال‌سازی · {item['title'][:22]}"
                        ),
                        callback_data=(
                            f"product:announcement:status:{class_id}:{item['id']}:"
                            f"{0 if item['is_active'] else 1}"
                        ),
                    )
                ]
            )
    else:
        lines.append(
            "هنوز اطلاعیه‌ای برای این کلاس ثبت نشده است."
            if page == 1
            else "اطلاعیه‌ای در این صفحه نیست؛ به صفحه قبل برگردید."
        )
    if has_next or page > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ صفحه قبل",
                    callback_data=f"product:announcements:{class_id}:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="صفحه بعد ➡️",
                    callback_data=f"product:announcements:{class_id}:{page + 1}",
                )
            )
        rows.append(navigation)
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ ایجاد اطلاعیه",
                    callback_data=f"product:announcement:add:{class_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به کلاس",
                    callback_data=f"product:class:{class_id}",
                )
            ],
        ]
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.regexp(r"^product:announcement:add:[0-9]+$"))
async def product_announcement_add_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز ایجاد اطلاعیه را ندارید.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    await state.clear()
    await _close_inline_panel(callback.message)
    await state.update_data(product_announcement_class_id=class_id)
    await state.set_state(AnnouncementCreation.title)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("عنوان اطلاعیه را وارد کنید (۲ تا ۱۸۰ نویسه):", reply_markup=shared_wizard_controls())


@router.message(AnnouncementCreation.title, F.text)
async def product_announcement_title_input(message: Message, state: FSMContext) -> None:
    title = " ".join((message.text or "").split())
    if not 2 <= len(title) <= 180:
        await message.answer("خطا: عنوان اطلاعیه باید بین ۲ تا ۱۸۰ نویسه باشد.\n\nعنوان را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    await state.update_data(product_announcement_title=title)
    await state.set_state(AnnouncementCreation.body)
    await message.answer("متن کامل اطلاعیه را وارد کنید (۲ تا ۸۰۰۰ نویسه):", reply_markup=shared_wizard_controls())


@router.message(AnnouncementCreation.body, F.text)
async def product_announcement_body_input(message: Message, state: FSMContext) -> None:
    body = (message.text or "").strip()
    if not 2 <= len(body) <= 8000:
        await message.answer("خطا: متن اطلاعیه باید بین ۲ تا ۸۰۰۰ نویسه باشد.\n\nمتن را دوباره وارد کنید:", reply_markup=shared_wizard_controls())
        return
    data = await state.get_data()
    token = secrets.token_urlsafe(8)
    await state.update_data(product_announcement_body=body, product_announcement_token=token)
    await state.set_state(AnnouncementCreation.confirm)
    await message.answer(
        "پیش‌نمایش اطلاعیه\n\n"
        f"عنوان: {data['product_announcement_title']}\n\n"
        f"{body[:1200]}{'…' if len(body) > 1200 else ''}\n\n"
        "انتشار انجام شود؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ انتشار اطلاعیه",
                        callback_data=f"product:announcement:confirm:{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ لغو",
                        callback_data=f"product:announcement:cancel:{token}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("product:announcement:confirm:"))
async def product_announcement_create_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    state: FSMContext,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != AnnouncementCreation.confirm.state
        or not token
        or token != data.get("product_announcement_token")
    ):
        await callback.answer("این تأیید منقضی شده است.", show_alert=True)
        return
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await state.clear()
        await callback.answer("مجوز ایجاد اطلاعیه را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await state.clear()
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return

    class_id = int(data.get("product_announcement_class_id") or 0)
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                announcement = await create_product_announcement(
                    product_db,
                    class_id=class_id,
                    telegram_id=principal.telegram_account.telegram_user_id,
                    author_name=principal.user.display_name,
                    title=data["product_announcement_title"],
                    body=data["product_announcement_body"],
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not publish ClassMate announcement.")
        await state.clear()
        await callback.answer("انتشار اطلاعیه ناموفق بود.", show_alert=True)
        return

    try:
        await record_event(
            db,
            event_type="product.announcement.created",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "announcement_id": announcement["id"],
                "title": announcement["title"],
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Announcement audit recording failed.")

    await state.clear()
    await callback.answer("اطلاعیه منتشر شد.")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ اطلاعیه منتشر شد.\nعنوان: {announcement['title']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📢 بازگشت به اطلاعیه‌ها",
                            callback_data=f"product:announcements:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:announcement:status:[0-9]+:[0-9]+:[01]$"))
async def product_announcement_status_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز مدیریت اطلاعیه‌ها را ندارید.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, announcement_id, desired = int(parts[3]), int(parts[4]), parts[5] == "1"
    action = "فعال‌سازی" if desired else "غیرفعال‌سازی"
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            f"آیا می‌خواهید این اطلاعیه را {action} کنید؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ تأیید {action}",
                            callback_data=(
                                f"product:announcement:status:confirm:{class_id}:"
                                f"{announcement_id}:{1 if desired else 0}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ لغو",
                            callback_data=f"product:announcements:{class_id}",
                        )
                    ],
                ]
            ),
        )


@router.callback_query(F.data.regexp(r"^product:announcement:status:confirm:[0-9]+:[0-9]+:[01]$"))
async def product_announcement_status_confirm_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if not await _has_permission(db, principal, "platform:announcements:manage"):
        await callback.answer("مجوز مدیریت اطلاعیه‌ها را ندارید.", show_alert=True)
        return
    if product_session_factory is None:
        await callback.answer("اتصال دیتابیس ClassMate فعال نیست.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    class_id, announcement_id, desired = int(parts[4]), int(parts[5]), parts[6] == "1"
    try:
        async with product_session_factory() as product_db:
            async with product_db.begin():
                changed = await set_product_announcement_active(
                    product_db,
                    class_id=class_id,
                    announcement_id=announcement_id,
                    is_active=desired,
                )
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not update ClassMate announcement status.")
        await callback.answer("تغییر وضعیت اطلاعیه ناموفق بود.", show_alert=True)
        return
    if not changed:
        await callback.answer("اطلاعیه پیدا نشد.", show_alert=True)
        return
    try:
        await record_event(
            db,
            event_type="product.announcement.status_changed",
            actor_id=principal.user.id,
            aggregate_type="class",
            event_data={
                "class_id": class_id,
                "announcement_id": announcement_id,
                "is_active": desired,
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Announcement status audit recording failed.")
    await callback.answer("وضعیت اطلاعیه تغییر کرد.")
    if callback.message is not None:
        await callback.message.edit_text(
            "وضعیت اطلاعیه تغییر کرد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📢 بازگشت به اطلاعیه‌ها",
                            callback_data=f"product:announcements:{class_id}",
                        )
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("product:member:cancel:"))
async def product_member_add_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != MemberCreation.confirm.state
        or not token
        or token != data.get("product_member_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("افزودن عضو لغو شد؛ تغییری ثبت نشد.")


@router.callback_query(F.data.startswith("product:announcement:cancel:"))
async def product_announcement_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    token = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if (
        await state.get_state() != AnnouncementCreation.confirm.state
        or not token
        or token != data.get("product_announcement_token")
    ):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_text("ایجاد اطلاعیه لغو شد؛ تغییری ثبت نشد.")
