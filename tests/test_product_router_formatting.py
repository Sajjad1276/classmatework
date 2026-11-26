from pathlib import Path

PRODUCT_ROUTER = Path(__file__).parents[1] / "backend/app/bot/product_router.py"


def test_html_product_messages_declare_html_parse_mode() -> None:
    source = PRODUCT_ROUTER.read_text(encoding="utf-8")

    # Telegram otherwise renders the <b>/<code> tags literally.
    assert source.count('parse_mode="HTML"') >= 3
    for marker in (
        '"🔑 <b>کد مشترک ورود به کلاس</b>',
        '"🔗 <b>کد اتصال گروه تلگرام</b>',
    ):
        message_start = source.index(marker)
        message_end = source.index("reply_markup=InlineKeyboardMarkup(", message_start)
        assert 'parse_mode="HTML"' in source[message_start:message_end]
