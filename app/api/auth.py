import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException

from app import db
from app.config import ADMIN_IDS, BOT_TOKEN, DEV_AUTH

MAX_AGE = 24 * 3600


def verify_init_data(init_data: str) -> dict:
    if not BOT_TOKEN:
        raise HTTPException(503, "сервер не настроен")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise HTTPException(401, "нет подписи")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(401, "подпись не совпадает")

    try:
        issued = int(pairs.get("auth_date", 0))
    except ValueError as exc:
        raise HTTPException(401, "испорченная метка времени") from exc
    if time.time() - issued > MAX_AGE:
        raise HTTPException(401, "сессия устарела")

    try:
        return json.loads(pairs["user"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(401, "нет данных пользователя") from exc


async def current_user(x_init_data: str = Header(default="")) -> dict:
    if DEV_AUTH and x_init_data.startswith("dev:"):
        tg_id = int(x_init_data.split(":", 1)[1])
        payload = {"id": tg_id, "first_name": "dev", "username": "dev"}
    else:
        payload = verify_init_data(x_init_data)

    tg_id = payload["id"]
    user = await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    if not user:
        await db.execute(
            "INSERT INTO users (tg_id, username, first_name, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
            (tg_id, payload.get("username"), payload.get("first_name"), int(tg_id in ADMIN_IDS), db.utcnow()),
        )
        user = await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    if user["blocked"]:
        raise HTTPException(403, "доступ заблокирован")
    return user


def is_admin(user: dict) -> bool:
    return bool(user["is_admin"]) or user["tg_id"] in ADMIN_IDS


def has_access(user: dict) -> bool:
    """Panel is invite-only: access comes from redeeming a promo code, admins always in."""
    if user.get("blocked"):
        return False
    if is_admin(user):
        return True
    until = user.get("access_until")
    return bool(until and until > datetime.now(timezone.utc))


async def active_user(user: dict = Depends(current_user)) -> dict:
    if not has_access(user):
        raise HTTPException(402, "нужен промокод")
    return user


async def require_admin(user: dict) -> dict:
    if not is_admin(user):
        raise HTTPException(403, "только для администратора")
    return user


async def owned_channel(channel_id: int, user: dict) -> dict:
    channel = await db.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,))
    if not channel:
        raise HTTPException(404, "канал не найден")
    if channel["owner_id"] != user["tg_id"] and not is_admin(user):
        raise HTTPException(403, "чужой канал")
    return channel
