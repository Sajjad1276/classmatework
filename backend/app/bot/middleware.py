from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import TelegramAccount, User
from app.modules.identity.principal import TelegramPrincipal


def is_private_chat(chat_type: str | ChatType | None) -> bool:
    return chat_type == ChatType.PRIVATE or chat_type == ChatType.PRIVATE.value


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Reject every administrative update that did not originate in a private chat."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            chat_type = event.chat.type
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat_type = event.message.chat.type
        else:
            chat_type = None

        if not is_private_chat(chat_type):
            if isinstance(event, Message):
                await event.answer("این ربات فقط در گفت‌وگوی خصوصی قابل استفاده است.")
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "مدیریت فقط در گفت‌وگوی خصوصی مجاز است.",
                    show_alert=True,
                )
            return None

        return await handler(event, data)


class DatabaseSessionMiddleware(BaseMiddleware):
    """Provide one short-lived AsyncSession per Telegram update."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["db"] = session
            return await handler(event, data)


class TelegramIdentityMiddleware(BaseMiddleware):
    """Resolve the sender by numeric Telegram ID; usernames are never credentials."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        sender = event.from_user
        if sender is None:
            await self._reject(event)
            return None

        db: AsyncSession = data["db"]
        result = await db.execute(
            select(TelegramAccount, User)
            .join(User, User.id == TelegramAccount.user_id)
            .where(
                TelegramAccount.telegram_user_id == sender.id,
                TelegramAccount.status == "active",
                User.status == "active",
            )
        )
        row = result.one_or_none()
        if row is None:
            await self._reject(event)
            return None

        account, user = row
        data["principal"] = TelegramPrincipal(user=user, telegram_account=account)
        return await handler(event, data)

    @staticmethod
    async def _reject(event: Message | CallbackQuery) -> None:
        if isinstance(event, Message):
            await event.answer(
                "دسترسی مدیریتی برای این حساب فعال نیست. "
                "برای دریافت دسترسی باید از مالک سامانه دعوت‌نامه بگیرید."
            )
        else:
            await event.answer(
                "دسترسی این حساب فعال نیست.",
                show_alert=True,
            )
