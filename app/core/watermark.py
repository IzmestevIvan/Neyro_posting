import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.sources.safe_http import fetch
from PIL import Image
from PIL import ImageOps

log = logging.getLogger("watermark")

MARGIN_RATIO = 0.03
LOGO_WIDTH_RATIO = 0.20
OPACITY = 0.85
POSITIONS = {'top-left', 'top-right', 'center', 'bottom-left', 'bottom-right'}


def save_logo(content: bytes, path: Path) -> None:
    if len(content) > 4 * 1024 * 1024:
        raise ValueError('Логотип должен быть не больше 4 МБ')
    try:
        original = Image.open(io.BytesIO(content))
    except Image.DecompressionBombError as exc:
        raise ValueError('Слишком большое изображение') from exc
    with original:
        if original.format != 'PNG' or original.width * original.height > 4_000_000:
            raise ValueError('Нужен PNG размером до 4 миллионов пикселей')
        logo = original.convert('RGBA')
        box = logo.getchannel('A').getbbox()
        if box is None:
            raise ValueError('Логотип полностью прозрачный')
        logo = logo.crop(box)
        logo.thumbnail((512, 512), Image.Resampling.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as tmp:
                temporary = Path(tmp.name)
                logo.save(tmp, 'PNG')
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _overlay(photo_bytes: bytes, logo_path: Path, placement: str = 'bottom-right') -> bytes:
    base = Image.open(io.BytesIO(photo_bytes))
    logo = Image.open(logo_path)
    if base.width * base.height > 12_000_000 or logo.width * logo.height > 4_000_000:
        raise ValueError('Изображение слишком большое для обработки')
    base = ImageOps.exif_transpose(base)
    base.thumbnail((1920,1920))
    logo.thumbnail((512,512))
    base, logo = base.convert('RGBA'), logo.convert('RGBA')

    target_width = max(1, int(base.width * LOGO_WIDTH_RATIO))
    ratio = min(target_width / logo.width, max(1, base.height * 0.25) / logo.height)
    target_width = max(1, int(logo.width * ratio))
    logo = logo.resize((target_width, max(1, int(logo.height * ratio))), Image.LANCZOS)

    if OPACITY < 1:
        alpha = logo.getchannel("A").point(lambda p: int(p * OPACITY))
        logo.putalpha(alpha)

    margin = int(min(base.size) * MARGIN_RATIO)
    positions = {
        'top-left': (margin, margin),
        'top-right': (base.width-logo.width-margin, margin),
        'bottom-left': (margin, base.height-logo.height-margin),
        'bottom-right': (base.width-logo.width-margin, base.height-logo.height-margin),
        'center': ((base.width-logo.width)//2, (base.height-logo.height)//2),
    }
    position = positions.get(placement, positions['bottom-right'])

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(logo, position)
    result = Image.alpha_composite(base, layer).convert("RGB")

    out = io.BytesIO()
    result.save(out, format="JPEG", quality=90)
    return out.getvalue()


async def apply(url: str, logo_path: Optional[str], placement: str = 'bottom-right') -> Optional[bytes]:
    if not logo_path or not Path(logo_path).exists():
        return None
    try:
        resp = await fetch(url, max_bytes=8 * 1024 * 1024,
                           allowed_types=("image/jpeg", "image/png", "image/webp", "image/gif"))
        return await asyncio.to_thread(_overlay, resp.content, Path(logo_path), placement)
    except Exception as exc:
        log.warning("watermark failed (%s)", type(exc).__name__)
        return None
