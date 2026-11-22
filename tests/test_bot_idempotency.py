from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.idempotency import TelegramUpdateDedupMiddleware
from app.core.request_context import request_id_context
from app.db.base import Base
from app.db.models import ProcessedTelegramUpdate


async def _factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_duplicate_telegram_update_is_processed_only_once() -> None:
    engine, factory = await _factory()
    middleware = TelegramUpdateDedupMiddleware(factory)
    event = SimpleNamespace(update_id=1001)
    calls = 0
    correlation_ids: list[str | None] = []

    async def handler(_event, _data):
        nonlocal calls
        calls += 1
        correlation_ids.append(request_id_context.get())
        return "handled"

    first = await middleware(handler, event, {})
    duplicate = await middleware(handler, event, {})

    assert first == "handled"
    assert duplicate is None
    assert calls == 1
    assert correlation_ids == ["tg-1001"]

    async with factory() as db:
        record = await db.get(ProcessedTelegramUpdate, 1001)
        assert record is not None
        assert record.status == "completed"
        assert record.completed_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_update_releases_lease_for_retry() -> None:
    engine, factory = await _factory()
    middleware = TelegramUpdateDedupMiddleware(factory)
    event = SimpleNamespace(update_id=1002)
    calls = 0

    async def fail_once(_event, _data):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return "retried"

    with pytest.raises(RuntimeError, match="temporary failure"):
        await middleware(fail_once, event, {})

    result = await middleware(fail_once, event, {})
    assert result == "retried"
    assert calls == 2

    async with factory() as db:
        record = await db.get(ProcessedTelegramUpdate, 1002)
        assert record is not None
        assert record.status == "completed"
        assert record.last_error is None

    await engine.dispose()
