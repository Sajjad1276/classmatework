from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis

_RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class TelegramRateLimitMiddleware(BaseMiddleware):
    """Apply per-sender message and callback limits using an atomic Redis counter."""

    def __init__(
        self,
        redis: Redis,
        *,
        message_limit: int = 30,
        callback_limit: int = 45,
        window_seconds: int = 60,
    ) -> None:
        if min(message_limit, callback_limit, window_seconds) <= 0:
            raise ValueError("Telegram rate-limit settings must be positive.")
        self.redis = redis
        self.message_limit = message_limit
        self.callback_limit = callback_limit
        self.window_seconds = window_seconds

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        sender = event.from_user
        if sender is None:
            await self._reject(event, "شناسه فرستنده معتبر نیست.")
            return None

        if isinstance(event, CallbackQuery):
            action = "callback"
            limit = self.callback_limit
        else:
            action = "message"
            limit = self.message_limit

        key = f"classmatework:bot:rate:{sender.id}:{action}"
        try:
            count = int(
                await self.redis.eval(
                    _RATE_LIMIT_SCRIPT,
                    1,
                    key,
                    self.window_seconds,
                )
            )
        except Exception:
            await self._reject(
                event,
                "سرویس کنترل درخواست موقتاً در دسترس نیست؛ عملیات انجام نشد.",
            )
            return None

        if count > limit:
            await self._reject(
                event,
                "تعداد درخواست‌ها بیش از حد مجاز است. کمی بعد دوباره تلاش کنید.",
            )
            return None

        return await handler(event, data)

    @staticmethod
    async def _reject(event: Message | CallbackQuery, message: str) -> None:
        if isinstance(event, Message):
            await event.answer(message)
        else:
            await event.answer(message, show_alert=True)
