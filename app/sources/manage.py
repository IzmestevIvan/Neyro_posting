"""Owner-scoped, idempotent source changes shared by API and bot."""
from app import db


async def copy_sources(owner_id: int, target_id: int, origin_id: int, ids: list[int]) -> dict:
    if target_id == origin_id:
        raise ValueError("Выберите другой канал")
    if not ids or len(ids) > 500 or any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError("Выберите от 1 до 500 источников")
    ids = list(dict.fromkeys(ids))
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        channels = await conn.fetch(
            "SELECT id FROM channels WHERE id = ANY($1::bigint[]) AND owner_id=$2 ORDER BY id FOR UPDATE",
            [target_id, origin_id], owner_id,
        )
        if len(channels) != 2:
            raise PermissionError("Копирование доступно только между вашими каналами")
        rows = await conn.fetch("SELECT * FROM sources WHERE channel_id=$1 AND id=ANY($2::bigint[]) FOR SHARE", origin_id, ids)
        if len(rows) != len(ids):
            raise ValueError("Список источников изменился. Откройте его заново")
        added = 0
        for row in rows:
            result = await conn.fetchval(
                "INSERT INTO sources(channel_id,kind,ref,title,created_at) VALUES($1,$2,$3,$4,now()) "
                "ON CONFLICT(channel_id,kind,ref) DO NOTHING RETURNING id",
                target_id, row['kind'], row['ref'], row['title'],
            )
            added += result is not None
        return {"added": added, "skipped": len(ids) - added}


async def add_forward_source(owner_id: int, target_id: int, ref: str, title: str) -> bool:
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        owned = await conn.fetchval("SELECT id FROM channels WHERE id=$1 AND owner_id=$2 FOR UPDATE", target_id, owner_id)
        if not owned:
            raise PermissionError("Канал недоступен")
        return await conn.fetchval(
            "INSERT INTO sources(channel_id,kind,ref,title,created_at) VALUES($1,'tg',$2,$3,now()) "
            "ON CONFLICT(channel_id,kind,ref) DO NOTHING RETURNING id", target_id, ref, title,
        ) is not None
