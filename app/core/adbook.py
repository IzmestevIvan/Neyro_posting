"""Archive of posts rejected as advertising.

An ad that keeps showing up in your sources is a live advertiser already paying for reach
in your niche. Instead of dropping such posts silently, they are collected here with
whatever contact details they carry, so the channel owner can approach the buyer directly.
"""

import json
import re
from typing import Optional

from app import db
from app.core.filters import fingerprint, jaccard

# The lookbehind keeps the "@bank" inside "ads@bank.ru" from being read as a Telegram handle.
MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9_]{4,32})")
TME = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_+]{4,40})", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
PHONE = re.compile(r"\+?\d[\d\s()-]{9,17}\d")
COMPANY = re.compile(r"(?:ООО|ИП|АО|ЗАО)\s+[«\"']?([^»\"'\n,.]{2,60})")

SIMILAR = 0.5
KEEP_TEXT = 2000


def extract_contacts(text: str) -> list[str]:
    found: list[str] = []
    for match in TME.findall(text):
        found.append(f"t.me/{match}")
    for match in MENTION.findall(text):
        if f"t.me/{match}" not in found:
            found.append(f"@{match}")
    found += EMAIL.findall(text)
    found += [p.strip() for p in PHONE.findall(text)]

    unique, seen = [], set()
    for item in found:
        key = item.lower().lstrip("@").replace("t.me/", "")
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:6]


def extract_advertiser(text: str) -> Optional[str]:
    company = COMPANY.search(text)
    if company:
        return company.group(1).strip()
    mention = MENTION.search(text) or TME.search(text)
    return f"@{mention.group(1)}" if mention else None


async def record(post: dict, score: int, reasons: list[str]) -> Optional[int]:
    """Store a rejected ad, or bump the counter if the same offer was already seen."""
    text = (post.get("raw_text") or "").strip()
    if not text:
        return None

    prints = fingerprint(text)
    mine = set(prints.split())
    known = await db.fetch_all(
        "SELECT id, fingerprint FROM ad_offers WHERE channel_id = ? ORDER BY id DESC LIMIT 200",
        (post["channel_id"],),
    )
    for row in known:
        if row["fingerprint"] and jaccard(mine, set(row["fingerprint"].split())) >= SIMILAR:
            await db.execute(
                "UPDATE ad_offers SET seen_count = seen_count + 1, last_seen_at = ? WHERE id = ?",
                (db.utcnow(), row["id"]),
            )
            return row["id"]

    now = db.utcnow()
    return await db.insert(
        "INSERT INTO ad_offers (channel_id, source_id, source_title, url, advertiser, contacts, "
        "raw_text, score, reasons, fingerprint, created_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            post["channel_id"],
            post.get("source_id"),
            post.get("source_title"),
            post.get("url"),
            extract_advertiser(text),
            json.dumps(extract_contacts(text), ensure_ascii=False),
            text[:KEEP_TEXT],
            score,
            json.dumps(reasons, ensure_ascii=False),
            prints,
            now,
            now,
        ),
    )
