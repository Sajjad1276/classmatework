from __future__ import annotations

import base64
import binascii
import logging
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram.types import (
    InlineKeyboardMarkup as TelegramInlineKeyboardMarkup,
)

from app.application.control_center import (
    get_class_subject_catalog,
    get_platform_report,
    set_class_subject_assignment,
)
from app.application.errors import ApplicationError
from app.bot.ux import ensure_back_button
from app.modules.access.policy import has_permission
from app.modules.identity.principal import TelegramPrincipal

router = Router(name="control-center")

def InlineKeyboardMarkup(*args, **kwargs) -> TelegramInlineKeyboardMarkup:
    """Create an inline keyboard with a safe back path by default."""
    return ensure_back_button(TelegramInlineKeyboardMarkup(*args, **kwargs))

logger = logging.getLogger(__name__)
TEHRAN = ZoneInfo("Asia/Tehran")


def _report_text(report: dict) -> str:
    generated = report["generated_at"].astimezone(TEHRAN)
    return (
        "╔  🤖 آمار سامانه ClassMate\n"
        "║  منبع: دیتابیس زنده ربات اصلی ClassMate\n"
        "║\n"
        f"╣  👥 کل حساب‌های ثبت‌شده: {report['total_users']:,}\n"
        f"╣  🚀 شروع‌کنندگان ربات در چت خصوصی: {report['started_users']:,}\n"
        f"╣  ✅ حساب‌های فعال: {report['active_users']:,}\n"
        f"╣  🟢 شروع‌کنندگانِ دارای حساب فعال: {report['active_started_users']:,}\n"
        "║\n"
        f"╣  🎓 دانش‌آموزان دارای عضویت فعال: {report['active_students']:,}\n"
        f"╣  🔗 کل عضویت‌های فعال در کلاس‌ها: {report['active_memberships']:,}\n"
        "║\n"
        "╣  🆕 حساب‌های ثبت‌شده در روز / هفته / ماه گذشته:\n"
        f"╣  {report['new_users_1d']:,} / {report['new_users_7d']:,} / {report['new_users_30d']:,}\n"
        "║\n"
        "╣  📡 کاربرانِ دارای تعامل خصوصی ثبت‌شده در یک / چهار / دوازده / بیست‌وچهار ساعت گذشته:\n"
        f"╣  {report['active_users_1h']:,} / {report['active_users_4h']:,} / "
        f"{report['active_users_12h']:,} / {report['active_users_24h']:,}\n"
        "║  توجه: ثبت آخرین فعالیت خصوصی از انتشار نسخهٔ جدید شروع شده است؛ سابقهٔ قبل از آن موجود نیست.\n"
        "║\n"
        f"╣  🏫 مدارس: {report['schools']:,} (فعال: {report['active_schools']:,})\n"
        f"╣  👥 کلاس‌ها: {report['classes']:,} (فعال: {report['active_classes']:,})\n"
        f"╣  📚 درس‌ها: {report['subjects']:,}\n"
        f"╣  🔗 گروه‌های متصل به کلاس: {report['connected_groups']:,}\n"
        f"╣  🧾 رخدادهای تحلیلی ثبت‌شده در Work طی ۲۴ ساعت: {report['events_24h']:,}\n"
        "║\n"
        f"╚  🗓 {generated:%Y-%m-%d}   ⏰ {generated:%H:%M}"
    )


async def _load_platform_report(
    db: AsyncSession,
    principal: TelegramPrincipal,
    product_session_factory: async_sessionmaker[AsyncSession] | None,
) -> dict:
    if product_session_factory is None:
        raise ApplicationError("اتصال دیتابیس اصلی ClassMate فعال نیست؛ آمار زنده قابل دریافت نیست.")
    async with product_session_factory() as product_db:
        return await get_platform_report(
            db,
            actor_id=principal.user.id,
            product_db=product_db,
        )


async def _show_reports(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None,
) -> None:
    try:
        report = await _load_platform_report(db, principal, product_session_factory)
    except ApplicationError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        logger.exception("Could not build the live ClassMate platform report.")
        await message.answer(
            "دریافت آمار از دیتابیس اصلی ClassMate ناموفق بود. "
            "هیچ عدد جایگزین یا تخمینی نمایش داده نشد."
        )
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


