import pytest

from app import db


async def columns(pool, table: str) -> set[str]:
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1", table
    )
    return {r["column_name"] for r in rows}


@pytest.mark.asyncio
async def test_existing_table_gains_new_columns(store):
    """Reproduces the deploy hazard: the table exists, the new column does not."""
    pool = await store.connect()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE users DROP COLUMN IF EXISTS max_channels")
        await conn.execute("ALTER TABLE users DROP COLUMN IF EXISTS access_until")
        await conn.execute(
            "INSERT INTO users (tg_id, created_at) VALUES (777, now()) ON CONFLICT DO NOTHING"
        )

    assert "max_channels" not in await columns(pool, "users")

    async with pool.acquire() as conn:
        await db._apply_migrations(conn)

    after = await columns(pool, "users")
    assert "max_channels" in after
    assert "access_until" in after

    survivor = await store.fetch_one("SELECT tg_id, max_channels FROM users WHERE tg_id = 777")
    assert survivor["tg_id"] == 777
    assert survivor["max_channels"] == 1, "у старых строк должно появиться значение по умолчанию"


@pytest.mark.asyncio
async def test_migrations_are_idempotent(store):
    pool = await store.connect()
    for _ in range(3):
        async with pool.acquire() as conn:
            await db._apply_migrations(conn)
    assert "promo_code" in await columns(pool, "users")


@pytest.mark.asyncio
async def test_placeholder_translation_matches_argument_count(store):
    """`?` placeholders in call sites become $1..$n; a mismatch fails loudly."""
    assert db._convert("SELECT ? , ? , ?") == "SELECT $1 , $2 , $3"
    row = await store.fetch_one("SELECT ?::int AS a, ?::text AS b", (5, "текст"))
    assert row == {"a": 5, "b": "текст"}


@pytest.mark.asyncio
async def test_update_reports_affected_rows(store, channel):
    changed = await store.update(
        "UPDATE channels SET title = ? WHERE id = ?", ("Новое имя", channel["id"])
    )
    assert changed == 1
    missing = await store.update("UPDATE channels SET title = ? WHERE id = ?", ("x", 999999))
    assert missing == 0


@pytest.mark.asyncio
async def test_kv_roundtrip(store):
    await store.set_kv("проверка", {"число": 5, "строка": "да"})
    assert await store.get_kv("проверка") == {"число": 5, "строка": "да"}
    assert await store.get_kv("нет-такого", "по умолчанию") == "по умолчанию"
