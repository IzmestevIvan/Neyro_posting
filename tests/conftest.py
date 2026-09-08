import os

import pytest
import pytest_asyncio

TEST_URL = os.getenv("TEST_DATABASE_URL", "postgresql://neyro:neyro@localhost:5432/neyro_test")
os.environ["DATABASE_URL"] = TEST_URL

TABLES = ("posts", "ad_offers", "sources", "stats_daily", "channels", "promo_codes", "users", "kv")


@pytest_asyncio.fixture
async def store():
    """Empty database with the real schema. Every test starts from a clean slate."""
    from app import db

    db.DATABASE_URL = TEST_URL
    await db.close()
    pool = await db.connect()
    async with pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    yield db
    await db.close()


@pytest_asyncio.fixture
async def owner(store):
    await store.execute(
        "INSERT INTO users (tg_id, daily_limit, max_channels, created_at) VALUES (1, 3, 5, ?)",
        (store.utcnow(),),
    )
    return 1


@pytest_asyncio.fixture
async def channel(store, owner):
    channel_id = await store.insert(
        "INSERT INTO channels (owner_id, chat_id, username, title, created_at) "
        "VALUES (?, -100, 'chan', 'Канал', ?)",
        (owner, store.utcnow()),
    )
    return await store.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,))
