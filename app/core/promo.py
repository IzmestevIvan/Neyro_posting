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
}


class PromoError(RuntimeError):
    pass


def generate_code() -> str:
    block = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))  # noqa: E731
    return f"{block()}-{block()}"


def normalize(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())[:8]


def formatted(code: str) -> str:
    clean = normalize(code)
    return f"{clean[:4]}-{clean[4:]}" if len(clean) == 8 else clean


async def create_codes(
    count: int,
    plan: str = "pro",
    note: Optional[str] = None,
    overrides: Optional[dict] = None,
) -> list[str]:
    if plan not in PLANS:
        raise PromoError(f"неизвестный тариф: {plan}")
    settings = {**PLANS[plan], **(overrides or {})}

    codes = []
    for _ in range(max(1, min(count, 100))):
        code = generate_code()
        await db.execute(
            "INSERT INTO promo_codes (code, plan, daily_limit, max_channels, days, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                plan,
                settings["daily_limit"],
                settings["max_channels"],
                settings["days"],
                note,
                db.utcnow(),
            ),
        )
        codes.append(code)
    return codes


async def redeem(code: str, tg_id: int) -> dict:
    formatted_code = formatted(code)
    if not CODE_RE.match(formatted_code):
        raise PromoError("код выглядит неправильно — он вида ABCD-2345")

    row = await db.fetch_one("SELECT * FROM promo_codes WHERE code = ?", (formatted_code,))
    if not row:
        raise PromoError("такого кода нет")
    if row["used_by"] and row["used_by"] != tg_id:
        raise PromoError("код уже активирован другим пользователем")

    now = db.utcnow()
    user = await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    # Re-entering your own code extends from the current expiry instead of resetting it.
    current = user.get("access_until") if user else None
    base = current if current and current > now else now
    until = base + timedelta(days=row["days"])

    await db.execute(
        "UPDATE users SET daily_limit = ?, max_channels = ?, access_until = ?, promo_code = ? "
        "WHERE tg_id = ?",
        (row["daily_limit"], row["max_channels"], until, formatted_code, tg_id),
    )
    if not row["used_by"]:
        await db.execute(
            "UPDATE promo_codes SET used_by = ?, used_at = ? WHERE code = ?",
            (tg_id, now, formatted_code),
        )
    return {
        "plan": row["plan"],
        "daily_limit": row["daily_limit"],
        "max_channels": row["max_channels"],
        "access_until": until,
    }
