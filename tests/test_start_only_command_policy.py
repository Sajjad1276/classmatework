from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import product_router
from app.bot.middleware import StartOnlyCommandMiddleware


@pytest.mark.asyncio
async def test_only_start_command_reaches_admin_bot_handlers():
    middleware = StartOnlyCommandMiddleware()
    handler = AsyncMock(return_value="handled")

    blocked = SimpleNamespace(
        text="/roles",
        caption=None,
        answer=AsyncMock(),
    )
    result = await middleware(handler, blocked, {})
    assert result is None
    blocked.answer.assert_awaited_once()
    handler.assert_not_awaited()

    allowed = SimpleNamespace(
        text="/start pairing_payload",
        caption=None,
        answer=AsyncMock(),
    )
    result = await middleware(handler, allowed, {})
    assert result == "handled"
    handler.assert_awaited_once()


def test_class_join_start_link_uses_the_student_bot_username(monkeypatch):
    monkeypatch.setattr(
        product_router,
        "get_settings",
        lambda: SimpleNamespace(classmate_bot_username="Classmatepro_bot"),
    )

    assert product_router._class_join_start_url("ABCD2345XY") == (
        "https://t.me/Classmatepro_bot?start=join_ABCD2345XY"
    )
