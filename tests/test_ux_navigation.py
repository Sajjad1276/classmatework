from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.ux import add_wizard_cancel, ensure_back_button, wizard_controls


def test_inline_screen_gets_a_back_button_when_missing() -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ادامه", callback_data="next:step")]
        ]
    )

    result = ensure_back_button(keyboard)

    assert result.inline_keyboard[-1][0].text == "⬅️ بازگشت"
    assert result.inline_keyboard[-1][0].callback_data == "root:menu"


def test_existing_back_button_is_not_duplicated() -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ بازگشت به فهرست", callback_data="directory")]
        ]
    )

    result = ensure_back_button(keyboard)

    assert result is keyboard
    assert len(result.inline_keyboard) == 1


def test_wizard_controls_include_separate_back_and_cancel_actions() -> None:
    keyboard = wizard_controls()
    buttons = [
        button
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert any("بازگشت" in button.text and button.callback_data == "root:menu" for button in buttons)
    assert any("لغو" in button.text and button.callback_data == "ux:cancel" for button in buttons)


def test_add_wizard_cancel_preserves_existing_actions_and_adds_escape_paths() -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="شنبه", callback_data="weekday:0")]
        ]
    )

    result = add_wizard_cancel(keyboard)
    callbacks = {
        button.callback_data
        for row in result.inline_keyboard
        for button in row
    }

    assert "weekday:0" in callbacks
    assert "ux:cancel" in callbacks
    assert "root:menu" in callbacks
