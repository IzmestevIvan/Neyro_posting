"""Company-owned images only; bounded, re-encoded snapshots for approval."""
import base64
import hashlib
import io

from PIL import Image, ImageOps

from app.sources import safe_http

MAX_UPLOAD = 4 * 1024 * 1024


def revision(media):
    import json
    if isinstance(media, str):
        media = json.loads(media or '[]')
    return hashlib.sha256(json.dumps(media or [], sort_keys=True).encode()).hexdigest()


def normalize(content):
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError('Фото: максимум 4 МБ')
    try:
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in ('JPEG', 'PNG', 'WEBP') or original.width * original.height > 16_000_000:
                raise ValueError('Нужен JPEG, PNG или WebP до 16 миллионов пикселей')
            if min(original.size) < 200 or max(original.size) / min(original.size) > 5:
                raise ValueError('Фото слишком маленькое или узкое')
            picture = ImageOps.exif_transpose(original).convert('RGB')
            picture.thumbnail((1280, 1280))
            for quality in (85, 70, 55, 40):
                output = io.BytesIO()
                picture.save(output, format='JPEG', quality=quality)
                if output.tell() <= 256 * 1024:
                    return {'type': 'photo', 'data': base64.b64encode(output.getvalue()).decode(),
                            'origin': 'upload'}
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Не удалось прочитать фотографию') from exc
    raise ValueError('Фото слишком сложное: уменьшите его размер')


async def download(url, source):
    import asyncio
    response = await safe_http.fetch(url, max_bytes=MAX_UPLOAD, total_timeout=15,
        allowed_types=('image/jpeg', 'image/png', 'image/webp'))
    photo = await asyncio.to_thread(normalize, response.content)
    photo.update(origin='company_page', source=source)
    return photo


def company_page(url, website):
    """Do not treat search results / arbitrary third-party pages as owned assets."""
    try:
        target, official = safe_http.validate_url(url), safe_http.validate_url(website)
        return (target.host.removeprefix('www.') == official.host.removeprefix('www.')
                and target.path not in ('', '/'))
    except (ValueError, TypeError):
        return False
