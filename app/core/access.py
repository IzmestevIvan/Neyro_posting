"""Shared authorization for bot, HTTP services, and background work."""
from app import db
from app.api.auth import has_access


async def owner_has_access(owner_id: int) -> bool:
    user = await db.fetch_one('SELECT * FROM users WHERE tg_id = ?', (owner_id,))
    return bool(user and not user['blocked'] and has_access(user))
