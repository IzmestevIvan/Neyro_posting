import re
import secrets
from datetime import timedelta
from typing import Optional

from app import db

# Ambiguous characters are left out so codes survive being read aloud or retyped.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")

PLANS = {
    "start": {"daily_limit": 100, "max_channels": 1, "days": 30},
    "pro": {"daily_limit": 500, "max_channels": 3, "days": 30},
    "unlim": {"daily_limit": 2000, "max_channels": 10, "days": 365},
    "custom": {"daily_limit": 100, "max_channels": 1, "days": 30},
}


class PromoError(RuntimeError):
    pass


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise PromoError(f'{label}: целое число от {low} до {high}')
    return value


def generate_code() -> str:
    block = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))  # noqa: E731
    return f"{block()}-{block()}"


def normalize(code: str) -> str:
    return re.sub(r"[\s-]", "", (code or "").upper())


def formatted(code: str) -> str:
    clean = normalize(code)
    return f"{clean[:4]}-{clean[4:]}" if len(clean) == 8 else clean


async def create_codes(
    count: int,
    plan: str = "pro",
    note: Optional[str] = None,
    overrides: Optional[dict] = None,
    activation_days: Optional[int] = None,
    assigned_to: Optional[int] = None,
) -> list[str]:
    if plan not in PLANS:
        raise PromoError(f"неизвестный тариф: {plan}")
    integer(count, 1, 100, 'Количество кодов')
    if overrides is not None and (not isinstance(overrides, dict) or set(overrides)-{'daily_limit','max_channels','days'}):
        raise PromoError('Неизвестные настройки кода')
    settings = {**PLANS[plan], **(overrides or {})}
    integer(settings['daily_limit'], 0, 100000, 'Постов в день')
    integer(settings['max_channels'], 1, 120, 'Каналов')
    integer(settings['days'], 1, 3650, 'Дней доступа')
    if settings != PLANS[plan]:
        plan = 'custom'
    if note is not None and (not isinstance(note, str) or len(note)>500):
        raise PromoError('Комментарий: до 500 символов')
    expires = None if activation_days is None else db.utcnow()+timedelta(days=integer(activation_days,1,3650,'Срок активации'))
    if assigned_to is not None:
        integer(assigned_to, 1, 2**63-1, 'Telegram ID')

    codes = []
    for _ in range(max(1, min(count, 100))):
        code = generate_code()
        await db.execute(
            "INSERT INTO promo_codes (code, plan, daily_limit, max_channels, days, note, created_at, expires_at, assigned_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                plan,
                settings["daily_limit"],
                settings["max_channels"],
                settings["days"],
                note,
                db.utcnow(),
                expires, assigned_to,
            ),
        )
        codes.append(code)
    return codes


async def redeem(code: str, tg_id: int) -> dict:
    formatted_code = formatted(code)
    if not CODE_RE.match(formatted_code):
        raise PromoError("код выглядит неправильно — он вида ABCD-2345")

    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the recipient first: different new codes for one user must also
            # serialize so neither extension overwrites the other.
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE tg_id = $1 FOR UPDATE", tg_id
            )
            if not user or user["blocked"]:
                raise PromoError("пользователь недоступен")
            row = await conn.fetchrow(
                "SELECT * FROM promo_codes WHERE code = $1 FOR UPDATE", formatted_code
            )
            if not row:
                raise PromoError("такого кода нет")
            if row["used_by"] is not None and row["used_by"] != tg_id:
                raise PromoError("код уже активирован другим пользователем")
            if row["used_by"] is not None:
                # Replaying an old code cannot extend or downgrade current access.
                return {
                    "plan": row["plan"],
                    "daily_limit": user["daily_limit"],
                    "max_channels": user["max_channels"],
                    "access_until": user["access_until"],
                    "already_redeemed": True,
                }
            now = db.utcnow()
            if row['expires_at'] and row['expires_at'] <= now:
                raise PromoError('Срок активации кода истёк')
            if row['assigned_to'] is not None and row['assigned_to'] != tg_id:
                raise PromoError('Код предназначен другому клиенту')
            current = user["access_until"]
            until = max(current, now) + timedelta(days=row["days"]) if current else now + timedelta(days=row["days"])
            await conn.execute(
                "UPDATE users SET daily_limit=$1, max_channels=$2, access_until=$3, promo_code=$4, plan=$6 WHERE tg_id=$5",
                row["daily_limit"], row["max_channels"], until, formatted_code, tg_id, row['plan'],
            )
            await conn.execute(
                "UPDATE promo_codes SET used_by=$1, used_at=$2 WHERE code=$3",
                tg_id, now, formatted_code,
            )
            return {
                "plan": row["plan"], "daily_limit": row["daily_limit"],
                "max_channels": row["max_channels"], "access_until": until,
                "already_redeemed": False,
            }
