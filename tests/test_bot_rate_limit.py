from datetime import UTC, datetime

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User

from app.bot.rate_limit import TelegramRateLimitMiddleware


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(self, _script, _num_keys, key, _window):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class BrokenRedis:
    async def eval(self, *_args):
        raise ConnectionError("redis unavailable")


def _message(user_id: int = 1234) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type=ChatType.PRIVATE),
        from_user=User(id=user_id, is_bot=False, first_name="Admin"),
        text="/menu",
    )


def _callback(user_id: int = 1234) -> CallbackQuery:
    message = _message(user_id)
    return CallbackQuery(
        id="callback-id",
        from_user=message.from_user,
        chat_instance="private-chat",
        message=message,
        data="account:me",
    )


@pytest.mark.asyncio
async def test_rate_limit_allows_requests_up_to_configured_limit() -> None:
    redis = FakeRedis()
    middleware = TelegramRateLimitMiddleware(
        redis, message_limit=2, callback_limit=2, window_seconds=60
    )
    calls = 0

    async def handler(_event, _data):
        nonlocal calls
        calls += 1
        return "ok"

    assert await middleware(handler, _message(), {}) == "ok"
    assert await middleware(handler, _message(), {}) == "ok"
    assert await middleware(handler, _message(), {}) is None
    assert calls == 2


@pytest.mark.asyncio
async def test_rate_limit_is_fail_closed_when_redis_is_unavailable() -> None:
    middleware = TelegramRateLimitMiddleware(BrokenRedis())
    calls = 0

    async def handler(_event, _data):
        nonlocal calls
        calls += 1

    result = await middleware(handler, _callback(), {})
    assert result is None
    assert calls == 0
