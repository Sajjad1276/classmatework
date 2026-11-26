from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def ensure_back_button(
    keyboard: InlineKeyboardMarkup,
    *,
    callback_data: str = "root:menu",
    label: str = "⬅️ بازگشت",
) -> InlineKeyboardMarkup:
    """Guarantee an escape/back action on every inline screen."""
    rows = [list(row) for row in keyboard.inline_keyboard]
    has_back = any(
        "بازگشت" in (button.text or "")
        or button.callback_data == "root:menu"
        or ":back:" in (button.callback_data or "")
        for row in rows
        for button in row
    )
    if has_back:
        return keyboard
    rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wizard_controls(
    *,
    back_callback: str = "root:menu",
    cancel_callback: str = "ux:cancel",
    back_label: str = "⬅️ بازگشت",
) -> InlineKeyboardMarkup:
    """Shared navigation for input steps; Cancel never submits a mutation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=back_label, callback_data=back_callback)],
            [InlineKeyboardButton(text="❌ لغو عملیات", callback_data=cancel_callback)],
        ]
    )


def add_wizard_cancel(
    keyboard: InlineKeyboardMarkup,
    *,
    cancel_callback: str = "ux:cancel",
) -> InlineKeyboardMarkup:
    rows = [list(row) for row in keyboard.inline_keyboard]
    if not any(
        button.callback_data == cancel_callback
        for row in rows
        for button in row
    ):
        rows.append(
            [InlineKeyboardButton(text="❌ لغو عملیات", callback_data=cancel_callback)]
        )
    return ensure_back_button(InlineKeyboardMarkup(inline_keyboard=rows))
