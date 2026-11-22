import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.bot.rate_limit import _RATE_LIMIT_SCRIPT


@pytest.mark.asyncio
async def test_redis_runtime_and_atomic_rate_limit_counter() -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured.")

    client = Redis.from_url(redis_url, decode_responses=True)
    key = f"classmatework:test:rate:{uuid4().hex}"
    try:
        assert await client.ping()
        first = await client.eval(_RATE_LIMIT_SCRIPT, 1, key, 60)
        second = await client.eval(_RATE_LIMIT_SCRIPT, 1, key, 60)
        assert first == 1
        assert second == 2
        assert await client.ttl(key) > 0
    finally:
        await client.delete(key)
        await client.aclose()
