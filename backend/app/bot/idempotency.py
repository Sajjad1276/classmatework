from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import Update
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from app.core.request_context import request_id_context
from app.db.models import ProcessedTelegramUpdate


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TelegramUpdateDedupMiddleware(BaseMiddleware):
    """Use a durable DB lease to suppress duplicate Telegram Update deliveries.

    Failed handlers become retryable after the lease is released. Mutating use cases
    must still enforce domain idempotency because a process can crash after a commit
    but before this middleware records the Update as completed.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_ttl: timedelta = timedelta(minutes=3),
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("Telegram update lease TTL must be positive.")
        self.session_factory = session_factory
        self.lease_ttl = lease_ttl

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        update_id = getattr(event, "update_id", None)
        if not isinstance(update_id, int):
            return await handler(event, data)

        now = datetime.now(UTC)
        lease_expires_at = now + self.lease_ttl

        async with self.session_factory() as db:
            values = {
                "update_id": update_id,
                "status": "processing",
                "lease_expires_at": lease_expires_at,
                "created_at": now,
                "completed_at": None,
                "last_error": None,
            }
            dialect = db.bind.dialect.name if db.bind is not None else ""
            if dialect == "postgresql":
                statement = (
                    pg_insert(ProcessedTelegramUpdate)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[ProcessedTelegramUpdate.update_id]
                    )
                )
            elif dialect == "sqlite":
                statement = (
                    sqlite_insert(ProcessedTelegramUpdate)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[ProcessedTelegramUpdate.update_id]
                    )
                )
            else:
                raise RuntimeError(
                    f"Unsupported database dialect for Update deduplication: {dialect}"
                )

            inserted = await db.execute(statement)
            if inserted.rowcount == 0:
                existing = (
                    await db.execute(
                        select(ProcessedTelegramUpdate)
                        .where(ProcessedTelegramUpdate.update_id == update_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if existing is None:
                    await db.rollback()
                    return None
                if existing.status == "completed":
                    await db.commit()
                    return None
                if (
                    existing.status == "processing"
                    and _as_utc(existing.lease_expires_at) > now
                ):
                    await db.commit()
                    return None

                existing.status = "processing"
                existing.lease_expires_at = lease_expires_at
                existing.completed_at = None
                existing.last_error = None

            await db.commit()

        data["telegram_update_id"] = update_id
        correlation_token = request_id_context.set(f"tg-{update_id}")
        try:
            result = await handler(event, data)
        except Exception as exc:
            async with self.session_factory() as db:
                await db.execute(
                    update(ProcessedTelegramUpdate)
                    .where(ProcessedTelegramUpdate.update_id == update_id)
                    .values(
                        status="failed",
                        lease_expires_at=datetime.now(UTC),
                        last_error=type(exc).__name__[:160],
                    )
                )
                await db.commit()
            raise
        finally:
            request_id_context.reset(correlation_token)

        async with self.session_factory() as db:
            completed_at = datetime.now(UTC)
            await db.execute(
                update(ProcessedTelegramUpdate)
                .where(ProcessedTelegramUpdate.update_id == update_id)
                .values(
                    status="completed",
                    completed_at=completed_at,
                    lease_expires_at=completed_at,
                    last_error=None,
                )
            )
            await db.commit()
        return result
