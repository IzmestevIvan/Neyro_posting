"""One-shot import of the pre-Postgres SQLite database.

    python -m scripts.sqlite_to_pg /path/to/neyro.db

Safe to re-run: every row is inserted with ON CONFLICT DO NOTHING, and the script refuses
to touch a table that already has rows unless --force is given.
"""

import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from app import db

TIMESTAMPS = {"created_at", "published_at", "publish_at", "checked_at", "used_at", "last_seen_at"}
DATES = {"day", "digest_sent_on"}


def parse_ts(value):
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parse_date(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def convert(row: sqlite3.Row, columns: set[str]) -> dict:
    out = {}
    for key in row.keys():
        if key not in columns:
            continue
        value = row[key]
        if key in TIMESTAMPS:
            value = parse_ts(value)
        elif key in DATES:
            value = parse_date(value)
        out[key] = value
    return out


async def target_columns(table: str) -> set[str]:
    rows = await db.fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,)
    )
    return {r["column_name"] for r in rows}


async def copy_table(source: sqlite3.Connection, table: str, force: bool) -> int:
    columns = await target_columns(table)
    if not columns:
        print(f"  {table}: нет такой таблицы в Postgres, пропускаю")
        return 0

    existing = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
    if existing["n"] and not force:
        print(f"  {table}: уже есть {existing['n']} строк, пропускаю (--force чтобы дописать)")
        return 0

    try:
        rows = source.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError:
        print(f"  {table}: нет в исходной базе, пропускаю")
        return 0

    copied = 0
    for row in rows:
        payload = convert(row, columns)
        if not payload:
            continue
        names = ", ".join(payload)
        holders = ", ".join("?" * len(payload))
        await db.execute(
            f"INSERT INTO {table} ({names}) VALUES ({holders}) ON CONFLICT DO NOTHING",
            tuple(payload.values()),
        )
        copied += 1
    print(f"  {table}: перенесено {copied}")
    return copied


async def fix_sequences() -> None:
    for table in ("channels", "sources", "posts", "ad_offers"):
        await db.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )


async def main(path: Path, force: bool) -> None:
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row

    await db.connect()
    print(f"Импорт из {path}")
    for table in ("users", "channels", "sources", "posts", "stats_daily", "kv"):
        await copy_table(source, table, force)
    await fix_sequences()
    print("Готово. Счётчики id выставлены по максимальным значениям.")
    await db.close()
    source.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("укажите путь к файлу SQLite")
    asyncio.run(main(Path(args[0]), "--force" in sys.argv))
