import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

import httpx
from selectolax.parser import HTMLParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
BG_URL = re.compile(r"background-image\s*:\s*url\('([^']+)'\)")
BR = re.compile(r"<br\s*/?>", re.I)


@dataclass
class RawItem:
    uid: str
    url: str
    text: str
    media: list[dict] = field(default_factory=list)
    views: int = 0
    date: Optional[str] = None
    source_title: Optional[str] = None


def parse_views(raw: str) -> int:
    raw = raw.strip().upper().replace(",", ".")
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", raw)
    if not m:
        return 0
    value = float(m.group(1))
    return int(value * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2)])


def _node_text(node) -> str:
    if node is None:
        return ""
    return HTMLParser(BR.sub("\n", node.html or "")).text(strip=False).strip()


def _extract_media(msg) -> list[dict]:
    media: list[dict] = []
    for photo in msg.css("a.tgme_widget_message_photo_wrap"):
        m = BG_URL.search(photo.attributes.get("style", "") or "")
        if m:
            media.append({"type": "photo", "url": m.group(1)})
    for video in msg.css("video.tgme_widget_message_video"):
        src = video.attributes.get("src")
        if src:
            media.append({"type": "video", "url": src})
    if not media:
        for thumb in msg.css("i.tgme_widget_message_video_thumb, i.tgme_widget_message_roundvideo_thumb"):
            m = BG_URL.search(thumb.attributes.get("style", "") or "")
            if m:
                media.append({"type": "photo", "url": m.group(1)})
    seen, unique = set(), []
    for item in media:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


def parse_channel_page(html: str) -> tuple[list[RawItem], Optional[str]]:
    tree = HTMLParser(html)
    title_node = tree.css_first("div.tgme_channel_info_header_title")
    channel_title = title_node.text(strip=True) if title_node else None

    items: list[RawItem] = []
    for msg in tree.css("div.tgme_widget_message"):
        post = msg.attributes.get("data-post")
        if not post:
            continue
        if msg.css_first("div.tgme_widget_message_forwarded_from"):
            continue

        text = _node_text(msg.css_first("div.tgme_widget_message_text"))
        media = _extract_media(msg)
        if not text and not media:
            continue

        views_node = msg.css_first("span.tgme_widget_message_views")
        time_node = msg.css_first("span.tgme_widget_message_meta time, time.time")

        items.append(
            RawItem(
                uid=post,
                url=f"https://t.me/{post}",
                text=text,
                media=media,
                views=parse_views(views_node.text()) if views_node else 0,
                date=time_node.attributes.get("datetime") if time_node else None,
                source_title=channel_title,
            )
        )
    return items, channel_title


TME_PREFIX = re.compile(r"^(?:https?://)?(?:www\.)?t\.me/(?:s/)?", re.I)


def normalize_ref(ref: str) -> str:
    # People paste "t.me/name" without a protocol just as often as the full link.
    ref = TME_PREFIX.sub("", ref.strip())
    ref = ref.split("/")[0].split("?")[0]
    return ref.lstrip("@")


async def fetch(client: httpx.AsyncClient, ref: str) -> tuple[list[RawItem], Optional[str]]:
    username = normalize_ref(ref)
    resp = await client.get(
        f"https://t.me/s/{username}",
        headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"},
        follow_redirects=True,
        timeout=25,
    )
    resp.raise_for_status()
    items, title = parse_channel_page(resp.text)
    if not items and "tgme_channel_info" not in resp.text:
        raise ValueError("канал не найден или закрыт предпросмотр")
    return items, title


POST_LINK = re.compile(r"^(?:https?://)?(?:www\.)?t\.me/(?:s/)?([A-Za-z0-9_]+)/(\d+)", re.I)


def parse_post_link(url: str) -> Optional[tuple[str, int]]:
    m = POST_LINK.match(url.strip())
    return (m.group(1), int(m.group(2))) if m else None


async def fetch_single(client: httpx.AsyncClient, username: str, post_id: int) -> Optional[RawItem]:
    resp = await client.get(
        f"https://t.me/{username}/{post_id}",
        params={"embed": "1"},
        headers={"User-Agent": UA},
        follow_redirects=True,
        timeout=25,
    )
    resp.raise_for_status()
    items, _ = parse_channel_page(resp.text)
    return items[0] if items else None


def median_views(items: list[RawItem]) -> int:
    values = [i.views for i in items if i.views > 0]
    return int(statistics.median(values)) if values else 0
