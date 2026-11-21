from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import suppress
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.academic_router import router as academic_router
from app.bot.idempotency import TelegramUpdateDedupMiddleware
from app.bot.middleware import (
    DatabaseSessionMiddleware,
    PrivateChatOnlyMiddleware,
    TelegramIdentityMiddleware,
)
from app.bot.rate_limit import TelegramRateLimitMiddleware
from app.bot.router import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.runtime import create_database_engine, create_redis_client, create_session_factory

logger = logging.getLogger(__name__)

_LOCK_KEY = "classmatework:admin-bot:poller-lock"
_LOCK_TTL_SECONDS = 45
_LOCK_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_LOCK_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def _renew_poller_lock(redis: Redis, token: str) -> None:
    while True:
        await asyncio.sleep(15)
        renewed = await redis.eval(
            _LOCK_RENEW_SCRIPT,
            1,
            _LOCK_KEY,
            token,
            _LOCK_TTL_SECONDS,
        )
        if not renewed:
            raise RuntimeError("Admin bot poller lock was lost; stopping polling.")


async def _run_polling(dispatcher: Dispatcher, bot: Bot) -> None:
    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types(),
        handle_signals=True,
        close_bot_session=False,
    )


async def main() -> None:
    settings = get_settings()
    if (
        settings.telegram_bot_token is None
        or not settings.telegram_bot_token.get_secret_value().strip()
    ):
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required to run the admin bot.")

    configure_logging(settings.log_level)
    engine = create_database_engine(settings.database_url)
    session_factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
    redis = create_redis_client(settings.redis_url)
    token = secrets.token_urlsafe(32)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    storage = RedisStorage.from_url(
        settings.redis_url,
        state_ttl=timedelta(seconds=settings.bot_fsm_ttl_seconds),
        data_ttl=timedelta(seconds=settings.bot_fsm_ttl_seconds),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher["redis"] = redis
    dispatcher.update.outer_middleware(
        TelegramUpdateDedupMiddleware(
                session_factory,
                lease_ttl=timedelta(seconds=settings.bot_update_lease_seconds),
            )
    )

    try:
        await redis.ping()
        acquired = await redis.set(
            _LOCK_KEY,
            token,
            nx=True,
            ex=_LOCK_TTL_SECONDS,
        )
        if not acquired:
            raise RuntimeError(
                "Another admin bot poller is already active for this Telegram bot token."
            )

        for observer in (dispatcher.message, dispatcher.callback_query):
            observer.outer_middleware(PrivateChatOnlyMiddleware())
            observer.outer_middleware(TelegramRateLimitMiddleware(
                redis,
                message_limit=settings.bot_message_rate_limit,
                callback_limit=settings.bot_callback_rate_limit,
                window_seconds=settings.bot_rate_limit_window_seconds,
            ))
            observer.outer_middleware(DatabaseSessionMiddleware(session_factory))
            observer.outer_middleware(TelegramIdentityMiddleware())

        dispatcher.include_router(academic_router)
        dispatcher.include_router(router)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="شروع و بررسی دسترسی"),
                BotCommand(command="menu", description="منوی اصلی"),
                BotCommand(command="back", description="بازگشت یک مرحله"),
                BotCommand(command="cancel", description="لغو عملیات جاری"),
                BotCommand(command="roles", description="فهرست نقش‌های قابل واگذاری"),
                BotCommand(command="role-create", description="ساخت نقش در مجموعه"),
                BotCommand(command="members", description="فهرست اعضای مجموعه"),
                BotCommand(command="member-suspend", description="تعلیق عضویت پس از تأیید"),
                BotCommand(command="invite", description="دعوت مدیر با نقش مشخص"),
                BotCommand(command="invites", description="فهرست دعوت‌های باز"),
                BotCommand(command="revoke-invite", description="لغو دعوت پذیرفته‌نشده"),
                BotCommand(command="help", description="راهنمای ربات"),
                BotCommand(command="status", description="وضعیت سرویس‌ها"),
            ]
        )
        await bot.delete_webhook(drop_pending_updates=False)
        me = await bot.get_me()
        logger.info("ClassMate Work admin bot starting; bot_id=%s", me.id)

        polling_task = asyncio.create_task(_run_polling(dispatcher, bot))
        heartbeat_task = asyncio.create_task(_renew_poller_lock(redis, token))
        done, _ = await asyncio.wait(
            {polling_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if heartbeat_task in done:
            await heartbeat_task
        await polling_task
    finally:
        for task in (locals().get("polling_task"), locals().get("heartbeat_task")):
            if task is not None and not task.done():
                task.cancel()
        pending_tasks = [
            task
            for task in (locals().get("polling_task"), locals().get("heartbeat_task"))
            if task is not None
        ]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        with suppress(Exception):
            await redis.eval(_LOCK_RELEASE_SCRIPT, 1, _LOCK_KEY, token)
        await storage.close()
        await redis.aclose()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
