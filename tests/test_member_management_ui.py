from secrets import token_urlsafe
from uuid import uuid4

from app.bot.router import _member_suspend_callback_data


def test_member_suspend_callback_data_fits_telegram_limit() -> None:
    callback_data = _member_suspend_callback_data(token_urlsafe(6), uuid4())

    assert len(callback_data.encode("utf-8")) <= 64
