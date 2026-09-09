import asyncio
import io
import logging
from pathlib import Path
from typing import Optional

from app.sources.safe_http import fetch
from PIL import Image

log = logging.getLogger("watermark")

MARGIN_RATIO = 0.03
LOGO_WIDTH_RATIO = 0.20
OPACITY = 0.85


def _overlay(photo_bytes: bytes, logo_path: Path) -> bytes:
    base = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")

    target_width = max(48, int(base.width * LOGO_WIDTH_RATIO))
    ratio = target_width / logo.width
    logo = logo.resize((target_width, max(1, int(logo.height * ratio))), Image.LANCZOS)

    if OPACITY < 1:
        alpha = logo.getchannel("A").point(lambda p: int(p * OPACITY))
        logo.putalpha(alpha)

    margin = int(base.width * MARGIN_RATIO)
    position = (base.width - logo.width - margin, base.height - logo.height - margin)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(logo, position, logo)
    result = Image.alpha_composite(base, layer).convert("RGB")

    out = io.BytesIO()
    result.save(out, format="JPEG", quality=90)
    return out.getvalue()


async def apply(url: str, logo_path: Optional[str]) -> Optional[bytes]:
    if not logo_path or not Path(logo_path).exists():
        return None
    try:
        resp = await fetch(url, max_bytes=8 * 1024 * 1024,
                           allowed_types=("image/jpeg", "image/png", "image/webp", "image/gif"))
        return await asyncio.to_thread(_overlay, resp.content, Path(logo_path))
    except Exception as exc:
        log.warning("watermark failed (%s)", type(exc).__name__)
        return None
