import asyncio
import re
from typing import Optional

import feedparser
from selectolax.parser import HTMLParser

from app.sources.telegram_web import RawItem

BR = re.compile(r"<br\s*/?>|</p>", re.I)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _clean(html: str) -> str:
    return HTMLParser(BR.sub("\n", html or "")).text(strip=False).strip()


def _media_from_entry(entry) -> list[dict]:
    media: list[dict] = []
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and (link.get("type") or "").startswith("image"):
            media.append({"type": "photo", "url": link["href"]})
    for thumb in entry.get("media_thumbnail", []) or []:
        if thumb.get("url"):
            media.append({"type": "photo", "url": thumb["url"]})
    for content in entry.get("media_content", []) or []:
        if content.get("url", "").lower().endswith(IMG_EXT):
            media.append({"type": "photo", "url": content["url"]})
    if not media:
        summary = entry.get("summary", "") or ""
        img = HTMLParser(summary).css_first("img")
        if img and img.attributes.get("src"):
            media.append({"type": "photo", "url": img.attributes["src"]})
    seen, unique = set(), []
    for item in media:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique[:1]


def _parse(url: str) -> tuple[list[RawItem], Optional[str]]:
    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        raise ValueError(f"не удалось прочитать ленту: {feed.bozo_exception}")
    title = (feed.feed.get("title") or "").strip() or None

    items: list[RawItem] = []
    for entry in feed.entries[:30]:
        headline = (entry.get("title") or "").strip()
        body = _clean(entry.get("summary") or entry.get("description") or "")
        text = f"{headline}\n\n{body}".strip() if headline else body
        if not text:
            continue
        items.append(
            RawItem(
                uid=entry.get("id") or entry.get("link") or headline,
                url=entry.get("link") or url,
                text=text,
                media=_media_from_entry(entry),
                date=entry.get("published") or entry.get("updated"),
                source_title=title,
            )
        )
    items.reverse()
    return items, title


async def fetch(url: str) -> tuple[list[RawItem], Optional[str]]:
    return await asyncio.to_thread(_parse, url)
