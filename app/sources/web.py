import httpx
import json
from urllib.parse import urljoin
from selectolax.parser import HTMLParser

from app.sources.telegram_web import RawItem
from app.sources.safe_http import fetch

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
    resp = await fetch(url, allowed_types=("text/html", "application/xhtml+xml", "text/plain"))
    resp.raise_for_status()
    tree = HTMLParser(resp.text)

    # Inertia exposes the same public project cards the browser renders. Read
    # only this known collection, never auth props or executable scripts.
    projects = []
    node = tree.css_first('[data-page]')
    if node:
        try:
            props = json.loads(node.attributes['data-page']).get('props', {})
            entries = props.get('projects', [])
            if isinstance(entries, list):
                for entry in entries[:60]:
                    if not isinstance(entry, dict) or not isinstance(entry.get('title'), str):
                        continue
                    title = entry['title'][:300]
                    description = HTMLParser(str(entry.get('description') or '')).text()[:1500]
                    images = entry.get('images', [])
                    image = next((i['src'] for i in images if isinstance(i, dict) and isinstance(i.get('src'), str)), '') if isinstance(images, list) else ''
                    projects.append({'title': title, 'description': description, 'image': urljoin(str(resp.url), image) if image else ''})
        except (ValueError, TypeError, AttributeError):
            pass

    title = _meta(tree, "og:title") or (tree.css_first("title").text(strip=True) if tree.css_first("title") else "")
    description = _meta(tree, "og:description")
    body = _body_text(tree)
    if projects:
        body = '\n\n'.join(p['title'] + ': ' + p['description'] for p in projects)
    text = "\n\n".join(part for part in (title, description, body) if part).strip()
    if len(text) < 40:
        raise ValueError("не удалось извлечь текст со страницы")

    image = urljoin(str(resp.url), _meta(tree, "og:image")) if _meta(tree, "og:image") else ''
    site = _meta(tree, "og:site_name") or httpx.URL(url).host

    return RawItem(
        uid=url,
        url=str(resp.url),
        text=text[:8000],
        media=([{'type':'photo', 'url':p['image'], 'project_title':p['title']} for p in projects if p['image'].startswith('http')]
               if projects else [{"type": "photo", "url": image}] if image.startswith("http") else []),
        source_title=site,
    )
