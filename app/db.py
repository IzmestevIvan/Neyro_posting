import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import asyncpg

from app.config import DATABASE_URL, DEFAULT_DAILY_LIMIT

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  tg_id       BIGINT PRIMARY KEY,
  username    TEXT,
  first_name  TEXT,
  is_admin    INTEGER NOT NULL DEFAULT 0,
  blocked     INTEGER NOT NULL DEFAULT 0,
  daily_limit INTEGER NOT NULL DEFAULT %(limit)s,
  max_channels INTEGER NOT NULL DEFAULT 1,
  access_until TIMESTAMPTZ,
  promo_code  TEXT,
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_codes (
  code         TEXT PRIMARY KEY,
  plan         TEXT NOT NULL DEFAULT 'base',
  daily_limit  INTEGER NOT NULL DEFAULT %(limit)s,
  max_channels INTEGER NOT NULL DEFAULT 1,
  days         INTEGER NOT NULL DEFAULT 30,
  note         TEXT,
  used_by      BIGINT,
  used_at      TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS channels (
  id             BIGSERIAL PRIMARY KEY,
  owner_id       BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
  chat_id        BIGINT,
  username       TEXT,
  title          TEXT,
  autopost       INTEGER NOT NULL DEFAULT 0,
  paused         INTEGER NOT NULL DEFAULT 0,
  delay_mode     TEXT    NOT NULL DEFAULT 'instant',
  pace           TEXT    NOT NULL DEFAULT 'as_they_come',
  tz             TEXT    NOT NULL DEFAULT 'Europe/Moscow',
  window_start   INTEGER NOT NULL DEFAULT 8,
  window_end     INTEGER NOT NULL DEFAULT 23,
  quality        TEXT    NOT NULL DEFAULT 'super',
  instructions   TEXT    NOT NULL DEFAULT '',
  stopwords      TEXT    NOT NULL DEFAULT '',
  lang           TEXT    NOT NULL DEFAULT 'ru',
  channel_voice  INTEGER NOT NULL DEFAULT 0,
  hits_only      INTEGER NOT NULL DEFAULT 0,
  media_only     INTEGER NOT NULL DEFAULT 0,
  digest_enabled INTEGER NOT NULL DEFAULT 0,
  digest_time    TEXT    NOT NULL DEFAULT '21:00',
  digest_sent_on DATE,
  signature_text TEXT    NOT NULL DEFAULT '',
  signature_url  TEXT    NOT NULL DEFAULT '',
  watermark      INTEGER NOT NULL DEFAULT 0,
  logo_path      TEXT,
  gemini_key     TEXT,
  voice_sample   TEXT,
  created_at     TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channels_owner ON channels(owner_id);

CREATE TABLE IF NOT EXISTS sources (
  id           BIGSERIAL PRIMARY KEY,
  channel_id   BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,
  ref          TEXT NOT NULL,
  title        TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  median_views INTEGER NOT NULL DEFAULT 0,
  last_uid     TEXT,
  checked_at   TIMESTAMPTZ,
  error        TEXT,
  created_at   TIMESTAMPTZ NOT NULL,
  UNIQUE (channel_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_sources_channel ON sources(channel_id);

CREATE TABLE IF NOT EXISTS posts (
  id             BIGSERIAL PRIMARY KEY,
  channel_id     BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  source_id      BIGINT,
  uid            TEXT,
  url            TEXT,
  source_title   TEXT,
  raw_text       TEXT,
  media          TEXT NOT NULL DEFAULT '[]',
  views          INTEGER NOT NULL DEFAULT 0,
  fingerprint    TEXT,
  status         TEXT NOT NULL,
  reason         TEXT,
  text_out       TEXT,
  fact_check     TEXT,
  publish_at     TIMESTAMPTZ,
  message_id     BIGINT,
  mod_message_id BIGINT,
  attempts       INTEGER NOT NULL DEFAULT 0,
  is_manual      INTEGER NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL,
  published_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_posts_channel_status ON posts(channel_id, status);
CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(channel_id, uid);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);

CREATE TABLE IF NOT EXISTS delivery_attempts (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT REFERENCES posts(id) ON DELETE SET NULL,
  channel_id BIGINT REFERENCES channels(id) ON DELETE SET NULL,
  owner_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
  worker_id TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'sending',
  receipts JSONB NOT NULL DEFAULT '[]',
  error_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_delivery_post ON delivery_attempts(post_id);
CREATE INDEX IF NOT EXISTS idx_delivery_owner_time ON delivery_attempts(owner_id, started_at);

CREATE TABLE IF NOT EXISTS ad_offers (
  id           BIGSERIAL PRIMARY KEY,
  channel_id   BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  source_id    BIGINT,
  source_title TEXT,
  url          TEXT,
  advertiser   TEXT,
  contacts     TEXT,
  raw_text     TEXT,
  score        INTEGER NOT NULL DEFAULT 0,
  reasons      TEXT,
  status       TEXT NOT NULL DEFAULT 'new',
  seen_count   INTEGER NOT NULL DEFAULT 1,
  fingerprint  TEXT,
  created_at   TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ads_channel ON ad_offers(channel_id, status);

CREATE TABLE IF NOT EXISTS stats_daily (
  channel_id  BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  day         DATE NOT NULL,
  published   INTEGER NOT NULL DEFAULT 0,
  filtered    INTEGER NOT NULL DEFAULT 0,
  duplicates  INTEGER NOT NULL DEFAULT 0,
  ai_requests INTEGER NOT NULL DEFAULT 0,
  subscribers INTEGER NOT NULL DEFAULT 0,
  avg_views   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (channel_id, day)
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v JSONB NOT NULL
);
""" % {"limit": DEFAULT_DAILY_LIMIT}

# Columns added after the first Postgres release. CREATE TABLE IF NOT EXISTS never alters an
# existing table, so anything new has to be listed here as well as in SCHEMA above.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("users", "max_channels", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "access_until", "TIMESTAMPTZ"),
    ("users", "promo_code", "TEXT"),
]

_pool: Optional[asyncpg.Pool] = None
_PLACEHOLDER = re.compile(r"\?")


def _convert(sql: str) -> str:
    """Call sites are written with `?` placeholders; asyncpg wants $1, $2, …"""
    counter = 0

    def replace(_match: re.Match) -> str:
        nonlocal counter
        counter += 1
        return f"${counter}"

    return _PLACEHOLDER.sub(replace, sql)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8, command_timeout=30)
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA)
            await _apply_migrations(conn)
    return _pool


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    for table, column, declaration in MIGRATIONS:
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = $1 AND column_name = $2",
            table,
            column,
        )
        if not exists:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            log.info("миграция: добавлена колонка %s.%s", table, column)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    pool = await connect()
    rows = await pool.fetch(_convert(sql), *params)
    return [dict(r) for r in rows]


async def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    pool = await connect()
    row = await pool.fetchrow(_convert(sql), *params)
    return dict(row) if row else None


async def execute(sql: str, params: Iterable[Any] = ()) -> None:
    pool = await connect()
    await pool.execute(_convert(sql), *params)


async def insert(sql: str, params: Iterable[Any] = ()) -> Any:
    """INSERT that returns the new id. Not every table has an `id` column (users, kv,
    stats_daily are keyed differently), so this is deliberately separate from execute()."""
    pool = await connect()
    statement = _convert(sql)
    if "RETURNING" not in statement.upper():
        statement += " RETURNING id"
    return await pool.fetchval(statement, *params)


async def update(sql: str, params: Iterable[Any] = ()) -> int:
    """Like execute(), but returns how many rows changed — used to claim a row exactly once."""
    pool = await connect()
    status = await pool.execute(_convert(sql), *params)
    return int(status.rsplit(" ", 1)[-1]) if status else 0


async def get_kv(key: str, default: Any = None) -> Any:
    pool = await connect()
    value = await pool.fetchval("SELECT v FROM kv WHERE k = $1", key)
    if value is None:
        return default
    return json.loads(value)


async def set_kv(key: str, value: Any) -> None:
    pool = await connect()
    await pool.execute(
        "INSERT INTO kv (k, v) VALUES ($1, $2::jsonb) ON CONFLICT (k) DO UPDATE SET v = excluded.v",
        key,
        json.dumps(value, default=str),
    )


async def bump_stat(channel_id: int, day, field: str, delta: int = 1) -> None:
    if field not in {"published", "filtered", "duplicates", "ai_requests"}:
        raise ValueError(f"unknown stat field: {field}")
    pool = await connect()
    await pool.execute(
        f"INSERT INTO stats_daily (channel_id, day, {field}) VALUES ($1, $2, $3) "
        f"ON CONFLICT (channel_id, day) DO UPDATE SET {field} = stats_daily.{field} + excluded.{field}",
        channel_id,
        day,
        delta,
    )
