import os

import pytest
import pytest_asyncio

from tests.db_safety import validate_test_url

TEST_URL = validate_test_url(os.getenv("TEST_DATABASE_URL", "postgresql://neyro:neyro@localhost:5432/neyro_test"))
os.environ["DATABASE_URL"] = TEST_URL

TABLES = ("support_routes", "service_api_keys", "delivery_attempts", "posts", "ad_offers", "sources", "stats_daily", "channels", "promo_codes", "users", "kv")


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from app.core import scheduler, runtime, rate_limit
    monkeypatch.setattr(scheduler, 'ADMIN_IDS', set())
    scheduler._alert_times.clear()
    scheduler._source_cache.clear()
    rate_limit._buckets.clear()
    monkeypatch.setattr(runtime, 'wait_telegram', AsyncMock())
    for name, count in [('ai_slots',4),('source_slots',8),('delivery_slots',2),('fresh_slots',2)]:
        monkeypatch.setattr(runtime, name, asyncio.Semaphore(count))


@pytest_asyncio.fixture
async def store():
    """Empty database with the real schema. Every test starts from a clean slate."""
    from app import db

    db.DATABASE_URL = TEST_URL
    await db.close()
    pool = await db.connect()
    async with pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    try:
        yield db
    finally:
        await db.close()


@pytest_asyncio.fixture
async def owner(store):
    await store.execute(
        "INSERT INTO users (tg_id, daily_limit, max_channels, access_until, created_at) VALUES (1, 3, 5, now() + interval '30 days', ?)",
        (store.utcnow(),),
    )
    return 1


@pytest_asyncio.fixture
async def channel(store, owner):
    # General pipeline tests must not depend on the wall clock. Window-policy
    # tests supply their own hours explicitly.
    channel_id = await store.insert(
        "INSERT INTO channels (owner_id, chat_id, username, title, window_start, window_end, created_at) "
        "VALUES (?, -100, 'chan', 'Канал', 0, 24, ?)",
        (owner, store.utcnow()),
    )
    return await store.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,))
