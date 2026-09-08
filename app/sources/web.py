import httpx
from selectolax.parser import HTMLParser

from app.sources.telegram_web import UA, RawItem

NOISE = "script, style, nav, header, footer, aside, form, noscript"


def _meta(tree: HTMLParser, prop: str) -> str:
    node = tree.css_first(f'meta[property="{prop}"], meta[name="{prop}"]')
    return (node.attributes.get("content") or "").strip() if node else ""


def _body_text(tree: HTMLParser) -> str:
    for node in tree.css(NOISE):
        node.decompose()
    container = tree.css_first("article") or tree.css_first("main") or tree.body
    if container is None:
        return ""
    paragraphs = [p.text(strip=True) for p in container.css("p")]
    meaningful = [p for p in paragraphs if len(p) > 60]
    return "\n\n".join(meaningful[:12]) if meaningful else container.text(strip=True)[:4000]


async def fetch_article(client: httpx.AsyncClient, url: str) -> RawItem:
    resp = await client.get(
        url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30
    )
    resp.raise_for_status()
    tree = HTMLParser(resp.text)

    title = _meta(tree, "og:title") or (tree.css_first("title").text(strip=True) if tree.css_first("title") else "")
    description = _meta(tree, "og:description")
    body = _body_text(tree)
    text = "\n\n".join(part for part in (title, description, body) if part).strip()
    if len(text) < 40:
        raise ValueError("не удалось извлечь текст со страницы")

    image = _meta(tree, "og:image")
    site = _meta(tree, "og:site_name") or httpx.URL(url).host

    return RawItem(
        uid=url,
        url=url,
        text=text[:8000],
        media=[{"type": "photo", "url": image}] if image.startswith("http") else [],
        source_title=site,
    )
