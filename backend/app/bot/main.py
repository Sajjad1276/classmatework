from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import suppress
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.control_router import router as control_router
from app.bot.idempotency import TelegramUpdateDedupMiddleware
from app.bot.middleware import (
    DatabaseSessionMiddleware,
    PrivateChatOnlyMiddleware,
    TelegramIdentityMiddleware,
)
from app.bot.product_router import router as product_router
from app.bot.rate_limit import TelegramRateLimitMiddleware
from app.bot.router import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.runtime import (
    DATABASE_SCHEMA,
    create_database_engine,
    create_product_database_engine,
    create_redis_client,
    create_session_factory,
)

logger = logging.getLogger(__name__)

_LOCK_KEY = "classmatework:admin-bot:poller-lock"
_LOCK_TTL_SECONDS = 45
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 90
_LOCK_RETRY_INTERVAL_SECONDS = 1
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


async def _acquire_poller_lock(redis: Redis, token: str) -> None:
    """Wait for the previous Railway replica to release its polling lease.

    During a rolling deploy the old replica may still own the lock when the
    replacement starts. Failing immediately would turn a healthy handover
    into a crash loop. The bounded wait is longer than the lease TTL so that
    an abruptly terminated owner can also expire safely.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
    next_notice = loop.time()

    while True:
        acquired = await redis.set(
            _LOCK_KEY,
            token,
            nx=True,
            ex=_LOCK_TTL_SECONDS,
        )
        if acquired:
            logger.info("Admin bot poller lock acquired.")
            return

        now = loop.time()
        if now >= deadline:
            raise RuntimeError(
                "Timed out waiting for the previous admin bot poller to release its lock."
            )

        if now >= next_notice:
            logger.info("Previous admin bot poller still holds the lock; waiting for handover.")
            next_notice = now + 10

        await asyncio.sleep(_LOCK_RETRY_INTERVAL_SECONDS)


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
    product_engine = None
    product_session_factory = None
    if settings.c2_db_url is not None and settings.c2_db_url.get_secret_value().strip():
        product_engine = create_product_database_engine(
            settings.c2_db_url.get_secret_value().strip()
        )
        product_session_factory = create_session_factory(product_engine)
        try:
            async with product_engine.connect() as product_connection:
                await product_connection.execute(
                    text(
                        "ALTER TABLE public.classes "
                        "ADD COLUMN IF NOT EXISTS member_join_code VARCHAR(64)"
                    )
                )
                await product_connection.commit()
                product_state = (
                    await product_connection.execute(
                        text(
                            "SELECT current_database() AS database, "
                            "current_schema() AS schema, "
                            "to_regclass('public.schools')::text AS schools_table, "
                            "to_regclass('public.classes')::text AS classes_table, "
                            "to_regclass('public.academic_years')::text AS years_table, "
                            "to_regclass('public.subjects')::text AS subjects_table, "
                            "to_regclass('public.timetable_entries')::text AS timetable_table"
                        )
                    )
                ).mappings().one()
                required_tables = (
                    "schools_table",
                    "classes_table",
                    "years_table",
                    "subjects_table",
                    "timetable_table",
                )
                missing_tables = [
                    name for name in required_tables if product_state[name] is None
                ]
                if missing_tables:
                    raise RuntimeError(
                        "ClassMate product database is missing required tables: "
                        + ", ".join(missing_tables)
                    )
            logger.info(
                "ClassMate product database connected; database=%s schema=%s",
                product_state["database"],
                product_state["schema"],
            )
        except Exception:
            logger.exception(
                "ClassMate product database is unavailable; product management is disabled."
            )
            await product_engine.dispose()
            product_engine = None
            product_session_factory = None

    redis = create_redis_client(settings.redis_url)
    token = secrets.token_urlsafe(32)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    storage = RedisStorage.from_url(
        settings.redis_url,
        key_builder=DefaultKeyBuilder(
            prefix="classmatework:fsm",
            with_bot_id=True,
        ),
        state_ttl=timedelta(seconds=settings.bot_fsm_ttl_seconds),
        data_ttl=timedelta(seconds=settings.bot_fsm_ttl_seconds),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher["redis"] = redis
    if product_session_factory is not None:
        # Used only by product-management use cases. Admin identities,
        # permissions, FSM and audit remain in the isolated Work database.
        dispatcher["product_session_factory"] = product_session_factory
    dispatcher.update.outer_middleware(
        TelegramUpdateDedupMiddleware(
            session_factory,
            lease_ttl=timedelta(seconds=settings.bot_update_lease_seconds),
        )
    )

    try:
        await redis.ping()
        async with engine.connect() as connection:
            diagnostic = await connection.execute(
                text(
                    "SELECT current_database() AS database, "
                    "current_user AS database_user, "
                    "current_schema() AS schema, "
                    "current_setting('search_path') AS search_path, "
                    "(SELECT array_agg(schema_name ORDER BY schema_name) "
                    " FROM information_schema.schemata "
                    " WHERE schema_name IN ('public', 'classmatework')) AS available_schemas, "
                    "CASE WHEN EXISTS (SELECT 1 FROM information_schema.schemata "
                    " WHERE schema_name = 'classmatework') "
                    "THEN has_schema_privilege(current_user, 'classmatework', 'USAGE') "
                    "ELSE FALSE END AS classmatework_schema_usage, "
                    "to_regclass('classmatework.processed_telegram_updates')::text AS processed_table, "
                    "to_regclass('public.processed_telegram_updates')::text AS public_processed_table, "
                    "to_regclass('classmatework.alembic_version')::text AS version_table, "
                    "to_regclass('public.alembic_version')::text AS public_version_table"
                )
            )
            database_state = diagnostic.mappings().one()
            if database_state["processed_table"] is None:
                raise RuntimeError(
                    "ClassMate Work migration check failed: "
                    f"database_state={dict(database_state)}"
                )
            if database_state["version_table"] is None:
                raise RuntimeError(
                    "ClassMate Work Alembic version table is missing: "
                    f"database_state={dict(database_state)}"
                )
            versions = (
                await connection.execute(
                    text(f"SELECT version_num FROM {DATABASE_SCHEMA}.alembic_version")
                )
            ).scalars().all()
        logger.info(
            "ClassMate Work database ready; database=%s schema=%s search_path=%s migrations=%s",
            database_state["database"],
            DATABASE_SCHEMA,
            database_state["search_path"],
            versions,
        )

        await _acquire_poller_lock(redis, token)

        for observer in (dispatcher.message, dispatcher.callback_query):
            observer.outer_middleware(PrivateChatOnlyMiddleware())
            observer.outer_middleware(
                TelegramRateLimitMiddleware(
                    redis,
                    message_limit=settings.bot_message_rate_limit,
                    callback_limit=settings.bot_callback_rate_limit,
                    window_seconds=settings.bot_rate_limit_window_seconds,
                )
            )
            observer.outer_middleware(DatabaseSessionMiddleware(session_factory))
            observer.outer_middleware(TelegramIdentityMiddleware())

        dispatcher.include_router(product_router)
        dispatcher.include_router(control_router)
        dispatcher.include_router(router)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="شروع و بررسی دسترسی"),
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
        if product_engine is not None:
            await product_engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