@router.message(F.text == "📊 داشبورد و گزارش‌ها")
async def reports_menu_button(
    message: Message,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await _hide_root_keyboard(message)
    await _show_reports(message, principal, db, product_session_factory)


@router.callback_query(F.data == "control:reports:refresh")
async def reports_refresh_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
    product_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    try:
        report = await _load_platform_report(db, principal, product_session_factory)
    except ApplicationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Could not refresh the live ClassMate platform report.")
        await callback.answer(
            "دریافت آمار از دیتابیس اصلی ClassMate ناموفق بود.",
            show_alert=True,
        )
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


def _encode_subject_callback_id(subject_id: UUID) -> str:
    return base64.urlsafe_b64encode(subject_id.bytes).decode("ascii").rstrip("=")


def _decode_subject_callback_id(value: str) -> UUID:
    if len(value) != 22:
        raise ValueError("شناسه درس معتبر نیست.")
    try:
        raw = base64.urlsafe_b64decode(value + "==")
    except (ValueError, binascii.Error) as exc:
        raise ValueError("شناسه درس معتبر نیست.") from exc
    if len(raw) != 16:
        raise ValueError("شناسه درس معتبر نیست.")
    return UUID(bytes=raw)


def _class_callback_id(class_group_id: UUID) -> str:
    return class_group_id.hex


async def _render_class_subject_panel(
    callback: CallbackQuery,
    *,
    principal: TelegramPrincipal,
    db: AsyncSession,
    class_group_id: UUID,
    page: int = 1,
) -> None:
    catalog = await get_class_subject_catalog(
        db,
        actor_id=principal.user.id,
        class_group_id=class_group_id,
    )
    class_group = catalog["class_group"]
    can_manage = await has_permission(
        db,
        user_id=principal.user.id,
        permission_key="class:subjects:manage",
        tenant_id=class_group.tenant_id,
        scope_type="class",
        scope_id=class_group.id,
    )
    items = catalog["items"]
    page_size = 8
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(max(page, 1), pages)
    start = (page - 1) * page_size
    visible = items[start : start + page_size]
    class_ref = _class_callback_id(class_group.id)

    lines = [
        f"╔  📚 درس‌های کلاس {class_group.name}",
        "║",
        f"╣  🏫 پایه: {class_group.grade}",
        f"╣  📖 کل درس‌های مدرسه: {catalog['subject_count']:,}",
        f"╣  ✅ درس‌های تخصیص‌یافته: {catalog['assigned_count']:,}",
        f"╣  ➕ درس‌های قابل تخصیص: {catalog['subject_count'] - catalog['assigned_count']:,}",
        "║",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(visible, start=1):
        subject = item["subject"]
        assigned = item["assigned"]
        status_label = "تخصیص‌یافته" if assigned else "تخصیص‌نیافته"
        lines.append(f"╣  {start + index:02d}. {subject.name[:72]} · {status_label}")
        if can_manage:
            subject_ref = _encode_subject_callback_id(subject.id)
            action = "🗑 برداشتن" if assigned else "➕ افزودن"
            action_key = "d" if assigned else "a"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{action} · {subject.name[:35]}",
                        callback_data=f"cs:{action_key}:{class_ref}:{subject_ref}",
                    )
                ]
            )

    if not items:
        lines.append("╣  هنوز درسی برای این مدرسه ثبت نشده است.")
    elif not can_manage:
        lines.append("║")
        lines.append("╣  این صفحه فقط‌خواندنی است؛ مجوز تخصیص درس ندارید.")

    lines.extend(["║", f"╚  صفحهٔ {page} از {pages}"])
    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"cs:p:{class_ref}:{page - 1}",
            )
        )
    if page < pages:
        navigation.append(
            InlineKeyboardButton(
                text="صفحه بعد ➡️",
                callback_data=f"cs:p:{class_ref}:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ بازگشت به کلاس",
                callback_data="product:schools",
            )
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("cs:"))
async def class_subject_management_callback(
    callback: CallbackQuery,
    principal: TelegramPrincipal,
    db: AsyncSession,
) -> None:
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    try:
        if action == "o" and len(parts) == 3:
            class_group_id = UUID(hex=parts[2])
            page = 1
        elif action == "p" and len(parts) == 4:
            class_group_id = UUID(hex=parts[2])
            page = int(parts[3])
            if page < 1:
                raise ValueError
        elif action in {"a", "c", "d", "r"} and len(parts) == 4:
            class_group_id = UUID(hex=parts[2])
            subject_id = _decode_subject_callback_id(parts[3])
            page = 1
        else:
            raise ValueError

        if action == "a":
            catalog = await get_class_subject_catalog(
                db,
                actor_id=principal.user.id,
                class_group_id=class_group_id,
            )
            target = next(
                (
                    item for item in catalog["items"]
                    if item["subject"].id == subject_id
                ),
                None,
            )
            if target is None:
                raise ApplicationError("درس موردنظر در فهرست این مدرسه پیدا نشد.")
            if target["assigned"]:
                await callback.answer("این درس از قبل به کلاس تخصیص دارد.")
                await _render_class_subject_panel(
                    callback,
                    principal=principal,
                    db=db,
                    class_group_id=class_group_id,
                    page=page,
                )
                return
            class_ref = _class_callback_id(class_group_id)
            subject_ref = _encode_subject_callback_id(subject_id)
            await callback.answer()
            if callback.message is not None:
                await callback.message.edit_text(
                    "پیش‌نمایش تخصیص درس به کلاس\n\n"
                    f"کلاس: {catalog['class_group'].name}\n"
                    f"درس: {target['subject'].name}\n\n"
                    "با تأیید، این درس در فهرست درس‌های قابل استفادهٔ کلاس قرار می‌گیرد. "
                    "هیچ تغییری تا انتخاب «تأیید تخصیص» ثبت نمی‌شود.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✅ تأیید تخصیص درس",
                                    callback_data=f"cs:c:{class_ref}:{subject_ref}",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="❌ لغو",
                                    callback_data=f"cs:p:{class_ref}:{page}",
                                )
                            ],
                        ]
                    ),
                )
            return
        elif action == "d":
            await callback.answer()
            if callback.message is not None:
                class_ref = _class_callback_id(class_group_id)
                subject_ref = _encode_subject_callback_id(subject_id)
                await callback.message.edit_text(
                    "پیش‌نمایش برداشتن درس از کلاس\n\n"
                    "با تأیید، فقط رابطهٔ درس و کلاس غیرفعال می‌شود؛ خود درس حذف نمی‌شود.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="تأیید برداشتن درس",
                                    callback_data=f"cs:r:{class_ref}:{subject_ref}",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="لغو",
                                    callback_data=f"cs:p:{class_ref}:1",
                                )
                            ],
                        ]
                    ),
                )
            return
        elif action == "c":
            changed = await set_class_subject_assignment(
                db,
                actor_id=principal.user.id,
                class_group_id=class_group_id,
                subject_id=subject_id,
                assigned=True,
            )
            await callback.answer(
                "درس به کلاس تخصیص یافت." if changed else "این درس از قبل در کلاس بود."
            )
        elif action == "r":
            changed = await set_class_subject_assignment(
                db,
                actor_id=principal.user.id,
                class_group_id=class_group_id,
                subject_id=subject_id,
                assigned=False,
            )
            await callback.answer(
                "درس از کلاس برداشته شد." if changed else "این درس قبلاً تخصیص نداشت."
            )
        else:
            await callback.answer()
    except (ValueError, TypeError, ApplicationError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ApplicationError)
            else "شناسه یا مسیر صفحه معتبر نیست؛ دوباره وارد بخش درس‌های کلاس شوید."
        )
        await callback.answer(message, show_alert=True)
        return

    await _render_class_subject_panel(
        callback,
        principal=principal,
        db=db,
        class_group_id=class_group_id,
        page=page,
    )
